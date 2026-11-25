"""phoenix_order.py — Phoenix Channel WebSocket Order Executor for Binomo / Stockity.
Exact reverse-engineered protocol from Binomo/Stockity APK (Chaquopy broker_api.py).

Features:
- Background persistent WebSocket connection to wss://ws.{domain}/?v=2&vsn=2.0.0
- Auto-join channels: 'account', 'user', 'bo', 'asset:{pair}'
- Background Phoenix heartbeat (every 25s)
- Synchronous place_order() with Future/threading.Event for immediate response (<200ms)
- Accurate turbo expiry calculation (aligns with 60s candle close & 30s cutoff)
- Automatic reconnect & session recovery
"""

import asyncio
import json
import logging
import math
import ssl
import sys
import threading
import time
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import timezone
from typing import Optional

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger('PhoenixOrder')

def get_ssl_context():
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

DOMAINS = {
    'Binomo':   'binomo5.com',
    'Stockity': 'stockity1.id',
}

def calculate_turbo_expire_at(duration_seconds: int = 60) -> int:
    """Hitung expiry epoch timestamp sesuai aturan Turbo Binomo.
    Jika detik saat ini < 30s: expiry = candle menit saat ini (menit + 1).
    Jika detik saat ini >= 30s: expiry = candle menit berikutnya (menit + 2).
    """
    now = time.time()
    sec = int(now) % 60
    base_min = (int(now) // 60) * 60
    if sec < 30:
        return base_min + 60
    else:
        return base_min + 120


class PhoenixOrderClient:
    def __init__(self, broker: str = 'Binomo', authtoken: str = '', device_id: str = '', pair: str = 'Z-CRY/IDX'):
        self.broker = broker
        self.domain = DOMAINS.get(broker, 'binomo5.com')
        self.authtoken = authtoken
        self.device_id = device_id
        self.pair = pair

        self.ws_url = f"wss://ws.{self.domain}/?v=2&vsn=2.0.0"
        self.origin = f"https://{self.domain}"
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._joined = False
        self._joined_topics = set()
        self._join_refs = {}
        self._pending_replies = {}  # ref -> asyncio.Future
        self._ref = 1
        self._running = False
        self._lock = threading.Lock()
        self.status = "DISCONNECTED"
        self._time_offset_ms = 0
        self._last_time_sync = 0.0

    def _sync_server_time(self, force: bool = False) -> int:
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_time_sync < 60.0):
            return self._time_offset_ms
        try:
            api_domain = 'stockity.com' if self.broker == 'Stockity' else 'binomo5.com'
            url = f"https://api.{api_domain}/platform/private/v2/profile?locale=en"
            req = urllib.request.Request(url, headers={
                'authorization-token': self.authtoken,
                'device-id': str(self.device_id),
                'device-type': 'web',
                'User-Agent': self.user_agent,
            })
            t0 = time.time() * 1000
            with urllib.request.urlopen(req, context=get_ssl_context(), timeout=4) as resp:
                t1 = time.time() * 1000
                date_hdr = resp.headers.get("Date") or resp.headers.get("date")
                if date_hdr:
                    dt = parsedate_to_datetime(date_hdr)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    server_ms = dt.timestamp() * 1000
                    local_mid = (t0 + t1) / 2.0
                    self._time_offset_ms = int(server_ms - local_mid)
                    self._last_time_sync = now_mono
        except Exception:
            pass
        return self._time_offset_ms

    def update_credentials(self, authtoken: str, device_id: str, pair: Optional[str] = None):
        with self._lock:
            cred_changed = (self.authtoken != authtoken or self.device_id != device_id)
            self.authtoken = authtoken
            self.device_id = device_id
            if pair:
                old_pair = self.pair
                self.pair = pair
                if old_pair != pair and self._ws and self._loop and self._loop.is_running() and self._joined:
                    asyncio.run_coroutine_threadsafe(self._ensure_asset_joined(pair), self._loop)
        if cred_changed and self._running:
            self.reconnect()

    async def _ensure_asset_joined(self, pair: str):
        topic = f"asset:{pair}"
        if topic not in self._join_refs and self._ws:
            ref = self._next_ref()
            self._join_refs[topic] = ref
            join_msg = {
                "topic": topic,
                "event": "phx_join",
                "payload": {},
                "ref": ref,
                "join_ref": ref
            }
            try:
                await self._ws.send(json.dumps(join_msg))
            except Exception:
                pass

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="PhoenixOrderWS")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)

    def reconnect(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)

    def _next_ref(self) -> str:
        r = str(self._ref)
        self._ref += 1
        return r

    def _headers(self) -> dict:
        return {
            'User-Agent': self.user_agent,
            'Origin': self.origin,
            'device-type': 'web',
            'device-id': str(self.device_id),
            'Cookie': f"device_type=web; device_id={self.device_id}; authtoken={self.authtoken}"
        }

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main_task())

    async def _cleanup(self):
        self._joined = False
        self._join_refs.clear()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self.status = "DISCONNECTED"

    async def _main_task(self):
        while self._running:
            if not self.authtoken or not self.device_id:
                self.status = "NO_CREDENTIALS"
                await asyncio.sleep(2)
                continue

            try:
                self.status = "CONNECTING"
                headers = self._headers()
                connect_kwargs = {
                    'ssl': get_ssl_context(),
                    'ping_interval': None,
                    'close_timeout': 5
                }
                try:
                    connect_kwargs['extra_headers'] = headers
                    ws = await websockets.connect(self.ws_url, **connect_kwargs)
                except TypeError:
                    connect_kwargs.pop('extra_headers', None)
                    connect_kwargs['additional_headers'] = headers
                    ws = await websockets.connect(self.ws_url, **connect_kwargs)

                async with ws:
                    self._ws = ws
                    self.status = "CONNECTED"
                    self._joined = False
                    self._joined_topics.clear()
                    self._join_refs.clear()

                    # Join Phoenix channels
                    topics = ["account", "user", "bo", f"asset:{self.pair}"]
                    for t in topics:
                        ref = self._next_ref()
                        self._join_refs[t] = ref
                        join_msg = {
                            "topic": t,
                            "event": "phx_join",
                            "payload": {},
                            "ref": ref,
                            "join_ref": ref
                        }
                        await ws.send(json.dumps(join_msg))

                    # Start heartbeat task
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    try:
                        while self._running:
                            raw = await ws.recv()
                            msg = json.loads(raw)
                            event = msg.get("event")
                            ref = str(msg.get("ref") or "")
                            topic = msg.get("topic")

                            if event == "phx_reply":
                                payload = msg.get("payload", {})
                                if ref in self._join_refs.values():
                                    st = payload.get("status")
                                    if st == "ok" and topic:
                                        self._joined_topics.add(topic)
                                future = self._pending_replies.pop(ref, None)
                                if future and not future.done():
                                    future.set_result(msg)

                            if "bo" in self._joined_topics or len(self._joined_topics) >= 2:
                                self._joined = True
                    finally:
                        heartbeat_task.cancel()
            except Exception as e:
                self.status = f"ERROR: {e}"
                await self._cleanup()
                await asyncio.sleep(1)

    async def _heartbeat_loop(self):
        while self._running:
            await asyncio.sleep(25)
            if self._ws and self._joined:
                try:
                    ref = self._next_ref()
                    hb = {
                        "topic": "phoenix",
                        "event": "heartbeat",
                        "payload": {},
                        "ref": ref
                    }
                    await self._ws.send(json.dumps(hb))
                except Exception:
                    break

    def place_order(self, direction: str, amount: float, pair: Optional[str] = None, wallet: str = 'DEMO', duration_seconds: int = 60, timeout: float = 8.0) -> dict:
        """Kirim order ke broker melalui WebSocket Phoenix Channel secara sinkron."""
        if not self._running:
            self.start()

        # Tunggu hingga loop dan WebSocket siap (maks 8 detik)
        start_wait = time.time()
        while time.time() - start_wait < 8.0:
            if self._loop and self._loop.is_running() and self._ws and self._joined:
                break
            time.sleep(0.05)

        if not self._loop or not self._loop.is_running():
            return {'ok': False, 'error': 'Event loop order client belum running'}

        if not self._ws or not self._joined:
            return {'ok': False, 'error': f'WebSocket order belum siap ({self.status})'}

        p_name = pair or self.pair
        dir_str = "call" if str(direction).upper() in ("BUY", "CALL", "UP") else "put"
        acc_type = "demo" if str(wallet).strip().upper() == "DEMO" else "real"
        offset_ms = self._sync_server_time()
        now_ms = int(time.time() * 1000) + offset_ms
        # Hitung expire_at berdasarkan waktu server broker
        now_sec = now_ms / 1000.0
        sec = int(now_sec) % 60
        base_min = (int(now_sec) // 60) * 60
        exp = (base_min + 60) if sec < 30 else (base_min + 120)
        amount_subunits = int(round(float(amount) * 100))

        payload = {
            "amount":        amount_subunits,
            "created_at":    now_ms,
            "ric":           p_name,
            "deal_type":     acc_type,
            "expire_at":     exp,
            "option_type":   "turbo",
            "trend":         dir_str,
            "tournament_id": None,
            "is_state":      False
        }

        # Dispatch async call
        fut = asyncio.run_coroutine_threadsafe(
            self._async_place_order(payload),
            self._loop
        )

        try:
            res = fut.result(timeout=timeout)
            return res
        except Exception as e:
            return {'ok': False, 'error': f'Order timeout/gagal: {e}'}

    async def _async_place_order(self, payload: dict) -> dict:
        ref = self._next_ref()
        bo_join_ref = self._join_refs.get("bo") or self._join_refs.get("user") or ref

        order_msg = {
            "topic":    "bo",
            "event":    "create",
            "payload":  payload,
            "ref":      ref,
            "join_ref": str(bo_join_ref)
        }

        loop = asyncio.get_running_loop()
        reply_future = loop.create_future()
        self._pending_replies[ref] = reply_future

        try:
            await self._ws.send(json.dumps(order_msg))
            reply = await asyncio.wait_for(reply_future, timeout=6.0)
            payload_resp = reply.get("payload", {})
            status = str(payload_resp.get("status", "")).lower()

            if status in ("ok", "success"):
                response = payload_resp.get("response", {})
                uuid = response.get("uuid") or response.get("id") or ""
                return {
                    'ok': True,
                    'deal_id': uuid,
                    'data': {'id': uuid, 'uuid': uuid, 'response': response},
                    'expire_at': payload['expire_at']
                }
            else:
                err_text = json.dumps(payload_resp.get("response") or payload_resp)
                return {'ok': False, 'error': f"REJECTED: {err_text}"}

        except asyncio.TimeoutError:
            self._pending_replies.pop(ref, None)
            return {'ok': False, 'error': 'phx_reply TIMEOUT'}
        except Exception as e:
            self._pending_replies.pop(ref, None)
            return {'ok': False, 'error': str(e)}


# Singleton instance
_client: Optional[PhoenixOrderClient] = None

def get_order_client(broker: str = 'Binomo', authtoken: str = '', device_id: str = '', pair: str = 'Z-CRY/IDX') -> PhoenixOrderClient:
    global _client
    if _client is not None and _client.broker != broker:
        try:
            _client.stop()
        except Exception:
            pass
        _client = None

    if _client is None:
        _client = PhoenixOrderClient(broker=broker, authtoken=authtoken, device_id=device_id, pair=pair)
        _client.start()
    else:
        _client.update_credentials(authtoken=authtoken, device_id=device_id, pair=pair)
    return _client
