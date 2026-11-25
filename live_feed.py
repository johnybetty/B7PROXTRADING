"""live_feed.py — Real WebSocket connector ke broker Binomo/Stockity
Exact protocol dari 448_LivePriceFeed.js (decompiled APK).

Usage (from dashboard.py):
    from live_feed import LiveFeed
    feed = LiveFeed(broker='Binomo', authtoken='xxx', device_id='yyy',
                    pair='Z-CRY/IDX', on_tick=callback, on_status=status_cb)
    feed.start()
    feed.stop()
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import random
import ssl
import time
import threading

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger(__name__)

# ─── Broker Config (dari 448_LivePriceFeed.js closure_1_5) ──────────────────
WS_URLS = {
    'Binomo':   'wss://as.binomo5.com/',
    'Stockity': 'wss://as.stockity.com/',
}
WS_URL_CANDIDATES = {
    'Binomo': [
        'wss://as.binomo5.com/',
        'wss://as.binomo.com/',
        'wss://ws.binomo5.com/',
    ],
    'Stockity': [
        'wss://as.stockity.com/',
        'wss://as.stockity1.id/',
        'wss://as.stockity.id/',
        'wss://ws.stockity1.id/',
    ],
}
ORIGINS = {
    'Binomo':   'https://binomo5.com',
    'Stockity': 'https://stockity.com',
}
# ─────────────────────────────────────────────────────────────────────────────

def get_ssl_context():
    """Create a permissive SSL context that works reliably on Android & PC."""
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _parse_ts_ms(val, fallback_ms: int) -> int:
    """Parse various timestamp representations into Unix ms."""
    if not val:
        return fallback_ms
    if isinstance(val, (int, float)):
        # If in seconds (10 digits), convert to ms
        if val < 10_000_000_000:
            return int(val * 1000)
        return int(val)
    if isinstance(val, str):
        val = val.strip()
        if val.isdigit():
            n = int(val)
            return n * 1000 if n < 10_000_000_000 else n
        try:
            # ISO format: 2026-11-18T00:54:50.986000Z
            iso = val.replace('Z', '+00:00')
            dt = datetime.fromisoformat(iso)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    return fallback_ms


def parse_price_frame(raw_msg: str | bytes, expected_pair, now_ms: int) -> list[dict]:
    """Parse broker WebSocket frame → list of tick dicts.

    Handles real Binomo/Stockity live tick structure:
      {
        "data": [
          {
            "assets": [
              {
                "ric": "Z-CRY/IDX",
                "rate": 641.86722194,
                "bid": 641.86722192,
                "ask": 641.86722196,
                "created_at_with_millis": "2026-11-18T00:54:50.986000Z"
              }
            ]
          }
        ]
      }
    """
    ticks = []
    try:
        if isinstance(raw_msg, bytes):
            raw_msg = raw_msg.decode('utf-8', errors='replace')

        msg = json.loads(raw_msg)
        candidates = []

        def try_extract(obj):
            if isinstance(obj, dict):
                # Direct asset object
                if 'ric' in obj and ('bid' in obj or 'ask' in obj or 'rate' in obj or 'price' in obj):
                    candidates.append(obj)
                    return
                for key in ('response', 'payload', 'data'):
                    if key in obj:
                        try_extract(obj[key])
                if 'assets' in obj:
                    assets = obj['assets']
                    if isinstance(assets, list):
                        for a in assets:
                            candidates.append(a)
            elif isinstance(obj, list):
                for item in obj:
                    try_extract(item)

        try_extract(msg)

        is_multi = isinstance(expected_pair, (set, list, tuple))

        for asset in candidates:
            ric = asset.get('ric', '') or asset.get('pair', '')
            if expected_pair and expected_pair != '*':
                if is_multi:
                    if ric not in expected_pair:
                        continue
                else:
                    if not ric:
                        ric = str(expected_pair)
                    elif ric != expected_pair:
                        continue
            elif not ric and not is_multi and expected_pair:
                ric = str(expected_pair)
            elif not ric:
                continue

            bid = float(asset.get('bid', 0) or 0)
            ask = float(asset.get('ask', 0) or 0)
            rate = float(asset.get('rate', 0) or asset.get('price', 0) or 0)

            if bid > 0 and ask > 0:
                price = (bid + ask) / 2.0
            elif rate > 0:
                price = rate
                if bid == 0: bid = rate
                if ask == 0: ask = rate
            elif bid > 0:
                price = bid
            elif ask > 0:
                price = ask
            else:
                continue

            raw_ts = (
                asset.get('created_at_with_millis') or
                asset.get('provider_time') or
                asset.get('sent_at') or
                asset.get('created_at') or
                asset.get('ts')
            )
            ts_ms = _parse_ts_ms(raw_ts, now_ms)
            second = round((ts_ms % 60000) / 1000.0, 2)
            candle_id = int((ts_ms // 60000) * 60)

            ticks.append({
                'pair':      ric,
                'price':     round(price, 6),
                'bid':       round(bid, 6),
                'ask':       round(ask, 6),
                'second':    second,
                'candle_id': candle_id,
                'ts_ms':     ts_ms,
            })

    except Exception as e:
        logger.warning(f'parse_price_frame error: {e} | raw={str(raw_msg)[:200]}')

    return ticks


class LiveFeed:
    """Real WebSocket feed dari broker Binomo/Stockity."""

    def __init__(self, broker: str, authtoken: str, device_id: str, pair=None,
                 on_tick=None, on_status=None, reconnect: bool = True):
        if not HAS_WEBSOCKETS:
            raise ImportError('websockets tidak terinstall. Jalankan: pip install websockets')

        self.broker    = broker
        self.authtoken = authtoken.strip()
        self.device_id = device_id.strip()
        
        if pair is None:
            self.pairs = set(['Z-CRY/IDX'])
            self.pair = 'Z-CRY/IDX'
        elif isinstance(pair, (list, tuple, set)):
            self.pairs = set(str(p).strip() for p in pair if str(p).strip())
            self.pair = next(iter(self.pairs), 'Z-CRY/IDX')
        else:
            self.pair  = str(pair).strip()
            self.pairs = set([self.pair])

        self.on_tick   = on_tick or (lambda t: None)
        self.on_status = on_status or (lambda s: None)
        self.reconnect = reconnect

        self._stop_event = threading.Event()
        self._thread     = None
        self._loop       = None
        self._ws         = None

        # Stats
        self.tick_count    = 0
        self.last_tick_ts  = 0
        self.error_message = ''
        self.connected     = False

    def set_pairs(self, pairs):
        """Update daftar pair yang di-subscribe dan kirim subscribe langsung via socket terbuka."""
        if isinstance(pairs, (list, tuple, set)):
            new_set = set(str(p).strip() for p in pairs if str(p).strip())
        else:
            new_set = set([str(pairs).strip()])
        if not new_set:
            return
        self.pairs = new_set
        self.pair = next(iter(self.pairs), 'Z-CRY/IDX')
        if self._ws and self._loop and self._loop.is_running() and self.connected:
            sub_msg = json.dumps({'action': 'subscribe', 'rics': list(self.pairs)})
            asyncio.run_coroutine_threadsafe(self._ws.send(sub_msg), self._loop)

    def add_pairs(self, new_pairs):
        """Tambah pair baru tanpa memutus koneksi WebSocket."""
        if isinstance(new_pairs, (list, tuple, set)):
            to_add = [str(p).strip() for p in new_pairs if str(p).strip() and str(p).strip() not in self.pairs]
        else:
            p = str(new_pairs).strip()
            to_add = [p] if p and p not in self.pairs else []
        if not to_add:
            return
        self.pairs.update(to_add)
        if self._ws and self._loop and self._loop.is_running() and self.connected:
            sub_msg = json.dumps({'action': 'subscribe', 'rics': to_add})
            asyncio.run_coroutine_threadsafe(self._ws.send(sub_msg), self._loop)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='LiveFeed')
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception:
                pass
        self.connected = False
        self.on_status('DISCONNECTED')

    def is_running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_connect())
        except Exception as e:
            if not self._stop_event.is_set():
                self.error_message = str(e)
                self.on_status(f'ERROR: {e}')
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()

    async def _main_connect(self):
        broker_key = self.broker if self.broker in WS_URL_CANDIDATES else 'Binomo'
        candidate_urls = WS_URL_CANDIDATES.get(broker_key, [WS_URLS.get(self.broker, 'wss://as.binomo5.com/')])
        origin = ORIGINS.get(self.broker, ORIGINS['Binomo'])

        headers = {
            'Cookie':      f'device_type=web; device_id={self.device_id}; authtoken={self.authtoken}',
            'device-id':   self.device_id,
            'device-type': 'web',
            'Origin':      origin,
            'User-Agent':  'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        }

        attempt = 0
        ssl_ctx = get_ssl_context()
        while not self._stop_event.is_set():
            attempt += 1
            url = candidate_urls[(attempt - 1) % len(candidate_urls)]
            url_host = url.split('/')[2] if '/' in url else url
            self.on_status(f'CONNECTING {url_host} (#{attempt})...')
            try:
                connect_kwargs = {
                    'ssl': ssl_ctx,
                    'ping_interval': 20,
                    'ping_timeout': 15,
                    'close_timeout': 5,
                    'max_size': 10 * 1024 * 1024,
                }
                try:
                    connect_kwargs['extra_headers'] = headers
                    ws = await websockets.connect(url, **connect_kwargs)
                except TypeError:
                    connect_kwargs.pop('extra_headers', None)
                    connect_kwargs['additional_headers'] = headers
                    ws = await websockets.connect(url, **connect_kwargs)

                async with ws:
                    self._ws = ws
                    self.connected = True
                    self.error_message = ''
                    self.on_status('CONNECTED')
                    attempt = 0

                    # Subscribe ke semua pair yang diminta
                    rics_to_sub = list(self.pairs)
                    sub_msg = json.dumps({'action': 'subscribe', 'rics': rics_to_sub})
                    await ws.send(sub_msg)
                    logger.info(f'Subscribed to {rics_to_sub} via {url}')

                    # Listen loop
                    async for raw_msg in ws:
                        if self._stop_event.is_set():
                            break
                        now_ms = int(time.time() * 1000)
                        ticks  = parse_price_frame(raw_msg, self.pairs, now_ms)
                        for tick in ticks:
                            self.tick_count   += 1
                            self.last_tick_ts  = tick['ts_ms']
                            try:
                                self.on_tick(tick)
                            except Exception as cb_err:
                                logger.error(f'on_tick callback error: {cb_err}')

            except ConnectionClosed as e:
                self.connected = False
                self.error_message = f'Closed ({e.code})'
                self.on_status(f'CONNECTION CLOSED: {e.code} {e.reason}')
            except OSError as e:
                self.connected = False
                self.error_message = f'Network: {e}'
                self.on_status(f'CONNECTION ERROR: {e}')
            except Exception as e:
                self.connected = False
                self.error_message = f'Error: {e}'
                self.on_status(f'ERROR: {e}')

            if not self.reconnect or self._stop_event.is_set():
                break

            wait = min(3 + (attempt * 2), 20)
            status_text = f'{self.error_message} | RECONNECTING in {wait}s...' if self.error_message else f'RECONNECTING in {wait}s...'
            self.on_status(status_text)
            for _ in range(wait * 10):
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(0.1)
