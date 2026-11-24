# uncompyle6 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)]
# Embedded file name: broker_api.py
# Compiled at: 2026-11-12 20:24:15
# Size of source mod 2**32: 75766 bytes
import asyncio, json, math, time, threading
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional
import requests, websockets
from session_login import open_browser_login_and_save, direct_login_and_save
from urllib.parse import quote

class BrokerAPI:
    __doc__ = '\n    Stockity/Binomo websocket connector.\n\n    V19 dual websocket price feed:\n    - Socket order Phoenix tetap memakai ws.stockity1.id.\n    - Socket harga terpisah memakai wss://as.stockity.com/.\n    - Subscribe dengan {"action":"subscribe","rics":[RIC]}.\n    - Parse data/assets/rate dan provider_time untuk FO exact tick.\n\n    V9 fix:\n    - WebSocket broker dibuat fresh per order agar order kedua tidak memakai event lama.\n    - Response order hanya dianggap valid dari phx_reply dengan ref order yang sama.\n    - Event opened lama yang broadcast setelah join tidak lagi membuat order palsu sukses.\n    - WebSocket ditutup setelah order accepted/reject untuk menghindari stale session.\n    '

    def __init__(self, api_config: dict, logger=None):
        self.cfg = api_config or {}
        self.logger = logger
        self.broker = self.cfg.get("broker", "stockity")
        self.domain = self.cfg.get("domains", {}).get(self.broker)
        self._closed_results = {}
        self._closed_events = []
        self._pending_refs = {}
        self._ref_order_meta = {}
        self._loop_thread = None
        self._listener_task = None
        self._heartbeat_task = None
        self._reconnecting = False
        self._last_heartbeat_ref = ""
        self._heartbeat_sent_at = {}
        if not self.domain:
            self.domain = "stockity.id" if self.broker == "stockity" else "binomo5.com"
        self.authtoken = self.cfg.get("authtoken", "")
        self.device_id = self.cfg.get("device_id", "")
        self.user_agent = self.cfg.get("user_agent", "Mozilla/5.0")
        self.timeout = int(self.cfg.get("timeout_seconds", 20))
        self.debug_ws = bool(self.cfg.get("debug_ws", True))
        self.base_api = f"https://api.{self.domain}"
        self.ws_url = f"wss://ws.{self.domain}/?v=2&vsn=2.0.0"
        self.web_url = f"https://{self.domain}"
        self.session = requests.Session()
        self.session.headers.update(self._rest_headers())
        self._ref = 1
        self._loop = None
        self._ws = None
        self._joined = False
        self._join_refs = {}
        self._last_message = None
        self._order_pending = False
        self._last_closed_uuid = ""
        self._opened_events = []
        self._active_order_uuid = ""
        self._active_order_payload = {}
        self._active_order_sent_ms = 0
        self._last_order_ws_ready_ms = 0
        self._last_ws_message_ms = 0
        self._server_time_offset_ms = 0
        self._last_time_sync_monotonic = 0.0
        self._latest_asset_price = {}
        self._latest_asset_price_ms = {}
        self._asset_tick_history = {}
        self._asset_tick_lock = threading.Lock()
        self._asset_debug_count = 0
        self._price_ws = None
        self._price_task = None
        self._price_stream_ric = str(self.cfg.get("asset_ric") or "Z-CRY/IDX")
        self.price_ws_url = str(self.cfg.get("price_ws_url") or ("wss://as.stockity.com/" if self.broker == "stockity" else "wss://as.binomo5.com/"))
        self._price_reconnect_count = 0
        self._last_provider_time_ms = 0
        self._last_provider_recv_ms = 0

    @staticmethod
    def _to_finite_number(value):
        """Konversi angka JSON/string ke float tanpa menerima bool atau data kosong."""
        if isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float)):
            number = float(value)
        else:
            if isinstance(value, str):
                text = value.strip().replace(",", ".")
                try:
                    number = float(text)
                except (TypeError, ValueError):
                    return

            else:
                return
        if math.isfinite(number):
            return number

    @staticmethod
    def _looks_like_timestamp(number):
        return number is not None and number >= 1000000000

    @staticmethod
    def _extract_asset_price(value):
        """Baca format tick Stockity: object, array, dan numeric-string secara terkontrol."""
        price_keys = ('price', 'rate', 'quote', 'close', 'value', 'last', 'last_price',
                      'lastPrice', 'current_price', 'currentPrice', 'asset_price',
                      'assetPrice')
        wrapper_keys = ('data', 'tick', 'ticks', 'quote', 'quotes', 'asset', 'assets',
                        'payload', 'result', 'candle', 'candles', 'items', 'updates',
                        'values')
        if isinstance(value, dict):
            for key in price_keys:
                if key in value:
                    numeric = BrokerAPI._to_finite_number(value.get(key))

            if numeric is not None:
                if 0 < numeric < 1000000000:
                    return numeric
                for key in wrapper_keys:
                    if key in value:
                        found = BrokerAPI._extract_asset_price(value.get(key))
                        if found is not None:
                            return found

                for candidate in value.values():
                    if isinstance(candidate, (dict, list, tuple)):
                        found = BrokerAPI._extract_asset_price(candidate)
                        if found is not None:
                            return found
                        return

        if isinstance(value, (list, tuple)):
            for candidate in reversed(value):
                if isinstance(candidate, (dict, list, tuple)):
                    found = BrokerAPI._extract_asset_price(candidate)
                    if found is not None:
                        return found
                    numbers = []
                    for candidate in value:
                        number = BrokerAPI._to_finite_number(candidate)
                        if number is not None:
                            numbers.append(number)
                        if any((BrokerAPI._looks_like_timestamp(x) for x in )):
                            prices = [x for x in numbers if 0 < x < 1000000000]
                            if prices:
                                return float(prices[-1])
                        return

        numeric = BrokerAPI._to_finite_number(value)
        if numeric is not None:
            if 0 < numeric < 1000000000:
                return numeric
        return

    @staticmethod
    def _extract_tick_timestamp_ms(value):
        """Ambil timestamp broker dari object/array; string timestamp juga didukung."""
        timestamp_keys = ('timestamp', 'timestamp_ms', 'time', 'time_ms', 'created_at',
                          'createdAt', 'server_time', 'serverTime', 'ts', 'at')
        wrapper_keys = ('data', 'tick', 'ticks', 'quote', 'quotes', 'asset', 'assets',
                        'payload', 'result', 'candle', 'candles', 'items', 'updates',
                        'values')
        if isinstance(value, dict):
            for key in timestamp_keys:
                if key in value:
                    numeric = BrokerAPI._to_finite_number(value.get(key))

            if numeric is not None:
                if numeric > 10000000000L:
                    return int(numeric)
                if numeric >= 1000000000:
                    return int(numeric * 1000)
                for key in wrapper_keys:
                    if key in value:
                        found = BrokerAPI._extract_tick_timestamp_ms(value.get(key))
                        if found:
                            return found
                else:
                    for candidate in value.values():
                        if isinstance(candidate, (dict, list, tuple)):
                            found = BrokerAPI._extract_tick_timestamp_ms(candidate)
                            if found:
                                return found

        else:
            if isinstance(value, (list, tuple)):
                for candidate in value:
                    numeric = BrokerAPI._to_finite_number(candidate)
                    if numeric is not None:
                        if numeric > 10000000000L:
                            return int(numeric)
                        if numeric >= 1000000000:
                            return int(numeric * 1000)
                        for candidate in reversed(value):
                            if isinstance(candidate, (dict, list, tuple)):
                                found = BrokerAPI._extract_tick_timestamp_ms(candidate)
                                if found:
                                    return found

            return 0

    @staticmethod
    def _parse_iso_timestamp_ms(value):
        """Ubah timestamp ISO-8601 Stockity menjadi epoch milidetik."""
        if not value:
            return 0
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[None[:-1]] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=(timezone.utc))
            return int(dt.timestamp() * 1000)
        except:
            except (TypeError, ValueError, OverflowError):
            return 0

    def _handle_range_stream_tick(self, message: dict, recv_ms: int) -> bool:
        """Parse feed range_stream Stockity: data -> assets -> rate/provider_time."""
        topic = str(message.get("topic") or "")
        if not topic.startswith("range_stream:"):
            return False
        asset_ric = topic.split("range_stream:", 1)[1]
        payload = message.get("payload")
        if not isinstance(payload, dict):
            payload = message
        else:
            container = payload
            response = payload.get("response") if isinstance(payload, dict) else None
            if isinstance(response, dict):
                if "data" in response:
                    container = response
            rows = container.get("data", []) if isinstance(container, dict) else []
            if isinstance(rows, dict):
                rows = [
                 rows]
            return isinstance(rows, list) or False
        found = False
        for row in rows:
            if not isinstance(row, dict):
                pass
            else:
                assets = row.get("assets", [])
                if isinstance(assets, dict):
                    assets = [
                     assets]
                if not isinstance(assets, list):
                    pass
                else:
                    for asset in assets:
                        if not isinstance(asset, dict):
                            pass
                        else:
                            ric = str(asset.get("ric") or asset_ric)
                            if ric != asset_ric:
                                pass
                            else:
                                price = self._to_finite_number(asset.get("rate"))
                                if price is None:
                                    bid = self._to_finite_number(asset.get("bid"))
                                    ask = self._to_finite_number(asset.get("ask"))
                                    if bid is not None:
                                        if ask is not None:
                                            price = (bid + ask) / 2.0
                                if not price is None:
                                    if price <= 0:
                                        pass
                                    else:
                                        broker_ms = self._parse_iso_timestamp_ms(asset.get("provider_time")) or self._parse_iso_timestamp_ms(asset.get("created_at_with_millis")) or self._parse_iso_timestamp_ms(asset.get("sent_at")) or self._parse_iso_timestamp_ms(asset.get("created_at"))
                                        self._store_asset_tick(asset_ric, price, recv_ms, broker_ms)
                                        if self._asset_debug_count < 3:
                                            self._log(f'[WS RANGE TICK] ric={asset_ric} price={price} broker_ms={broker_ms or "-"}')
                                            self._asset_debug_count += 1
                                        found = True
                    else:
                        return found

    def _scan_any_message_for_asset_tick(self, value, target_ric: str, recv_ms: int) -> bool:
        """Cari object harga nyata di frame mana pun tanpa menganggap metadata range sebagai harga."""
        found = False

        def walk(node):
            nonlocal found
            if isinstance(node, dict):
                ric = str(node.get("ric") or node.get("asset") or node.get("symbol") or "")
                has_price_field = any((k in node for k in ('rate', 'bid', 'ask', 'price',
                                                           'quote')))
                if has_price_field:
                    if not ric or ric == target_ric:
                        price = self._to_finite_number(node.get("rate"))
                        if price is None:
                            bid = self._to_finite_number(node.get("bid"))
                            ask = self._to_finite_number(node.get("ask"))
                            if bid is not None:
                                if ask is not None:
                                    price = (bid + ask) / 2.0
                        if price is None:
                            price = self._to_finite_number(node.get("price"))
                        if price is None:
                            price = self._to_finite_number(node.get("quote"))
                        if price is not None:
                            if price > 0:
                                broker_ms = self._parse_iso_timestamp_ms(node.get("provider_time")) or self._parse_iso_timestamp_ms(node.get("created_at_with_millis")) or self._parse_iso_timestamp_ms(node.get("sent_at")) or self._parse_iso_timestamp_ms(node.get("created_at")) or self._extract_tick_timestamp_ms(node)
                                self._store_asset_tick(target_ric, price, recv_ms, broker_ms)
                                if self._asset_debug_count < 5:
                                    self._log(f'[WS AUTO TICK] ric={target_ric} price={price} broker_ms={broker_ms or "-"}')
                                    self._asset_debug_count += 1
                                found = True
                    for child in node.values():
                        if isinstance(child, (dict, list, tuple)):
                            walk(child)

                else:
                    pass
            if isinstance(node, (list, tuple)):
                for child in node:
                    if isinstance(child, (dict, list, tuple)):
                        walk(child)

        walk(value)
        return found

    def _store_asset_tick(self, asset_ric, price, recv_ms, tick_ms=0):
        record = {'price':float(price), 
         'recv_ms':int(recv_ms), 
         'broker_ms':int(tick_ms or 0)}
        with self._asset_tick_lock:
            history = self._asset_tick_history.setdefault(asset_ric, deque(maxlen=2000))
            history.append(record)
            self._latest_asset_price[asset_ric] = float(price)
            self._latest_asset_price_ms[asset_ric] = int(recv_ms)
            if int(tick_ms or 0) > 0:
                self._last_provider_time_ms = int(tick_ms)
                self._last_provider_recv_ms = int(recv_ms)

    def get_live_price(self, asset_ric: str, max_age_seconds: float=3.0):
        with self._asset_tick_lock:
            price = self._latest_asset_price.get(asset_ric)
            recv_ms = int(self._latest_asset_price_ms.get(asset_ric) or 0)
        if price is None or recv_ms <= 0:
            return
        if int(time.time() * 1000) - recv_ms > int(max_age_seconds * 1000):
            return
        return float(price)

    def wait_live_price(self, asset_ric, timeout_seconds=10.0, newer_than_ms=0, at_or_after_ms=0):
        """Tunggu tick live. at_or_after_ms memilih tick pertama pada/setelah expiry."""
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        threshold = max(int(newer_than_ms or 0) + (1 if newer_than_ms else 0), int(at_or_after_ms or 0))
        while time.monotonic() < deadline:
            now_ms = int(time.time() * 1000)
            with self._asset_tick_lock:
                history = list(self._asset_tick_history.get(asset_ric, ()))
            if threshold:
                for tick in history:
                    recv_ms = int(tick.get("recv_ms") or 0)

                if recv_ms >= threshold:
                    return (
                     float(tick["price"]), recv_ms)
            else:
                if history:
                    tick = history[-1]
                    recv_ms = int(tick.get("recv_ms") or 0)
                    if now_ms - recv_ms <= int(max(3.0, timeout_seconds) * 1000):
                        return (
                         float(tick["price"]), recv_ms)
            time.sleep(0.01)

        return (None, 0)

    def get_last_live_price_before(self, asset_ric: str, before_ms: int, max_lookback_ms: int=5000):
        """Return tick terakhir sebelum boundary; dipakai sebagai CLOSE candle M1."""
        before_ms = int(before_ms or 0)
        if before_ms <= 0:
            return (None, 0)
        with self._asset_tick_lock:
            history = list(self._asset_tick_history.get(asset_ric, ()))
        for tick in reversed(history):
            recv_ms = int(tick.get("recv_ms") or 0)
            if recv_ms < before_ms:
                if before_ms - recv_ms <= int(max_lookback_ms):
                    return (
                     float(tick["price"]), recv_ms)
                break
            return (None, 0)

    def wait_candle_close_price(self, asset_ric: str, boundary_ms: int, timeout_seconds: float=10.0):
        """Tunggu boundary lewat lalu ambil tick TERAKHIR sebelum boundary sebagai close candle."""
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        boundary_ms = int(boundary_ms)
        while time.monotonic() < deadline:
            now_ms = int(time.time() * 1000)
            if now_ms >= boundary_ms:
                price, tick_ms = self.get_last_live_price_before(asset_ric, boundary_ms, max_lookback_ms=10000)
                if price is not None:
                    return (
                     price, tick_ms)
            time.sleep(0.01)

        return (None, 0)

    def get_balance(self, account_type='demo'):
        url = f"{self.base_api}/bank/v1/read?locale=en"
        res = self.session.get(url, timeout=(self.timeout))
        res.raise_for_status()
        data = res.json().get("data", [])
        for item in data:
            if item.get("account_type") == account_type:
                return item.get("amount", 0) / 100
            return 0

    def get_result_detail(self, order_id: str, account_type=None):
        account_type = account_type or self.cfg.get("account_type", "demo")
        url = f"{self.base_api}/bo-deals-history/v3/deals/trade?type={account_type}&locale=en"
        res = self.session.get(url, timeout=(self.timeout))
        res.raise_for_status()
        deals = res.json().get("data", {}).get("standard_trade_deals", [])
        deal = next((d for d in deals if str(d.get("uuid")) == str(order_id)), None)
        if not deal:
            return {'result': '"PENDING"', 
             'amount': 0, 
             'win': 0, 
             'profit': 0}
            status = str(deal.get("status", "")).lower()
            amount = float(deal.get("amount") or deal.get("bet") or 0) / 100
            win = float(deal.get("win") or 0) / 100
            if status == "won":
                result = "WIN"
                profit = win - amount
        elif status == "lost":
            result = "LOSE"
            profit = -amount
        else:
            if status in ('draw', 'tie', 'returned', 'refund'):
                result = "DRAW"
                profit = 0
            else:
                result = "PENDING"
                profit = 0
        return {'result': result, 
         'amount': amount, 
         'win': win, 
         'profit': profit}

    def _log(self, msg: str):
        if self.logger:
            self.logger.log(msg)
        else:
            print(msg)

    def _rest_headers(self) -> Dict[(str, str)]:
        headers = {'Accept':"application/json", 
         'Content-Type':"application/json", 
         'device-type':"web", 
         'User-Agent':self.user_agent}
        if self.authtoken:
            if self.device_id:
                headers.update({'authorization-token':self.authtoken, 
                 'device-id':str(self.device_id), 
                 'Cookie':f"authtoken={self.authtoken}; device_id={self.device_id}; device_type=web"})
        return headers

    def _ws_headers(self) -> Dict[(str, str)]:
        return {'User-Agent':self.user_agent, 
         'Cookie':f"device_type=web; device_id={self.device_id}; authtoken={self.authtoken}", 
         'device-id':str(self.device_id), 
         'device-type':"web", 
         'Origin':self.web_url}

    def get_current_candle(self, asset_ric='Z-CRY/IDX'):
        hari_ini = datetime.utcnow().strftime("%Y-%m-%dT00:00:00")
        ric_encoded = quote(asset_ric, safe="")
        url = f"{self.base_api}/candles/v1/{ric_encoded}/{hari_ini}/60"
        res = self.session.get(url, timeout=5)
        res.raise_for_status()
        candles = res.json().get("data", [])
        if not candles:
            return
        return candles[-1]

    def _next_ref(self) -> str:
        r = str(self._ref)
        self._ref += 1
        return r

    def get_latest_order_uuid(self, account_type='demo'):
        url = f"{self.base_api}/bo-deals-history/v3/deals/trade?type={account_type}&locale=en"
        res = self.session.get(url, timeout=(self.timeout))
        res.raise_for_status()
        deals = res.json().get("data", {}).get("standard_trade_deals", [])
        if not deals:
            return ""
        return str(deals[0].get("uuid") or "")

    async def _send(self, topic: str, event: str, payload: dict, join_ref: Optional[str]=None) -> str:
        ref = self._next_ref()
        msg = {
         'topic': topic, 
         'event': event, 
         'payload': payload, 
         'ref': ref}
        if join_ref is not None:
            msg["join_ref"] = str(join_ref)
        await self._ws.send(json.dumps(msg, separators=(',', ':')))
        return ref

    async def _auto_reconnect(self, reason='unknown'):
        if self._reconnecting:
            self._log(f"[WS AUTO RECONNECT] skip, already reconnecting | reason={reason}")
            return
        if self._order_pending:
            self._log("[WS AUTO RECONNECT SKIP] order pending, tunggu fallback/history")
            return
        self._reconnecting = True
        try:
            asset_ric = self.cfg.get("asset_ric", "Z-CRY/IDX")
            attempt = 0
            while not self._order_pending:
                attempt += 1
                try:
                    self._log(f"[WS AUTO RECONNECT] #{attempt} reason={reason}")
                    self._ws = None
                    self._joined = False
                    self._join_refs = {}
                    await self._connect_ws(asset_ric, fresh=True)
                    if self._ws is not None:
                        if self._joined:
                            self._log("[WS AUTO RECONNECT] sukses")
                            break
                except Exception as e:
                    try:
                        self._log(f"[WS AUTO RECONNECT ERROR] {e}")
                    finally:
                        e = None
                        del e

                await asyncio.sleep(1)

        finally:
            self._reconnecting = False

    async def _ws_heartbeatParse error at or near `SETUP_FINALLY' instruction at offset 0

    async def _ws_listenerParse error at or near `SETUP_FINALLY' instruction at offset 0_2

    async def _connect_price_ws_once(self, asset_ric: str):
        """Buka socket harga Stockity dan subscribe RIC yang dipakai FO."""
        cookie_parts = [
         "device_type=web"]
        if self.device_id:
            cookie_parts.append(f"device_id={self.device_id}")
        if self.authtoken:
            cookie_parts.append(f"authtoken={self.authtoken}")
        headers = {'User-Agent':self.user_agent, 
         'Cache-Control':"no-cache", 
         'Pragma':"no-cache", 
         'Referer':"https://stockity.com/", 
         'device-type':"web", 
         'Cookie':("; ".join)(cookie_parts)}
        if self.device_id:
            headers["device-id"] = str(self.device_id)
        if self.authtoken:
            headers["authorization-token"] = str(self.authtoken)
        self._log(f'[PRICE WS AUTH] token={"YES" if self.authtoken else "NO"} device_id={"YES" if self.device_id else "NO"}')
        self._log(f"[PRICE WS CONNECT] {self.price_ws_url}")
        try:
            self._price_ws = await websockets.connect((self.price_ws_url),
              origin="https://stockity.com",
              extra_headers=headers,
              ping_interval=20,
              ping_timeout=20,
              close_timeout=5)
        except TypeError:
            self._price_ws = await websockets.connect((self.price_ws_url),
              origin="https://stockity.com",
              additional_headers=headers,
              ping_interval=20,
              ping_timeout=20,
              close_timeout=5)
        else:
            subscribe = {'action':"subscribe", 
             'rics':[asset_ric]}
            await self._price_ws.send(json.dumps(subscribe, separators=(',', ':')))
            self._log(f"[PRICE WS SUBSCRIBE] ric={asset_ric}")

    def _handle_price_ws_message(self, data, asset_ric: str, recv_ms: int) -> bool:
        """Parse frame as.stockity.com: data -> rows -> assets -> rate/provider_time."""
        found = False
        containers = []
        if isinstance(data, dict):
            containers.append(data)
            response = data.get("response")
            if isinstance(response, dict):
                containers.append(response)
            payload = data.get("payload")
            if isinstance(payload, dict):
                containers.append(payload)
        for container in containers:
            rows = container.get("data", [])
            if isinstance(rows, dict):
                rows = [
                 rows]
            if not isinstance(rows, list):
                pass
            else:
                for row in rows:
                    if not isinstance(row, dict):
                        pass
                    else:
                        assets = row.get("assets", [])
                        if isinstance(assets, dict):
                            assets = [
                             assets]
                        if not isinstance(assets, list):
                            pass
                        else:
                            for asset in assets:
                                if not isinstance(asset, dict):
                                    pass
                                else:
                                    ric = str(asset.get("ric") or asset_ric)
                                    if ric != asset_ric:
                                        pass
                                    else:
                                        price = self._to_finite_number(asset.get("rate"))
                                        if price is None:
                                            bid = self._to_finite_number(asset.get("bid"))
                                            ask = self._to_finite_number(asset.get("ask"))
                                            if bid is not None:
                                                if ask is not None:
                                                    price = (bid + ask) / 2.0
                                        if not price is None:
                                            if price <= 0:
                                                pass
                                            else:
                                                broker_ms = self._parse_iso_timestamp_ms(asset.get("provider_time")) or self._parse_iso_timestamp_ms(asset.get("created_at_with_millis")) or self._parse_iso_timestamp_ms(asset.get("sent_at")) or self._parse_iso_timestamp_ms(asset.get("created_at"))
                                                self._store_asset_tick(ric, price, recv_ms, broker_ms)
                                                if self._asset_debug_count < 5:
                                                    self._log(f'[PRICE TICK] ric={ric} rate={price} broker_ms={broker_ms or "-"} recv_ms={recv_ms}')
                                                    self._asset_debug_count += 1
                                                found = True
                            else:
                                return found

    async def _price_stream_loopParse error at or near `LOAD_FAST' instruction at offset 0

    async def _connect_wsParse error at or near `LOAD_FAST' instruction at offset 0

    async def _close_ws(self):
        try:
            try:
                if self._ws:
                    if not self._ws.closed:
                        await self._ws.close()
                        if self.debug_ws:
                            self._log("[WS] closed after order")
            except Exception as e:
                try:
                    if self.debug_ws:
                        self._log(f"[WS CLOSE ERROR] {e}")
                finally:
                    e = None
                    del e

        finally:
            self._ws = None
            self._joined = False
            self._join_refs = {}

    async def _join_one(self, topic: str):
        ref = self._next_ref()
        self._join_refs[topic] = ref
        await self._ws.send(json.dumps({'topic':topic, 
         'event':"phx_join", 
         'payload':{},  'ref':ref, 
         'join_ref':ref},
          separators=(',', ':')))
        return ref

    async def _join_channels(self, asset_ric: str):
        refs = {}
        for topic in (
         "account", "user", "bo", f"asset:{asset_ric}"):
            join_ref = await self._join_one(topic)
            refs[str(join_ref)] = topic
        else:
            deadline = time.time() + self.timeout
            joined_topics = set()
            if time.time() < deadline:
                if len(joined_topics) < len(refs):
                    for ref, topic in list(refs.items()):
                        reply = self._pending_refs.pop(ref, None)
                        if reply:
                            payload = reply.get("payload", {}) or {}
                            status = str(payload.get("status", "")).lower()
                            if self.debug_ws:
                                self._log(f"[WS JOIN REPLY] topic={topic} status={status}")
                            if status == "ok":
                                joined_topics.add(topic)
                            await asyncio.sleep(0.05)

            else:
                self._joined = len(joined_topics) >= len(refs)
                if self._joined:
                    self._log(f'[WS] joined OK {",".join(sorted(joined_topics))}')
                else:
                    self._log(f'[WS] joined PARTIAL {",".join(sorted(joined_topics)) or "none"}')

    def _token_empty(self) -> bool:
        return not self.authtoken or not self.device_id or "ISI_" in str(self.authtoken) or "ISI_" in str(self.device_id)

    def _do_reloginParse error at or near `LOAD_GLOBAL' instruction at offset 0

    def login(self) -> bool:
        if self._token_empty():
            self._do_relogin()
        url = f"{self.base_api}/platform/private/v2/profile?locale=en"
        try:
            res = self.session.get(url, timeout=(self.timeout))
            res.raise_for_status()
        except Exception as e:
            try:
                self._log(f"[LOGIN] Token gagal/expired: {e}")
                if self.cfg.get("auto_relogin", True):
                    self._log("[LOGIN] Coba relogin sesuai login_mode...")
                    self._do_relogin()
                    res = self.session.get(url, timeout=(self.timeout))
                    res.raise_for_status()
                else:
                    raise
            finally:
                e = None
                del e

        else:
            data = res.json().get("data", {})
            email = data.get("email", "-")
            user_id = data.get("id", "-")
            self.user_id = str(user_id)
            self.profile_data = data
            currency = data.get("currency", "-")
            self._log(f"[LOGIN] OK broker={self.broker} email={email} id={user_id} currency={currency}")
            return True

    def _sync_server_time(self, force: bool=False) -> int:
        """Sinkronkan clock lokal ke header Date server broker.

        Offset dihitung memakai titik tengah request agar network RTT tidak
        membuat created_at terlihat terlambat. Nilai disimpan selama 30 detik.
        """
        now_mono = time.monotonic()
        if not force:
            if now_mono - self._last_time_sync_monotonic < 30:
                return int(self._server_time_offset_ms)
        url = f"{self.base_api}/platform/private/v2/profile?locale=en"
        try:
            local_before = time.time() * 1000
            res = self.session.get(url, timeout=(min(self.timeout, 8)))
            local_after = time.time() * 1000
            date_header = res.headers.get("Date") or res.headers.get("date")
            if not date_header:
                raise RuntimeError("header Date tidak ditemukan")
            server_dt = parsedate_to_datetime(date_header)
            if server_dt.tzinfo is None:
                server_dt = server_dt.replace(tzinfo=(timezone.utc))
            server_ms = server_dt.timestamp() * 1000
            local_mid = (local_before + local_after) / 2
            self._server_time_offset_ms = int(server_ms - local_mid)
            self._last_time_sync_monotonic = now_mono
            rtt_ms = int(local_after - local_before)
            self._log(f"[TIME SYNC] offset={self._server_time_offset_ms:+d}ms rtt={rtt_ms}ms")
        except Exception as exc:
            try:
                self._log(f"[TIME SYNC WARNING] gagal: {exc}; pakai clock lokal")
            finally:
                exc = None
                del exc

        else:
            return int(self._server_time_offset_ms)

    def _broker_now_ms(self, force_sync: bool=False) -> int:
        self._sync_server_time(force=force_sync)
        return int(time.time() * 1000) + int(self._server_time_offset_ms)

    def _blitz_client_time_ms(self) -> int:
        """Waktu client Blitz berdasarkan provider_time dari socket harga.

        Header HTTP Date hanya presisi satu detik sehingga offset-nya meloncat-loncat
        dan dapat memicu slow_internet_connection. provider_time memiliki presisi
        milidetik. Nilainya diproyeksikan sampai saat ini. Jangan menambah lead besar lagi,
        karena provider_time sendiri sudah mengoreksi selisih clock Windows terhadap broker.
        """
        local_now = int(time.time() * 1000)
        provider_ms = int(self._last_provider_time_ms or 0)
        provider_recv_ms = int(self._last_provider_recv_ms or 0)
        lead_ms = int(self.cfg.get("blitz_client_time_lead_ms", 0))
        max_age_ms = int(self.cfg.get("blitz_provider_time_max_age_ms", 3000))
        if provider_ms > 0:
            if provider_recv_ms > 0:
                age_ms = max(0, local_now - provider_recv_ms)
                if age_ms <= max_age_ms:
                    projected_provider_ms = provider_ms + age_ms
                    created_ms = projected_provider_ms + lead_ms
                    effective_offset = created_ms - local_now
                    return int(created_ms)
        fallback = self._broker_now_ms(force_sync=False) + lead_ms
        self._log(f"[BLITZ CLOCK] source=http_fallback lead={lead_ms}ms effective_offset={fallback - local_now:+d}ms")
        return int(fallback)

    def _expire_at(self, duration_seconds: int) -> int:
        duration_seconds = max(1, int(duration_seconds))
        now = datetime.now()
        if duration_seconds < 60:
            return int((now + timedelta(seconds=duration_seconds)).timestamp())
        else:
            minutes = max(1, math.ceil(duration_seconds / 60))
            base = now.replace(second=0, microsecond=0)
            if now.second < 30:
                exp = base + timedelta(minutes=minutes)
            else:
                exp = base + timedelta(minutes=(minutes + 1))
        return int(exp.timestamp())

    def _payload_amount(self, amount: float) -> int:
        multiplier = int(self.cfg.get("amount_multiplier", 100))
        return int(float(amount) * multiplier)

    async def _place_order_asyncParse error at or near `LOAD_CONST' instruction at offset 0

    def ensure_ws_ready(self, asset_ric='Z-CRY/IDX'):
        self.start_ws_background(asset_ric)
        time.sleep(1)
        return True

    def start_ws_background(self, asset_ric='Z-CRY/IDX'):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        if self._loop_thread:
            if self._loop_thread.is_alive():
                return

        def runner():
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._connect_ws(asset_ric, fresh=False))
            self._price_task = self._loop.create_task(self._price_stream_loop(asset_ric))
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=runner, daemon=True)
        self._loop_thread.start()
        self._log("[WS BACKGROUND] broker loop started")

    def find_recent_order_uuid(self, account_type, amount_payload, trend, asset_ric, sent_ms):
        url = f"{self.base_api}/bo-deals-history/v3/deals/trade?type={account_type}&locale=en"
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                res = self.session.get(url, timeout=3)
                res.raise_for_status()
                deals = res.json().get("data", {}).get("standard_trade_deals", [])
                candidates = []
                for d in deals[None[:15]]:
                    uuid = str(d.get("uuid") or "")
                    if not uuid:
                        pass
                    else:
                        d_amount = int(d.get("amount") or d.get("bet") or 0)
                        d_trend = str(d.get("trend") or "")
                        d_ric = str(d.get("asset_ric") or d.get("ric") or "")
                        d_ms = self._parse_deal_time_ms(d.get("created_at") or d.get("requested_at") or d.get("opened_at"))
                        if d_amount != int(amount_payload):
                            pass
                        elif d_trend != trend:
                            pass
                        elif d_ric != asset_ric:
                            pass
                        else:
                            delta_ms = abs(d_ms - int(sent_ms)) if d_ms else 15000
                            if d_ms and delta_ms > 15000:
                                pass
                            else:
                                candidates.append((delta_ms, uuid, d_amount, d_trend, d_ric, d_ms))
                else:
                    if candidates:
                        candidates.sort(key=(lambda item: item[0]))
                        delta_ms, uuid, d_amount, d_trend, d_ric, d_ms = candidates[0]
                        self._log(f"[HISTORY MATCH] uuid={uuid} amount={d_amount} trend={d_trend} ric={d_ric} delta_ms={delta_ms}")
                        return                         return uuid

            except Exception as e:
                try:
                    self._log(f"[HISTORY MATCH ERROR] {e}")
                finally:
                    e = None
                    del e

            else:
                time.sleep(0.3)

        return ""

    def place_order(self, direction, symbol, amount, duration_seconds, account_type):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        future = asyncio.run_coroutine_threadsafe(self._place_order_async(direction, symbol, amount, duration_seconds, account_type, "turbo"), self._loop)
        return future.result(timeout=(self.timeout + 30))

    def place_order_blitz(self, direction, symbol, amount, duration_seconds, account_type):
        """Create order Blitz/5ST memakai payload timestamp milidetik."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        future = asyncio.run_coroutine_threadsafe(self._place_order_async(direction, symbol, amount, duration_seconds, account_type, "blitz"), self._loop)
        return future.result(timeout=(self.timeout + 30))

    def _parse_deal_time_msParse error at or near `SETUP_FINALLY' instruction at offset 0

    def get_ws_closed_resultParse error at or near `LOAD_GLOBAL' instruction at offset 0

    def wait_ws_closed_result(self, order_id: str, timeout_seconds: float=0):
        start = time.time()
        while True:
            result = self.get_ws_closed_result(order_id)
            if result in ('WIN', 'LOSE', 'DRAW'):
                return result
            if timeout_seconds <= 0 or time.time() - start >= timeout_seconds:
                return "PENDING"
            time.sleep(0.2)

    def get_resultParse error at or near `LOAD_DEREF' instruction at offset 0