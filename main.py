"""BOT X - Dashboard v3 (B7PROXTRADING Edition)
Upgrade dari dashboard_v2.py dengan fitur:
  1. TP/SL Auto-Order (place_order via Binomo API)
  2. Export Sinyal ke CSV (live append + snapshot export)
  3. Alert Suara & Toast Popup
  4. Multi-Pair Monitoring (hingga 4 pair sekaligus)
Port: http://localhost:8080
"""
import os
import sys

# Windows Console UTF-8 compatibility (mencegah UnicodeEncodeError di EXE)
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr is not None and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import csv
import json
import time
import random
import datetime
import threading
import webbrowser
import ssl
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ── Application Base Directory (EXE, Android & Standalone-friendly) ───────────
if os.environ.get('ANDROID_FILES_DIR') and os.path.exists(os.environ['ANDROID_FILES_DIR']):
    BASE_DIR = os.environ['ANDROID_FILES_DIR']
elif getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── Engine & Broker Connectors (Standalone Single Folder) ────────────────────
import android_bridge
import pc_host_bridge
from broker_session_gate import GATE
from live_feed import LiveFeed
from auto_login import broker_login, fetch_balances
from phoenix_order import get_order_client

PORT = 8080

# ─── File Paths (Tersimpan di sebelah file EXE) ───────────────────────────────
SIGNALS_CSV_LIVE = os.path.join(BASE_DIR, 'signals_live.csv')
CSV_HEADERS = [
    'timestamp', 'candle_id', 'second', 'price', 'direction',
    'pattern', 'reason', 'mode', 'order_placed', 'order_amount',
    'order_result', 'profit'
]

def ensure_csv_header():
    if not os.path.exists(SIGNALS_CSV_LIVE):
        with open(SIGNALS_CSV_LIVE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(CSV_HEADERS)

def append_signal_csv(sig: dict, order: dict = None):
    ensure_csv_header()
    with open(SIGNALS_CSV_LIVE, 'a', newline='', encoding='utf-8') as f:
        res_text = ''
        if order:
            if order.get('ok'):
                res_text = order.get('status', 'OK')
            else:
                res_text = f"ERROR: {order.get('error', '')}"
        mode_str = sig.get('mode', '')
        if sig.get('pair'):
            mode_str = f"{mode_str} [{sig.get('pair')}]"
        csv.writer(f).writerow([
            sig.get('timestamp', ''), sig.get('candle_id', ''), sig.get('second', ''),
            sig.get('price', ''), sig.get('direction', ''), sig.get('pattern', ''),
            sig.get('reason', ''), mode_str,
            'YES' if order else 'NO',
            order.get('amount', '') if order else '',
            res_text,
            order.get('profit', 0) if order else '',
        ])

def export_signals_csv():
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = os.path.join(os.path.dirname(__file__), f'signals_log_{ts}.csv')
    with lock:
        sigs = list(app_state.get('signals_history', []))
    with open(fname, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADERS)
        for s in sigs:
            w.writerow([
                s.get('timestamp', ''), s.get('candle_id', ''), s.get('second', ''),
                s.get('price', ''), s.get('direction', ''), s.get('pattern', ''),
                s.get('reason', ''), s.get('mode', ''), '', '', '', ''
            ])
    return fname, len(sigs)

# ─── Persistent Session Manager ──────────────────────────────────────────────
SESSION_FILE = os.path.join(BASE_DIR, 'session_config.json')

def save_session_config(data: dict):
    session_file = os.path.join(BASE_DIR, 'session_config.json')
    try:
        current = {}
        if os.path.exists(session_file):
            try:
                with open(session_file, 'r', encoding='utf-8') as f:
                    current = json.load(f)
            except Exception:
                pass
        current.update(data)
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(current, f, indent=2)
        print(f"[+] Sesi akun tersimpan ke {session_file}")
    except Exception as e:
        print(f"[!] Gagal menyimpan sesi akun: {e}")

def load_session_config() -> dict:
    session_file = os.path.join(BASE_DIR, 'session_config.json')
    if os.path.exists(session_file):
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Gagal memuat sesi akun: {e}")
    return {}

_initial_cfg = load_session_config()
_has_saved_token = bool(_initial_cfg.get('authtoken') and _initial_cfg.get('device_id'))

# ─── Global App State (Disesuaikan untuk Akun Baru / Existing) ──────────────
app_state = {
    # Navigasi & Akun
    'current_tab':   'dashboard',
    'broker':        _initial_cfg.get('broker', 'Binomo'),
    'wallet':        _initial_cfg.get('wallet', 'DEMO'),
    'balance_demo':  float(_initial_cfg.get('balance_demo', 140000000.0)),
    'balance_real':  float(_initial_cfg.get('balance_real', 0.0)),
    'currency':      _initial_cfg.get('currency', 'IDR'),
    'user_id':       _initial_cfg.get('user_id', ''),
    'email':         _initial_cfg.get('email', ''),
    'logged_in':     _has_saved_token,
    'vip_active':    True,
    'vip_expires_at': '2099-12-31',

    # Credentials (kosong untuk akun baru, terisi jika sudah pernah login)
    'authtoken':     _initial_cfg.get('authtoken', ''),
    'device_id':     _initial_cfg.get('device_id', ''),

    # Koneksi Live Feed
    'feed_mode':     'LIVE' if _has_saved_token else 'SIMULATOR',
    'feed_status':   'CONNECTING...' if _has_saved_token else 'IDLE (Mode Simulator)',
    'feed_error':    '',
    'tick_count':    0,

    # Strategi & Kontrol
    'pair':          'Z-CRY/IDX',
    'strategy':      'OPEN_PRICE_PATTERN',
    'bot_running':   False,
    'speed':         1.0,

    # Live Tick & Market Data
    'candle_id':     int(time.time() // 60 * 60),
    'second':        0.0,
    'price':         1000.0,
    'open_price':    1000.0,
    'high_price':    1000.0,
    'low_price':     1000.0,
    'step':          0.0,
    'crossings':     0,
    'side':          'NEUTRAL',
    'one_way':       False,
    'reason':        'OBSERVING',

    # Sinyal
    'last_signal':       None,
    'signals_history':   [],
    'ticks_history':     [],

    # ── Risk & Money Management (Martingale & Compound/Parlay) ───────────────
    'mm_mode':                'HYBRID',    # 'HYBRID' | 'PARLAY' | 'MARTINGALE' | 'FLAT'
    'base_bet':               14000,       # OP Awal (Rp 14.000)
    'bet_amount':             14000,
    'next_bet_amount':        14000,
    'compensation_enabled':   True,

    # Martingale Settings
    'martingale_multiplier':  2.5,         # Perkalian (default 2.5x)
    'martingale_max_level':   5,           # Level kompensasi maksimal (1 - 7)
    'martingale_ladder':      [14000, 35000, 87500, 218750, 546875],
    'current_martingale_lvl': 0,           # Level aktif saat ini (0 = base)

    # Compound / Parlay Settings
    'parlay_target_steps':    2,           # Jumlah langkah per siklus (default 2)
    'parlay_payout_pct':      82.0,        # Asumsi Payout Market (default 82%)
    'auto_payout_sync':       True,        # Otomatis sinkronisasi payout dari broker
    'broker_payout_pct':      82.0,        # Nilai payment_rate riil dari broker
    'current_parlay_step':    0,           # Langkah aktif (0 = Langkah 1, 1 = Langkah 2, dst)
    'parlay_cycle_wins':      0,           # Total siklus parlay yang berhasil

    # ── Auto-Order / TP-SL ──────────────────────────────────────────────────
    'auto_order_enabled':     True,
    'tp_enabled':             True,
    'sl_enabled':             True,
    'tp_target':              500000,      # Rp 500k profit target
    'sl_amount_ao':           250000,      # Rp 250k max loss
    'session_profit':         0.0,
    'session_loss':           0.0,
    'session_wins':           0,
    'session_losses':         0,
    'session_draws':          0,
    'orders_history':         [],
    'current_ladder_idx':     0,
    'ladder':                 [14000, 35000, 87500, 218750, 546875],

    # ── Multi-pair ──────────────────────────────────────────────────────────
    'multi_pair_mode':    False,
    'pairs_watching':     ['Z-CRY/IDX'],
    'pairs_data':         {},

    # ── Alert state (untuk frontend) ────────────────────────────────────────
    'last_alert':         None,     # {type, message, ts}
    'tp_hit':             False,
    'sl_hit':             False,
}

lock = threading.RLock()

# ─── Singleton Feed Instance ─────────────────────────────────────────────────
_live_feed: LiveFeed | None = None
_sim_thread = None

# ─── Global multi-pair feeds ──────────────────────────────────────────────────
_multi_feeds: dict = {}   # pair → LiveFeed


# ─── place_order() ───────────────────────────────────────────────────────────
def place_order(direction: str, amount: float, pair: str) -> dict:
    wallet = app_state.get('wallet', 'DEMO')
    authtoken = app_state.get('authtoken', '')
    device_id = app_state.get('device_id', '')
    broker = app_state.get('broker', 'Binomo')

    if not authtoken or not device_id:
        return {'ok': False, 'error': 'Token atau Device ID kosong'}

    try:
        client = get_order_client(broker=broker, authtoken=authtoken, device_id=device_id, pair=pair)
        res = client.place_order(direction=direction, amount=amount, pair=pair, wallet=wallet)
        if res.get('ok'):
            # Segera refresh saldo di background
            threading.Thread(target=refresh_balance, daemon=True).start()
        return res
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ─── Money Management Helpers ────────────────────────────────────────────────
def generate_martingale_ladder(base: int, mult: float, levels: int) -> list:
    res = []
    val = float(base)
    for _ in range(max(1, levels)):
        res.append(int(round(val)))
        val *= mult
    return res


def calculate_current_bet() -> int:
    mm_mode = app_state.get('mm_mode', 'PARLAY')
    base = int(app_state.get('base_bet', 14000))

    if mm_mode == 'FLAT':
        return base

    elif mm_mode == 'MARTINGALE':
        ladder = app_state.get('martingale_ladder', [])
        if not ladder:
            mult = float(app_state.get('martingale_multiplier', 2.5))
            levels = int(app_state.get('martingale_max_level', 5))
            ladder = generate_martingale_ladder(base, mult, levels)
            app_state['martingale_ladder'] = ladder
        lvl = min(app_state.get('current_martingale_lvl', 0), len(ladder) - 1)
        return int(ladder[lvl])

    elif mm_mode == 'PARLAY':
        step = app_state.get('current_parlay_step', 0)
        payout = float(app_state.get('parlay_payout_pct', 82.0)) / 100.0
        bet = float(base)
        for _ in range(step):
            profit = bet * payout
            bet = bet + profit
        return int(round(bet))

    elif mm_mode == 'HYBRID':
        if app_state.get('current_martingale_lvl', 0) > 0:
            ladder = app_state.get('martingale_ladder', [])
            if not ladder:
                mult = float(app_state.get('martingale_multiplier', 2.5))
                levels = int(app_state.get('martingale_max_level', 5))
                ladder = generate_martingale_ladder(base, mult, levels)
                app_state['martingale_ladder'] = ladder
            lvl = min(app_state.get('current_martingale_lvl', 0), len(ladder) - 1)
            return int(ladder[lvl])
        else:
            step = app_state.get('current_parlay_step', 0)
            payout = float(app_state.get('parlay_payout_pct', 82.0)) / 100.0
            bet = float(base)
            for _ in range(step):
                profit = bet * payout
                bet = bet + profit
            return int(round(bet))

    return base


def resolve_deal_outcome(result: str, profit: float, bet: float):
    """Update state berdasarkan hasil WIN/LOSE/DRAW sesuai strategi MM yang aktif."""
    with lock:
        mm_mode = app_state.get('mm_mode', 'PARLAY')
        base_bet = int(app_state.get('base_bet', 14000))

        if result == 'WIN':
            app_state['session_profit'] += profit
            app_state['session_wins'] = app_state.get('session_wins', 0) + 1

            if mm_mode == 'MARTINGALE':
                app_state['current_martingale_lvl'] = 0
                next_b = calculate_current_bet()
                app_state['next_bet_amount'] = next_b
                app_state['last_alert'] = {
                    'type': 'win',
                    'message': f'🎉 WIN +Rp {profit:,.0f}! Martingale RESET ke Level 0 (Rp {next_b:,.0f})',
                    'ts': time.time()
                }

            elif mm_mode == 'PARLAY':
                curr_step = app_state.get('current_parlay_step', 0)
                target_steps = app_state.get('parlay_target_steps', 2)

                if curr_step + 1 >= target_steps:
                    # Siklus Berhasil Selesai!
                    app_state['current_parlay_step'] = 0
                    app_state['parlay_cycle_wins'] = app_state.get('parlay_cycle_wins', 0) + 1
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'win',
                        'message': f'🎯 SIKLUS PARLAY SELESAI! Untung Siklus +Rp {profit:,.0f} -> RESET ke Langkah 1 (Rp {next_b:,.0f})',
                        'ts': time.time()
                    }
                else:
                    # Lanjut ke langkah compound berikutnya
                    app_state['current_parlay_step'] = curr_step + 1
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'win',
                        'message': f'✅ PARLAY Langkah {curr_step+1} WIN! Lanjut ke Langkah {curr_step+2} -> Bet Rp {next_b:,.0f}',
                        'ts': time.time()
                    }

            elif mm_mode == 'HYBRID':
                # Cek apakah sedang memulihkan loss via Martingale:
                if app_state.get('current_martingale_lvl', 0) > 0:
                    rec_lvl = app_state['current_martingale_lvl']
                    app_state['current_martingale_lvl'] = 0
                    app_state['current_parlay_step'] = 0
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'win',
                        'message': f'🎉 WIN KOMPENSASI LV {rec_lvl} (+Rp {profit:,.0f})! Kerugian pulih -> Kembali ke Parlay Langkah 1 (Rp {next_b:,.0f})',
                        'ts': time.time()
                    }
                else:
                    curr_step = app_state.get('current_parlay_step', 0)
                    target_steps = app_state.get('parlay_target_steps', 2)

                    if curr_step + 1 >= target_steps:
                        app_state['current_parlay_step'] = 0
                        app_state['parlay_cycle_wins'] = app_state.get('parlay_cycle_wins', 0) + 1
                        next_b = calculate_current_bet()
                        app_state['next_bet_amount'] = next_b
                        app_state['last_alert'] = {
                            'type': 'win',
                            'message': f'🎯 SIKLUS PARLAY SELESAI (+Rp {profit:,.0f})! -> RESET ke Langkah 1 (Rp {next_b:,.0f})',
                            'ts': time.time()
                        }
                    else:
                        app_state['current_parlay_step'] = curr_step + 1
                        next_b = calculate_current_bet()
                        app_state['next_bet_amount'] = next_b
                        app_state['last_alert'] = {
                            'type': 'win',
                            'message': f'✅ PARLAY Langkah {curr_step+1} WIN! Lanjut ke Langkah {curr_step+2} -> Bet Rp {next_b:,.0f}',
                            'ts': time.time()
                        }

            elif mm_mode == 'FLAT':
                app_state['last_alert'] = {
                    'type': 'win',
                    'message': f'WIN +Rp {profit:,.0f}',
                    'ts': time.time()
                }

        elif result == 'LOSE':
            app_state['session_loss'] += bet
            app_state['session_losses'] = app_state.get('session_losses', 0) + 1

            if mm_mode == 'MARTINGALE':
                ladder = app_state.get('martingale_ladder', [])
                max_lvl = len(ladder)
                curr_lvl = app_state.get('current_martingale_lvl', 0)
                if curr_lvl + 1 < max_lvl:
                    app_state['current_martingale_lvl'] = curr_lvl + 1
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'loss',
                        'message': f'❌ LOSS -Rp {bet:,.0f}! Kompensasi Level {curr_lvl+1} -> Bet Rp {next_b:,.0f}',
                        'ts': time.time()
                    }
                else:
                    app_state['current_martingale_lvl'] = 0
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'loss',
                        'message': f'🛑 MAX KOMPENSASI HABIS! Reset ke Level 0 (Rp {next_b:,.0f})',
                        'ts': time.time()
                    }

            elif mm_mode == 'PARLAY':
                curr_step = app_state.get('current_parlay_step', 0)
                app_state['current_parlay_step'] = 0
                next_b = calculate_current_bet()
                app_state['next_bet_amount'] = next_b
                app_state['last_alert'] = {
                    'type': 'loss',
                    'message': f'❌ PARLAY GAGAL di Langkah {curr_step+1}! Reset ke Langkah 1 (Rp {next_b:,.0f})',
                    'ts': time.time()
                }

            elif mm_mode == 'HYBRID':
                app_state['current_parlay_step'] = 0
                ladder = app_state.get('martingale_ladder', [])
                max_lvl = len(ladder)
                curr_lvl = app_state.get('current_martingale_lvl', 0)
                if curr_lvl + 1 < max_lvl:
                    app_state['current_martingale_lvl'] = curr_lvl + 1
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'loss',
                        'message': f'❌ LOSS -Rp {bet:,.0f}! Masuk Kompensasi Martingale Lv {curr_lvl+1} -> Bet Rp {next_b:,.0f}',
                        'ts': time.time()
                    }
                else:
                    app_state['current_martingale_lvl'] = 0
                    next_b = calculate_current_bet()
                    app_state['next_bet_amount'] = next_b
                    app_state['last_alert'] = {
                        'type': 'loss',
                        'message': f'🛑 MAX KOMPENSASI HABIS! Reset ke Parlay Langkah 1 (Rp {next_b:,.0f})',
                        'ts': time.time()
                    }

            elif mm_mode == 'FLAT':
                app_state['last_alert'] = {
                    'type': 'loss',
                    'message': f'LOSS -Rp {bet:,.0f}',
                    'ts': time.time()
                }

        elif result == 'DRAW':
            app_state['session_draws'] = app_state.get('session_draws', 0) + 1
            app_state['last_alert'] = {
                'type': 'info',
                'message': f'⚖️ DRAW / IMPAS (Bet Rp {bet:,.0f} kembali)',
                'ts': time.time()
            }

        # Check TP / SL Target (Berdasarkan Net PnL Bersih Modal)
        net_pnl = float(app_state.get('session_profit', 0.0) - app_state.get('session_loss', 0.0))

        if app_state['tp_enabled'] and net_pnl >= float(app_state['tp_target']):
            app_state['bot_running'] = False
            app_state['tp_hit'] = True
            app_state['last_alert'] = {
                'type': 'tp',
                'message': f'🎯 TARGET PROFIT BERSIH TERCAPAI (+Rp {net_pnl:,.0f})! Bot Dihentikan.',
                'ts': time.time()
            }

        if app_state['sl_enabled'] and net_pnl <= -float(app_state['sl_amount_ao']):
            app_state['bot_running'] = False
            app_state['sl_hit'] = True
            app_state['last_alert'] = {
                'type': 'sl',
                'message': f'🛑 STOP LOSS BERSIH TERCAPAI (-Rp {abs(net_pnl):,.0f})! Bot Dihentikan.',
                'ts': time.time()
            }


def watch_and_resolve_deal(order_entry, direction, bet, entry_price, pair, candle_id, sec_val, deal_id, expire_at=None):
    """Background watcher yang menunggu deal selesai dan menjalankan kalkulasi MM."""
    if expire_at and float(expire_at) > 0:
        wait_time = max(2.0, float(expire_at) - time.time() + 2.0)
    else:
        wait_time = max(5.0, 60.0 - float(sec_val) + 2.0)
    time.sleep(wait_time)

    payout_pct = float(app_state.get('parlay_payout_pct', 82.0))
    for a in app_state.get('available_assets', []):
        if a.get('ric') == pair:
            payout_pct = float(a.get('payout') or payout_pct)
            break
    result = None
    profit = 0.0

    try:
        # Cek histori transaksi live dari broker bila ada session token
        if app_state.get('feed_mode') == 'LIVE' and app_state.get('authtoken') and app_state.get('device_id'):
            try:
                acc_type = app_state.get('wallet', 'DEMO').lower()
                api_dom = 'stockity.com' if app_state.get('broker') == 'Stockity' else 'binomo5.com'
                url = f"https://api.{api_dom}/bo-deals-history/v3/deals/trade?type={acc_type}&locale=en"
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(
                    url,
                    headers={
                        'authorization-token': app_state['authtoken'],
                        'device-id':           app_state['device_id'],
                        'device-type':         'web',
                        'User-Agent':          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept':              'application/json',
                    }
                )
                deadline = time.time() + 15.0
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp:
                            raw_data = resp.read().decode('utf-8', errors='replace')
                            data = json.loads(raw_data).get('data', {}).get('standard_trade_deals', [])
                            matched = None
                            if deal_id:
                                matched = next((d for d in data if str(d.get('uuid')) == str(deal_id) or str(d.get('id')) == str(deal_id)), None)
                            if not matched and data and not deal_id:
                                matched = data[0]

                            if matched:
                                st = str(matched.get('status', '')).lower()
                                if st in ('won', 'lost', 'draw', 'tie', 'refund'):
                                    if st == 'won':
                                        result = 'WIN'
                                        win_val = float(matched.get('win') or 0) / 100.0
                                        amt_val = float(matched.get('amount') or matched.get('bet') or 0) / 100.0
                                        profit = win_val - amt_val if win_val > 0 else (bet * (payout_pct / 100.0))
                                    elif st == 'lost':
                                        result = 'LOSE'
                                        profit = -bet
                                    elif st in ('draw', 'tie', 'refund'):
                                        result = 'DRAW'
                                        profit = 0.0
                                    print(f"[+] Deal {matched.get('uuid')} ({pair}) resolved: {result} (profit: Rp {profit:,.0f})")
                                    break
                    except Exception:
                        pass
                    time.sleep(1.2)
            except Exception as e:
                print(f"[!] Gagal query deals history: {e}")

        # Fallback perbandingan harga candle bila data broker belum update atau mode simulator
        if not result:
            if pair in app_state.get('pairs_data', {}):
                close_price = app_state['pairs_data'][pair].get('price', entry_price)
            else:
                close_price = app_state.get('price', entry_price)
            diff = close_price - entry_price
            if abs(diff) < 1e-6:
                result = 'DRAW'
                profit = 0.0
            elif (direction.upper() in ('BUY', 'CALL', 'UP') and diff > 0) or (direction.upper() in ('SELL', 'PUT', 'DOWN') and diff < 0):
                result = 'WIN'
                profit = bet * (payout_pct / 100.0)
            else:
                result = 'LOSE'
                profit = -bet
            print(f"[+] Deal resolved via fallback price ({pair}): {result} (profit: Rp {profit:,.0f})")

        order_entry['status'] = result
        order_entry['profit'] = profit

        # Terapkan hasil ke Money Management
        resolve_deal_outcome(result, profit, bet)

        # Segera refresh saldo broker langsung ke UI
        threading.Thread(target=refresh_balance, daemon=True).start()

        # Catat ke CSV
        threading.Thread(
            target=lambda: append_signal_csv(
                {'timestamp': order_entry['timestamp'], 'candle_id': candle_id, 'second': sec_val,
                 'price': entry_price, 'direction': direction, 'pattern': order_entry.get('pattern', ''),
                 'reason': result, 'mode': app_state.get('feed_mode')},
                order_entry
            ),
            daemon=True
        ).start()

    finally:
        # Lepas flag order in-flight agar order berikutnya PASTI bisa masuk
        with lock:
            app_state['active_order_running'] = False


# ─── Auto-Order Execution Helper (Single & Multi-Pair) ────────────────────────
_last_ordered_deals = {}  # (pair, candle_id) -> timestamp

def execute_auto_order(pair: str, candle_id: int, sec: float, price: float, sig_found: dict, mode: str) -> bool:
    """Mengeksekusi order otomatis (First Come First Served) dengan perlindungan Money Management."""
    with lock:
        if not app_state.get('auto_order_enabled'):
            return False
        if app_state.get('tp_hit') or app_state.get('sl_hit'):
            return False
        if app_state.get('active_order_running', False):
            # Order lain sedang berjalan di market, tunggu sampai selesai demi keamanan MM
            return False

        # Hindari double-order di candle/menit yang sama untuk pair yang sama
        if (pair, candle_id) in _last_ordered_deals:
            return False

        _last_ordered_deals[(pair, candle_id)] = time.time()
        if len(_last_ordered_deals) > 200:
            for k in list(_last_ordered_deals.keys())[:-100]:
                _last_ordered_deals.pop(k, None)

        app_state['last_ordered_candle'] = candle_id
        app_state['active_order_running'] = True
        global _order_flag_set_at
        _order_flag_set_at = time.time()

        direction = sig_found.get('direction', 'BUY')
        bet = calculate_current_bet()
        app_state['next_bet_amount'] = bet
        entry_p = price
        c_id = candle_id
        sec_at_order = sec
        sig_copy = {
            'timestamp': time.strftime('%H:%M:%S'),
            'candle_id': candle_id,
            'second':    sec,
            'price':     price,
            'direction': direction,
            'pattern':   sig_found.get('pattern', 'P1'),
            'reason':    sig_found.get('reason', ''),
            'mode':      mode,
            'pair':      pair
        }

    def _do_order(direction=direction, bet=bet, pair=pair, entry_price=entry_p, sig=sig_copy, cid=c_id, sec_val=sec_at_order):
        order_res = None
        try:
            order_res = place_order(direction, bet, pair)
            deal_id = order_res.get('deal_id')
            deal_exp = order_res.get('expire_at')
            if not deal_id and order_res.get('ok') and isinstance(order_res.get('data'), dict):
                deal_data = order_res.get('data', {}).get('data', {})
                if isinstance(deal_data, dict):
                    deal_id = deal_data.get('id') or deal_data.get('uuid')

            err_msg = order_res.get('error', '')
            if not order_res.get('ok'):
                if 'deal_expire_at' in err_msg or 'deal_asset' in err_msg:
                    err_msg = f"PASAR TUTUP: {pair} tutup di broker (gunakan OTC saat sesi tutup)"

            order_entry = {
                'timestamp': time.strftime('%H:%M:%S'),
                'direction': direction,
                'amount':    bet,
                'pair':      pair,
                'pattern':   sig.get('pattern'),
                'ok':        order_res.get('ok'),
                'deal_id':   deal_id,
                'status':    'RUNNING' if order_res.get('ok') else 'REJECTED',
                'profit':    0,
                'error':     err_msg,
            }

            with lock:
                app_state['orders_history'].insert(0, order_entry)
                if len(app_state['orders_history']) > 100:
                    app_state['orders_history'].pop()

            if order_res.get('ok'):
                threading.Thread(
                    target=watch_and_resolve_deal,
                    args=(order_entry, direction, bet, entry_price, pair, cid, sec_val, deal_id, deal_exp),
                    daemon=True
                ).start()
                # Flag akan direset oleh watch_and_resolve_deal di blok finally-nya
                return
            else:
                with lock:
                    app_state['last_alert'] = {
                        'type': 'info',
                        'message': f"⚠️ Order {pair} ({app_state.get('wallet','DEMO')}) gagal: {err_msg[:120]}",
                        'ts': time.time()
                    }
                threading.Thread(
                    target=lambda: append_signal_csv(sig, order_entry),
                    daemon=True
                ).start()
        except Exception as ex:
            print(f"[!] _do_order exception ({pair}): {ex}")
        finally:
            # Safety net: selalu reset flag jika order TIDAK dilanjutkan ke watcher
            if order_res is None or not (order_res.get('ok') if order_res else False):
                with lock:
                    app_state['active_order_running'] = False

    threading.Thread(target=_do_order, daemon=True).start()
    return True


# ─── Tick Processing (Main Pair) ─────────────────────────────────────────────
def feed_tick(price, sec=None, candle_id=None):
    with lock:
        if sec is None:
            sec = round(time.time() % 60, 1)
        if candle_id is None:
            candle_id = int(time.time() // 60 * 60)

        # Reset kalau menit baru
        if candle_id != app_state['candle_id'] or sec < app_state['second']:
            app_state['candle_id']  = candle_id
            app_state['open_price'] = price
            app_state['high_price'] = price
            app_state['low_price']  = price
            app_state['ticks_history'].clear()

        app_state['second']     = sec
        app_state['price']      = price
        app_state['high_price'] = max(app_state['high_price'], price)
        app_state['low_price']  = min(app_state['low_price'],  price)
        app_state['tick_count'] += 1

        bot_run = app_state.get('bot_running', False)
        pair_val = app_state.get('pair', 'Z-CRY/IDX')
        curr_candle_id = app_state.get('candle_id', candle_id)
        feed_m = app_state.get('feed_mode', 'SIMULATOR')

    # Proses engine kalau bot START (DI LUAR LOCK agar tidak menyebabkan deadlock)
    if bot_run:
        try:
            res_raw = android_bridge.tick_pair(pair_val, curr_candle_id, sec, price)
            res = json.loads(res_raw)
        except Exception:
            res = {}

        try:
            pc_raw = android_bridge.pc_tick(pair_val, curr_candle_id, sec, price)
            pc = json.loads(pc_raw)
        except Exception:
            pc = {}

        sig_found = None
        if 'signal' in pc and pc['signal']:
            sig_found = pc['signal']
        elif 'signal' in res and res['signal']:
            sig_found = res['signal']

        sig_entry = None
        with lock:
            app_state['step']      = pc.get('step',      app_state['step'])
            app_state['crossings'] = pc.get('crossings', app_state['crossings'])
            app_state['side']      = pc.get('side',      app_state['side'])
            app_state['one_way']   = pc.get('one_way',   app_state['one_way'])
            app_state['reason']    = pc.get('reason',    app_state['reason'])

            if sig_found:
                sig_entry = {
                    'timestamp': time.strftime('%H:%M:%S'),
                    'candle_id': curr_candle_id,
                    'second':    sec,
                    'price':     price,
                    'direction': sig_found.get('direction', 'BUY'),
                    'pattern':   sig_found.get('pattern',   'P1'),
                    'reason':    sig_found.get('reason',    ''),
                    'mode':      feed_m,
                    'pair':      pair_val,
                }
                app_state['last_signal'] = sig_entry
                app_state['signals_history'].insert(0, sig_entry)
                if len(app_state['signals_history']) > 200:
                    app_state['signals_history'].pop()

        if sig_found and sig_entry:
            # Eksekusi auto-order DI LUAR LOCK
            ordered = execute_auto_order(pair_val, curr_candle_id, sec, price, sig_found, feed_m)
            if not ordered:
                threading.Thread(
                    target=lambda s=sig_entry: append_signal_csv(s),
                    daemon=True
                ).start()

    with lock:
        app_state['ticks_history'].append({
            'sec': sec, 'price': price, 'side': app_state['side']
        })
        if len(app_state['ticks_history']) > 120:
            app_state['ticks_history'].pop(0)


# ─── Live Feed Callbacks ─────────────────────────────────────────────────────
def _on_live_tick(tick: dict):
    feed_tick(
        price     = tick['price'],
        sec       = tick['second'],
        candle_id = tick['candle_id'],
    )

def _on_feed_status(status: str):
    with lock:
        app_state['feed_status'] = status
        if status.startswith('ERROR') or status.startswith('CONNECTION'):
            app_state['feed_error'] = status
        elif status == 'CONNECTED':
            app_state['feed_error'] = ''


# ─── Simulator (fallback kalau mode SIMULATOR) ───────────────────────────────
def market_simulator():
    p     = 1000.0
    sec   = 0.0
    trend = 0.15
    while app_state['feed_mode'] == 'SIMULATOR':
        sec += 0.5
        if sec >= 60.0:
            sec = 0.0
            p   = round(p, 2)
            trend = random.choice([0.18, -0.18, 0.08, -0.08])
        noise = (random.random() - 0.5) * 0.4
        if 20 <= sec <= 28:
            p += (trend * 1.6 if random.random() < 0.45 else -trend * 0.7) + noise
        else:
            p += trend * 0.3 + noise
        p = round(max(10.0, p), 2)
        feed_tick(p, round(sec, 1))
        time.sleep(max(0.05, 0.5 / app_state['speed']))


def start_simulator():
    global _sim_thread
    if _sim_thread and _sim_thread.is_alive():
        return
    _sim_thread = threading.Thread(target=market_simulator, daemon=True, name='Simulator')
    _sim_thread.start()


# ─── Feed Manager ─────────────────────────────────────────────────────────────
def start_live_feed(broker, authtoken, device_id, pair):
    global _live_feed
    if _live_feed:
        _live_feed.stop()
        _live_feed = None

    app_state['feed_mode']   = 'LIVE'
    app_state['feed_status'] = 'CONNECTING...'
    app_state['tick_count']  = 0

    _live_feed = LiveFeed(
        broker    = broker,
        authtoken = authtoken,
        device_id = device_id,
        pair      = pair,
        on_tick   = _on_live_tick,
        on_status = _on_feed_status,
        reconnect = True,
    )
    _live_feed.start()

    # Warm up Phoenix order client untuk eksekusi auto-order instan
    try:
        get_order_client(broker=broker, authtoken=authtoken, device_id=device_id, pair=pair)
    except Exception:
        pass


def stop_live_feed():
    global _live_feed
    if _live_feed:
        _live_feed.stop()
        _live_feed = None
    app_state['feed_mode']   = 'SIMULATOR'
    app_state['feed_status'] = 'IDLE'
    start_simulator()


def refresh_balance():
    token  = app_state.get('authtoken')
    dev_id = app_state.get('device_id')
    broker = app_state.get('broker', 'Binomo')
    if token and dev_id:
        try:
            res = fetch_balances(broker, token, dev_id)
            if res.get('ok'):
                with lock:
                    app_state['balance_demo'] = res['demo']
                    app_state['balance_real'] = res['real']
                    app_state['currency']     = res.get('currency', 'IDR')
                return True
        except Exception:
            pass
    return False


def balance_poller():
    while True:
        refresh_balance()
        time.sleep(12)


# ─── Watchdog: auto-reset active_order_running jika macet > 120 detik ────────
_order_flag_set_at = 0.0  # timestamp saat flag diset True

def _order_watchdog():
    """Thread watchdog: kalau active_order_running macet > 120s, paksa reset."""
    global _order_flag_set_at
    while True:
        time.sleep(10)
        try:
            with lock:
                running = app_state.get('active_order_running', False)
                if running:
                    if _order_flag_set_at == 0.0:
                        _order_flag_set_at = time.time()
                    elif time.time() - _order_flag_set_at > 120:
                        print("[!] WATCHDOG: active_order_running macet > 120s -> paksa reset!")
                        app_state['active_order_running'] = False
                        _order_flag_set_at = 0.0
                else:
                    _order_flag_set_at = 0.0
        except Exception:
            pass


def auto_ensure_credentials() -> bool:
    """Memeriksa apakah ada kredensial akun tersimpan; verifikasi saldo jika ada."""
    cfg = load_session_config()
    token = cfg.get('authtoken') or app_state.get('authtoken')
    dev_id = cfg.get('device_id') or app_state.get('device_id')
    broker = cfg.get('broker') or app_state.get('broker', 'Binomo')
    email = cfg.get('email') or app_state.get('email', '')
    password = cfg.get('password') or ''

    # 1. Tes keaktifan token jika user sudah pernah login sebelumnya
    if token and dev_id:
        try:
            res = fetch_balances(broker, token, dev_id)
            if res.get('ok'):
                print(f"[+] Kredensial aktif terverifikasi!")
                if email: print(f"    Akun       : {email}")
                print(f"    Saldo Demo : Rp {res['demo']:,.0f}")
                print(f"    Saldo Real : Rp {res['real']:,.0f}")
                with lock:
                    app_state['authtoken'] = token
                    app_state['device_id'] = dev_id
                    app_state['broker'] = broker
                    if email: app_state['email'] = email
                    if cfg.get('user_id'): app_state['user_id'] = cfg.get('user_id')
                    app_state['balance_demo'] = res['demo']
                    app_state['balance_real'] = res['real']
                    app_state['currency'] = res.get('currency', 'IDR')
                    app_state['logged_in'] = True
                    app_state['feed_status'] = 'CONNECTED'
                try:
                    GATE.set_session({
                        'broker': broker.lower(),
                        'user_id': app_state.get('user_id', '0'),
                        'token': token,
                        'device_id': dev_id,
                    })
                except Exception:
                    pass
                threading.Thread(target=fetch_broker_assets_list, daemon=True).start()
                return True
        except Exception:
            pass

    # 2. Jika ada email & password tersimpan, coba login otomatis
    if email and password:
        print(f"[*] Melakukan login otomatis ke {broker} ({email})...")
        login_res = broker_login(broker, email, password)
        if login_res.get('ok'):
            token = login_res['authtoken']
            dev_id = login_res['device_id']
            u_id = login_res.get('user_id', '')
            with lock:
                app_state['authtoken'] = token
                app_state['device_id'] = dev_id
                app_state['user_id'] = u_id
                app_state['email'] = email
                app_state['broker'] = broker
                app_state['logged_in'] = True

            save_session_config({
                'broker': broker,
                'email': email,
                'password': password,
                'authtoken': token,
                'device_id': dev_id,
                'user_id': u_id,
                'pair': app_state.get('pair', 'Z-CRY/IDX')
            })

            try:
                GATE.set_session({
                    'broker': broker.lower(),
                    'user_id': u_id or '0',
                    'token': token,
                    'device_id': dev_id,
                })
            except Exception:
                pass

            bal = fetch_balances(broker, token, dev_id)
            if bal.get('ok'):
                with lock:
                    app_state['balance_demo'] = bal['demo']
                    app_state['balance_real'] = bal['real']
                    app_state['currency'] = bal.get('currency', 'IDR')

            print(f"[+] Login BERHASIL!")
            threading.Thread(target=fetch_broker_assets_list, daemon=True).start()
            return True

    # 3. Akun baru (belum ada data login) -> masuk Mode Simulator dengan aman
    print("[*] Belum ada kredensial akun tersimpan.")
    print("[*] Mode Simulator diaktifkan secara otomatis.")
    print("    -> Silakan login akun Binomo / Stockity di tab KONEKSI pada dashboard.")
    return False


# ─── Multi-pair Engine ────────────────────────────────────────────────────────
class MultiPairEngine:
    def tick(self, pair: str, price: float, sec: float, candle_id: int):
        with lock:
            if pair not in app_state['pairs_data']:
                app_state['pairs_data'][pair] = {
                    'price': price, 'last_signal': None,
                    'status': 'LIVE', 'tick_count': 0
                }
            app_state['pairs_data'][pair]['price'] = price
            app_state['pairs_data'][pair]['status'] = 'LIVE'
            app_state['pairs_data'][pair]['tick_count'] = \
                app_state['pairs_data'][pair].get('tick_count', 0) + 1

            if pair == app_state.get('pair') and app_state.get('feed_mode') != 'LIVE':
                app_state['tick_count'] += 1
                app_state['price'] = price
                app_state['second'] = sec

        # Jika bot sedang START, analisis pola strategi untuk pair ini secara real-time
        if app_state.get('bot_running'):
            try:
                res_raw = android_bridge.tick_pair(pair, candle_id, sec, price)
                res = json.loads(res_raw)
            except Exception:
                res = {}

            try:
                pc_raw = android_bridge.pc_tick(pair, candle_id, sec, price)
                pc = json.loads(pc_raw)
            except Exception:
                pc = {}

            sig_found = None
            if 'signal' in pc and pc['signal']:
                sig_found = pc['signal']
            elif 'signal' in res and res['signal']:
                sig_found = res['signal']

            if sig_found:
                sig_entry = {
                    'timestamp': time.strftime('%H:%M:%S'),
                    'candle_id': candle_id,
                    'second':    sec,
                    'price':     price,
                    'direction': sig_found.get('direction', 'BUY'),
                    'pattern':   sig_found.get('pattern',   'P1'),
                    'reason':    sig_found.get('reason',    ''),
                    'mode':      'MULTI_PAIR',
                    'pair':      pair
                }
                with lock:
                    if pair in app_state['pairs_data']:
                        app_state['pairs_data'][pair]['last_signal'] = sig_found
                    app_state['signals_history'].insert(0, sig_entry)
                    if len(app_state['signals_history']) > 200:
                        app_state['signals_history'].pop()

                # Eksekusi Auto-Order otomatis (First Come First Served)
                ordered = execute_auto_order(pair, candle_id, sec, price, sig_found, 'MULTI_PAIR')
                if not ordered:
                    threading.Thread(
                        target=lambda s=sig_entry: append_signal_csv(s),
                        daemon=True
                    ).start()

_multi_engine = MultiPairEngine()
_multi_feed_client = None  # Single unified LiveFeed instance for all multi pairs


def start_multi_feeds(pairs: list):
    global _multi_feed_client, _multi_feeds
    if _multi_feed_client:
        try:
            _multi_feed_client.stop()
        except Exception:
            pass
        _multi_feed_client = None

    for f in list(_multi_feeds.values()):
        try: f.stop()
        except Exception: pass
    _multi_feeds.clear()

    clean_pairs = [str(p).strip() for p in pairs if str(p).strip()]
    if not clean_pairs:
        with lock:
            app_state['multi_pair_mode'] = False
            app_state['pairs_watching']  = []
            app_state['pairs_data']      = {}
        return

    with lock:
        app_state['multi_pair_mode'] = True
        app_state['pairs_watching']  = clean_pairs
        for p in clean_pairs:
            if p not in app_state['pairs_data']:
                app_state['pairs_data'][p] = {
                    'price': 0.0, 'last_signal': None,
                    'status': 'CONNECTING...', 'tick_count': 0
                }
            else:
                app_state['pairs_data'][p]['status'] = 'CONNECTING...'

    def _on_multi_tick(tick: dict):
        p = tick.get('pair')
        if p and p in clean_pairs:
            _multi_engine.tick(p, tick['price'], tick['second'], tick['candle_id'])

    def _on_multi_status(status: str):
        with lock:
            for p in clean_pairs:
                if p in app_state['pairs_data']:
                    app_state['pairs_data'][p]['status'] = status

    # Buka 1 koneksi WebSocket tunggal yang melanggan semua pair secara efisien
    feed = LiveFeed(
        broker    = app_state.get('broker', 'Binomo'),
        authtoken = app_state.get('authtoken', ''),
        device_id = app_state.get('device_id', ''),
        pair      = clean_pairs,
        on_tick   = _on_multi_tick,
        on_status = _on_multi_status,
        reconnect = True,
    )
    feed.start()
    _multi_feed_client = feed
    _multi_feeds['__unified__'] = feed


# ══════════════════════════════════════════════════════════════════════════════
#  HTML Dashboard (v3)
# ══════════════════════════════════════════════════════════════════════════════
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>TRADER B7PRO BOT</title>
  <style>
    :root {
      --bg: #050507; --panel: #0E0F14; --panel2: #14151B; --edge: #29232D;
      --red: #E11C40; --red2: #7E1530; --red-active: #9D1734;
      --white: #F3F0F4; --dim: #87818E; --green: #38D4A1; --gold: #BFA27A;
    }
    * { box-sizing: border-box; margin: 0; padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        -webkit-tap-highlight-color: transparent; }
    body { background: var(--bg); color: var(--white); display: flex; justify-content: center; min-height: 100vh; }
    .app-container { width: 100%; max-width: 480px; background: var(--bg); display: flex;
                     flex-direction: column; min-height: 100vh;
                     border-left: 1px solid #1a1a24; border-right: 1px solid #1a1a24; }

    /* TOP BAR */
    .top-bar { display: flex; justify-content: space-between; align-items: center;
               padding: 14px 16px; border-bottom: 1px solid var(--edge); background: var(--panel); }
    .broker-pill { display: flex; align-items: center; gap: 8px; background: var(--panel2);
                   border: 1px solid var(--edge); padding: 5px 12px; border-radius: 9px; cursor: pointer; }
    .broker-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gold); }
    .broker-name { font-size: 13px; font-weight: 800; letter-spacing: 0.5px; }
    .wallet-pill { display: flex; align-items: center; gap: 6px; background: #191c25;
                   border: 1px solid #2e3547; padding: 5px 12px; border-radius: 9px; cursor: pointer; }
    .wallet-pill.real { background: #280e15; border-color: var(--red); color: var(--red); }
    .wallet-label { font-size: 11px; font-weight: 800; letter-spacing: 1px; }

    /* FEED STATUS BAR */
    .feed-bar { display: flex; align-items: center; justify-content: space-between;
                padding: 7px 16px; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
                border-bottom: 1px solid var(--edge); background: #090a0f; }
    .feed-dot { width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
    .feed-dot.live    { background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 1.5s infinite; }
    .feed-dot.sim     { background: var(--gold); }
    .feed-dot.conn    { background: #5577ff; animation: pulse 1s infinite; }
    .feed-dot.err     { background: var(--red); }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
    .feed-label { display: flex; align-items: center; color: var(--dim); }
    .feed-label span.hl { color: var(--white); margin-left: 4px; }
    .feed-btn { font-size: 10px; font-weight: 800; padding: 3px 10px; border-radius: 6px;
                border: 1px solid var(--edge); background: var(--panel2); color: var(--dim); cursor: pointer; }
    .feed-btn:hover { color: var(--white); }

    /* BALANCE CARD */
    .balance-card { margin: 12px 16px; padding: 14px 16px; background: var(--panel);
                    border: 1px solid var(--edge); border-radius: 13px;
                    display: flex; justify-content: space-between; align-items: center; }
    .balance-title { font-size: 10px; font-weight: 700; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; }
    .balance-amount { font-size: 22px; font-weight: 900; color: #fff; margin-top: 4px; font-family: monospace; }
    .vip-badge { font-size: 9px; font-weight: 800; background: #261f14; color: var(--gold);
                 border: 1px solid #4a3b22; padding: 3px 8px; border-radius: 6px; letter-spacing: 1px; }
    .tick-badge { font-size: 9px; font-weight: 700; color: var(--dim); margin-top: 4px; }

    /* SELECTORS */
    .selector-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 0 16px 12px 16px; }
    .select-box { background: var(--panel2); border: 1px solid var(--edge); border-radius: 11px;
                  padding: 10px 12px; display: flex; flex-direction: column; cursor: pointer; }
    .select-label { font-size: 9px; font-weight: 700; color: var(--dim); text-transform: uppercase; margin-bottom: 4px; }
    .select-val { font-size: 13px; font-weight: 800; color: var(--white); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* CHART */
    .chart-section { margin: 0 16px 14px 16px; background: var(--panel); border: 1px solid var(--edge);
                     border-radius: 13px; padding: 12px; position: relative; }
    .chart-header { display: flex; justify-content: space-between; font-size: 11px; color: var(--dim);
                    font-weight: 700; margin-bottom: 8px; }
    canvas { width: 100%; height: 190px; display: block; border-radius: 8px; background: #08090d; }
    .second-bar-wrap { width: 100%; height: 4px; background: #1c1d26; border-radius: 2px; margin-top: 10px; overflow: hidden; }
    .second-bar-fill { height: 100%; width: 0%; background: var(--green); transition: width 0.2s linear; }

    /* SIGNAL CARD */
    .signal-card { margin: 0 16px 14px 16px; padding: 14px; background: var(--panel);
                   border: 1.5px solid var(--edge); border-radius: 13px; text-align: center; transition: 0.3s; }
    .signal-card.buy  { border-color: var(--green); background: #072116; }
    .signal-card.sell { border-color: var(--red);   background: #240b15; }
    .sig-status-txt { font-size: 11px; font-weight: 800; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; }
    .sig-action-txt { font-size: 32px; font-weight: 900; letter-spacing: 1.5px; margin: 4px 0; }
    .sig-action-txt.buy  { color: var(--green); }
    .sig-action-txt.sell { color: var(--red); }
    .sig-reason-txt { font-size: 11px; color: var(--dim); }

    /* METRICS */
    .metrics-strip { display: flex; justify-content: space-between; margin: 0 16px 14px 16px;
                     padding: 10px 14px; background: var(--panel2); border: 1px solid var(--edge);
                     border-radius: 11px; font-size: 11px; }
    .metric-col { display: flex; flex-direction: column; gap: 2px; }
    .metric-lbl { font-size: 9px; color: var(--dim); font-weight: 700; }
    .metric-v { font-weight: 800; }

    /* ACTION BUTTONS */
    .btn-row { display: flex; gap: 10px; margin: 0 16px 20px 16px; }
    .btn-main { flex: 1; height: 50px; border-radius: 12px; font-size: 14px; font-weight: 900;
                letter-spacing: 1px; border: 1px solid var(--edge); cursor: pointer;
                display: flex; align-items: center; justify-content: center; transition: 0.15s; }
    .btn-start { background: var(--red); color: #fff; border-color: var(--red);
                 box-shadow: 0 4px 14px rgba(225,28,64,0.35); }
    .btn-start:active { background: var(--red-active); }
    .btn-stop  { background: var(--panel2); color: var(--dim); border-color: var(--edge); }
    .btn-stop.active  { background: #262633; color: var(--white); }

    /* BOTTOM NAV */
    .bottom-nav { margin-top: auto; display: flex; background: var(--panel);
                  border-top: 1px solid var(--edge); padding: 8px 10px 14px 10px; }
    .nav-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px;
                padding: 6px; border-radius: 8px; cursor: pointer; color: var(--dim); text-decoration: none; }
    .nav-item.active { color: var(--white); }
    .nav-icon { font-size: 18px; }
    .nav-txt  { font-size: 10px; font-weight: 700; letter-spacing: 0.5px; }

    /* VIEW CONTAINERS */
    .view-content { display: none; flex-direction: column; flex: 1; overflow-y: auto; padding-bottom: 10px; }
    .view-content.active { display: flex; }

    /* HISTORY VIEW */
    .hist-item { margin: 0 16px 8px 16px; padding: 12px 14px; background: var(--panel);
                 border: 1px solid var(--edge); border-radius: 11px; display: flex;
                 justify-content: space-between; align-items: center; }
    .hist-dir { font-size: 13px; font-weight: 900; }
    .hist-dir.buy  { color: var(--green); }
    .hist-dir.sell { color: var(--red); }
    .hist-info { font-size: 10px; color: var(--dim); margin-top: 2px; }
    .hist-time { font-size: 11px; color: var(--dim); font-weight: 700; }

    /* CONNECT VIEW (settings) */
    .connect-panel { margin: 12px 16px; padding: 16px; background: var(--panel);
                     border: 1px solid var(--edge); border-radius: 13px; }
    .connect-title { font-size: 12px; font-weight: 900; color: var(--gold); letter-spacing: 1px;
                     text-transform: uppercase; margin-bottom: 14px; }
    .input-group { margin-bottom: 12px; }
    .input-label { font-size: 10px; font-weight: 700; color: var(--dim); text-transform: uppercase;
                   letter-spacing: 0.5px; margin-bottom: 5px; display: block; }
    .input-field { width: 100%; background: #0a0b10; border: 1px solid var(--edge); color: var(--white);
                   padding: 10px 12px; border-radius: 9px; font-size: 12px; font-family: monospace;
                   outline: none; }
    .input-field:focus { border-color: var(--red); }
    select.input-field option { background: #0a0b10; }
    .connect-hint { font-size: 10px; color: var(--dim); line-height: 1.6; margin-top: 10px; }
    .connect-hint code { color: var(--gold); background: #1a1510; padding: 1px 5px; border-radius: 4px; }
    .btn-connect { width: 100%; height: 46px; background: var(--red); color: #fff; border: none;
                   border-radius: 11px; font-size: 13px; font-weight: 900; letter-spacing: 1px; cursor: pointer; }
    .btn-disconnect { width: 100%; height: 46px; background: var(--panel2); color: var(--dim);
                      border: 1px solid var(--edge); border-radius: 11px; font-size: 12px;
                      font-weight: 800; cursor: pointer; margin-top: 8px; }
    .status-box { margin-top: 12px; padding: 10px 12px; background: #0a0b10; border: 1px solid var(--edge);
                  border-radius: 9px; font-size: 10px; font-family: monospace; color: var(--dim); line-height: 1.6; }

    /* MODAL */
    .modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
                     background: rgba(0,0,0,0.85); z-index: 999; align-items: center;
                     justify-content: center; padding: 20px; }
    .modal-card { width: 100%; max-width: 380px; background: var(--panel); border: 1px solid var(--edge);
                  border-radius: 16px; padding: 20px; text-align: center; }
    .modal-title { font-size: 15px; font-weight: 900; color: var(--red); letter-spacing: 1px; margin-bottom: 8px; }
    .modal-body  { font-size: 12px; color: var(--dim); line-height: 1.5; margin-bottom: 18px; }
    .modal-btns  { display: flex; gap: 10px; }
    .m-btn       { flex: 1; padding: 12px; border-radius: 10px; font-size: 12px; font-weight: 800; border: none; cursor: pointer; }
    .m-cancel  { background: var(--panel2); color: var(--white); border: 1px solid var(--edge); }
    .m-confirm { background: var(--red); color: #fff; }

    /* PAIR MODAL */
    .pair-pick-item { display: flex; justify-content: space-between; align-items: center;
                      padding: 10px 12px; background: var(--panel2); border: 1px solid var(--edge);
                      border-radius: 9px; margin-bottom: 6px; cursor: pointer; transition: 0.15s; }
    .pair-pick-item:hover { border-color: var(--gold); background: #1a1e2d; }
    .pair-badge-turbo { font-size: 9px; font-weight: 800; background: #072116; color: var(--green);
                        border: 1px solid var(--green); padding: 2px 6px; border-radius: 5px; }

    .section-title { font-size: 10px; font-weight: 800; color: var(--dim); text-transform: uppercase;
                     letter-spacing: 1px; margin: 14px 16px 8px 16px; }

    /* TOAST */
    #toast-container { position: fixed; top: 20px; right: 20px; z-index: 9999;
                       display: flex; flex-direction: column; gap: 8px; max-width: 280px; }
    .toast { padding: 10px 14px; border-radius: 10px; font-size: 12px; font-weight: 800;
             opacity: 0; transition: opacity 0.3s; letter-spacing: 0.5px; }
    .toast-info  { background: #1e2535; border: 1px solid #3a4060; color: #fff; }
    .toast-buy   { background: #072116; border: 1px solid #38D4A1; color: #38D4A1; }
    .toast-sell  { background: #240b15; border: 1px solid #E11C40; color: #E11C40; }
    .toast-win   { background: #0a2515; border: 1px solid #38D4A1; color: #38D4A1; }
    .toast-loss  { background: #200810; border: 1px solid #E11C40; color: #E11C40; }
    .toast-tp    { background: #0a2515; border: 1px solid #BFA27A; color: #BFA27A; }
    .toast-sl    { background: #200810; border: 1px solid #E11C40; color: #ff4444; }

    /* MONEY MANAGEMENT */
    .mm-status-card { margin: 0 16px 12px 16px; padding: 12px 14px; background: #0c1018;
                      border: 1px solid #1c2738; border-radius: 12px; }
    .mm-badge { font-size: 9px; font-weight: 800; padding: 3px 8px; border-radius: 6px;
                letter-spacing: 0.5px; background: #132438; color: #55aaff; border: 1px solid #204166; }
    .mm-badge.mart { background: #281117; color: #ff5577; border-color: #5c1e2b; }
  </style>
</head>
<body>
<div id="toast-container"></div>
<div class="app-container">

  <!-- TOP BAR -->
  <div class="top-bar">
    <div class="broker-pill" onclick="switchBroker()">
      <div class="broker-dot"></div>
      <span class="broker-name" id="broker-name">BINOMO</span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <img src="/logo.png" style="width:24px;height:24px;border-radius:6px;object-fit:cover;box-shadow:0 0 8px rgba(59,130,246,0.6)" onerror="this.style.display='none'">
      <span style="font-size:11px;font-weight:900;color:#38bdf8;letter-spacing:1px;text-shadow:0 0 8px rgba(56,189,248,0.4)">TRADER B7PRO BOT</span>
    </div>
    <div class="wallet-pill" id="wallet-pill" onclick="toggleWallet()">
      <span class="wallet-label" id="wallet-label">DEMO</span>
    </div>
  </div>

  <!-- LIVE FEED STATUS BAR -->
  <div class="feed-bar">
    <div class="feed-label">
      <div class="feed-dot sim" id="feed-dot"></div>
      <span id="feed-mode-label">SIMULATOR</span>
      <span class="hl" id="feed-status-label">&nbsp;· IDLE</span>
    </div>
    <div style="display:flex;gap:6px;align-items:center">
      <span style="color:var(--dim);font-size:10px" id="tick-count-label">0 ticks</span>
      <button class="feed-btn" onclick="changeTab('settings')">⚙ CONNECT</button>
    </div>
  </div>

  <!-- DASHBOARD VIEW -->
  <div id="view-dashboard" class="view-content active">
    <!-- Balance -->
    <div class="balance-card">
      <div>
        <div class="balance-title">Saldo Akun</div>
        <div class="balance-amount" id="balance-amt">Rp 14.000.000</div>
        <div class="tick-badge" id="candle-label">CANDLE —</div>
      </div>
      <div>
        <div class="vip-badge" id="vip-badge">VIP</div>
        <div style="font-size:10px;color:var(--dim);margin-top:6px;text-align:right" id="price-display">1000.00</div>
      </div>
    </div>

    <!-- Pair & Strategy -->
    <div class="selector-row">
      <div class="select-box" onclick="openPairModal()" title="Klik untuk memilih dari 150 aset resmi broker">
        <div class="select-label">Pair (150 Aset)</div>
        <div class="select-val" id="pair-val">Z-CRY/IDX</div>
      </div>
      <div class="select-box" onclick="switchStrategy()">
        <div class="select-label">Strategi</div>
        <div class="select-val" id="strategy-val">OPEN PRICE</div>
      </div>
    </div>

    <!-- Chart -->
    <div class="chart-section">
      <div class="chart-header">
        <span id="chart-pair">Z-CRY/IDX</span>
        <span id="chart-time">--:--:--</span>
      </div>
      <canvas id="chart-canvas"></canvas>
      <div class="second-bar-wrap">
        <div class="second-bar-fill" id="second-bar"></div>
      </div>
    </div>

    <!-- Signal Card -->
    <div class="signal-card" id="signal-card">
      <div class="sig-status-txt" id="sig-status">MENUNGGU SINYAL</div>
      <div class="sig-action-txt" id="sig-action">—</div>
      <div class="sig-reason-txt" id="sig-reason">Bot dalam mode observasi</div>
    </div>

    <!-- Metrics -->
    <div class="metrics-strip">
      <div class="metric-col">
        <div class="metric-lbl">STEP</div>
        <div class="metric-v" id="m-step">0.00</div>
      </div>
      <div class="metric-col">
        <div class="metric-lbl">CROSSINGS</div>
        <div class="metric-v" id="m-cross">0</div>
      </div>
      <div class="metric-col">
        <div class="metric-lbl">SIDE</div>
        <div class="metric-v" id="m-side">NEUTRAL</div>
      </div>
      <div class="metric-col">
        <div class="metric-lbl">REASON</div>
        <div class="metric-v" id="m-reason" style="font-size:9px">OBSERVING</div>
      </div>
      <div class="metric-col">
        <div class="metric-lbl">SEC</div>
        <div class="metric-v" id="m-sec">0.0</div>
      </div>
    </div>

    <!-- MONEY MANAGEMENT STATUS CARD -->
    <div class="mm-status-card" id="mm-status-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="font-size:10px;font-weight:800;color:var(--gold);letter-spacing:1px">
          STRATEGI MM: <span id="mm-status-mode" style="color:#fff">COMPOUND / PARLAY</span>
        </div>
        <div id="mm-step-badge" class="mm-badge">LANGKAH 1 / 2</div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
        <div>
          <div style="font-size:9px;color:var(--dim)">OP Berikutnya:</div>
          <div id="mm-next-bet" style="font-size:16px;font-weight:900;color:var(--green);font-family:monospace">Rp 14.000</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:9px;color:var(--dim)">Siklus Sukses:</div>
          <div id="mm-cycle-wins" style="font-size:14px;font-weight:800;color:var(--gold)">0x</div>
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding-top:8px;border-top:1px solid #1c2738">
        <div>
          <div style="font-size:9px;color:var(--dim)">Total Net PnL:</div>
          <div id="mm-net-pnl" style="font-size:15px;font-weight:900;color:var(--green);font-family:monospace">Rp 0</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:9px;color:var(--dim)">Win Rate / Order:</div>
          <div id="mm-winrate" style="font-size:12px;font-weight:800;color:var(--white)">0.0% (0W / 0L)</div>
        </div>
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="btn-row">
      <button class="btn-main btn-start" id="btn-toggle" onclick="toggleBot()">▶ START</button>
      <button class="btn-main btn-stop active" onclick="resetEngine()">↺ RESET</button>
    </div>
  </div>

  <!-- HISTORY VIEW -->
  <div id="view-hist" class="view-content">
    <div class="section-title">Riwayat Sinyal</div>
    <div style="margin:0 16px 10px 16px">
      <button class="btn-connect" style="height:40px;font-size:12px" onclick="exportCSV()">📊 Export CSV</button>
    </div>
    <div id="hist-list">
      <div style="text-align:center;color:var(--dim);font-size:12px;margin-top:40px">Belum ada sinyal</div>
    </div>
  </div>

  <!-- SETTINGS / CONNECT VIEW -->
  <div id="view-sett" class="view-content">
    <div class="connect-panel">
      <div class="connect-title">🔌 Koneksi Live WebSocket</div>

      <div class="input-group">
        <label class="input-label">Broker</label>
        <select class="input-field" id="inp-broker">
          <option value="Binomo">Binomo (wss://as.binomo5.com/)</option>
          <option value="Stockity">Stockity (wss://as.stockity.com/)</option>
        </select>
      </div>

      <!-- AUTO LOGIN SECTION -->
      <div style="background:#13151f;border:1px solid #232738;border-radius:10px;padding:12px;margin-bottom:14px">
        <div style="font-size:11px;font-weight:900;color:var(--green);letter-spacing:0.5px;margin-bottom:8px">⚡ AUTO LOGIN OTOMATIS (Email &amp; Password)</div>
        <div class="input-group" style="margin-bottom:8px">
          <label class="input-label">Email Broker</label>
          <input class="input-field" id="inp-auto-email" value="" placeholder="Masukkan email akun broker">
        </div>
        <div class="input-group" style="margin-bottom:10px">
          <label class="input-label">Password Broker</label>
          <input class="input-field" id="inp-auto-pass" type="password" value="" placeholder="Masukkan password akun broker">
        </div>
        <button class="btn-connect" style="background:#1e7e34" onclick="autoLoginConnect()">⚡ LOGIN &amp; HUBUNGKAN OTOMATIS</button>
      </div>

      <div style="font-size:10px;font-weight:800;color:var(--dim);text-transform:uppercase;margin-bottom:10px">── Atau Input Manual Cookie ──</div>

      <div class="input-group">
        <label class="input-label">authtoken <span style="color:var(--red)">*</span></label>
        <input class="input-field" id="inp-authtoken" type="password" value="" placeholder="Paste authtoken dari cookie broker">
      </div>

      <div class="input-group">
        <label class="input-label">device_id <span style="color:var(--red)">*</span></label>
        <input class="input-field" id="inp-deviceid" value="" placeholder="device_id dari cookie broker">
      </div>

      <div class="input-group">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
          <label class="input-label" style="margin:0">Pair Aset Broker (150 Aset Resmi)</label>
          <button class="feed-btn" type="button" onclick="loadBrokerAssets(true)" title="Tarik data aset resmi terbaru">🔄 Tarik Aset Broker</button>
        </div>
        <select class="input-field" id="inp-pair" onchange="onPairDropdownChange()">
          <optgroup label="🔥 Populer / Crypto">
            <option value="Z-CRY/IDX" selected>Z-CRY/IDX (Crypto IDX · 82%)</option>
            <option value="BOOO-X">BOOO-X (Booo · 83%)</option>
            <option value="GHOSTY-X">GHOSTY-X (Ghosty · 83%)</option>
            <option value="BTCUSD-OTC2">BTCUSD-OTC2 (Bitcoin OTC · 80%)</option>
            <option value="ETHUSD-OTC2">ETHUSD-OTC2 (Ethereum OTC · 80%)</option>
            <option value="SOLUSD-OTC2">SOLUSD-OTC2 (Solana OTC · 80%)</option>
          </optgroup>
          <optgroup label="Forex & Currencies">
            <option value="Z-AUD/USD3">Z-AUD/USD3 (AUD/USD OTC · 80%)</option>
            <option value="CADJPY">CADJPY (CAD/JPY · 80%)</option>
            <option value="EURHUF">EURHUF (EUR/HUF · 80%)</option>
            <option value="USDMXN">USDMXN (USD/MXN · 80%)</option>
          </optgroup>
        </select>
        <div style="font-size:10px;color:var(--dim);margin-top:3px" id="asset-count-hint">
          ⚡ <i>150 aset resmi broker akan disinkronkan otomatis via API.</i>
        </div>
      </div>

      <div class="input-group">
        <label class="input-label">User ID (opsional)</label>
        <input class="input-field" id="inp-userid" placeholder="User ID akun broker">
      </div>

      <div class="input-group">
        <label class="input-label">Email (opsional)</label>
        <input class="input-field" id="inp-email" placeholder="Email akun broker">
      </div>

      <button class="btn-connect" onclick="connectLive()">🔗 CONNECT LIVE</button>
      <button class="btn-disconnect" onclick="disconnectLive()">⬛ Kembali ke Simulator</button>

      <div class="status-box" id="conn-status-box">Status: IDLE</div>

      <div class="connect-hint">
        <strong style="color:var(--gold)">Cara ambil credentials:</strong><br>
        1. Login ke <code>binomo5.com</code> atau <code>stockity.com</code> di browser<br>
        2. Buka DevTools → Application → Cookies<br>
        3. Copy nilai <code>authtoken</code> dan <code>device_id</code><br><br>
        <strong style="color:var(--gold)">Atau dari APK (Android):</strong><br>
        1. Buka BOT X APK, login ke broker<br>
        2. Gunakan ADB: <code>adb shell am broadcast ...</code><br>
        3. Atau intercept traffic dengan <code>mitmproxy</code>
      </div>
    </div>

    <!-- STRATEGI MONEY MANAGEMENT PANEL -->
    <div class="connect-panel" style="margin-top:0px">
      <div class="connect-title">💰 STRATEGI MONEY MANAGEMENT</div>

      <div class="input-group">
        <label class="input-label">Pilih Mode MM</label>
        <select class="input-field" id="inp-mm-mode" onchange="onMMModeChange()">
          <option value="HYBRID" selected>⚡🔥 GABUNGAN (Martingale + Compound/Parlay)</option>
          <option value="PARLAY">🔥 Compound / Parlay Saja (Profit Ditumpuk)</option>
          <option value="MARTINGALE">⚡ Martingale Saja (Kompensasi Saat Loss)</option>
          <option value="FLAT">➖ Flat Bet (Nominal Tetap)</option>
        </select>
      </div>

      <!-- HYBRID INFO BANNER -->
      <div id="sec-hybrid-info" style="background:#131124;border:1px solid #3d2866;border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:11px;line-height:1.5;color:#cdbeff">
        <b style="color:#f0e8ff">⚡🔥 Mode Kombinasi Martingale + Compound/Parlay:</b><br>
        • Saat <b>WIN</b>: Menjalankan <b>Compound / Parlay</b> untuk melipatgandakan keuntungan.<br>
        • Saat <b>LOSS</b>: Otomatis mengaktifkan <b>Martingale</b> untuk menutup kerugian.<br>
        • Begitu kompensasi Martingale menang, kerugian pulih dan bot otomatis kembali ke <b>Langkah 1 Parlay</b>!
      </div>

      <div class="input-group">
        <label class="input-label">Open Posisi Awal (OP Awal) - Rp</label>
        <input class="input-field" id="inp-base-bet" type="number" value="14000" oninput="updateMMPreview()">
      </div>

      <!-- PARLAY / COMPOUND SECTION -->
      <div id="sec-parlay" style="background:#0c131d;border:1px solid #1a3048;border-radius:10px;padding:12px;margin-bottom:12px">
        <div style="font-size:11px;font-weight:900;color:#55aaff;letter-spacing:0.5px;margin-bottom:8px">📈 PENGATURAN COMPOUND / PARLAY</div>
        <div class="input-group" style="margin-bottom:8px">
          <label class="input-label">Target Langkah / Level Parlay</label>
          <select class="input-field" id="inp-parlay-steps" onchange="updateMMPreview()">
            <option value="2" selected>2 Langkah (Rekomendasi - Risiko Rendah)</option>
            <option value="3">3 Langkah (Risiko Sedang)</option>
            <option value="4">4 Langkah (Risiko Tinggi)</option>
            <option value="5">5 Langkah</option>
          </select>
        </div>
        <div class="input-group" style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <label class="input-label" style="margin:0">Payout Market (%)</label>
            <label style="cursor:pointer;display:flex;align-items:center;gap:4px;font-size:10px;color:var(--cyan);font-weight:700">
              <input type="checkbox" id="chk-auto-payout" checked onchange="toggleAutoPayout(this.checked)">
              <span>⚡ Auto Broker</span>
            </label>
          </div>
          <input class="input-field" id="inp-parlay-payout" type="number" value="82" step="1" oninput="onPayoutManualInput()">
          <div style="font-size:10px;color:var(--dim);margin-top:3px" id="broker-payout-hint">
            Broker saat ini: <b style="color:var(--green)" id="broker-payout-badge">82%</b> (Terbaca otomatis dari Akun)
          </div>
        </div>
        <!-- PREVIEW PARLAY -->
        <div style="font-size:10px;font-weight:800;color:var(--dim);margin-bottom:4px">PRATINJAU SIKLUS PARLAY:</div>
        <div id="parlay-preview-box" style="background:#070b10;border:1px solid #142233;border-radius:8px;padding:10px;font-size:11px;line-height:1.6">
        </div>
      </div>

      <!-- MARTINGALE SECTION -->
      <div id="sec-martingale" style="background:#1d1115;border:1px solid #4a1e27;border-radius:10px;padding:12px;margin-bottom:12px;display:none">
        <div style="font-size:11px;font-weight:900;color:#ff5577;letter-spacing:0.5px;margin-bottom:8px">🛡️ PENGATURAN MARTINGALE (KOMPENSASI)</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
          <div>
            <label class="input-label">Perkalian (Multiplier)</label>
            <input class="input-field" id="inp-mart-mult" type="number" step="0.1" value="2.5" oninput="onMartParamChange()">
          </div>
          <div>
            <label class="input-label">Maksimal Level</label>
            <input class="input-field" id="inp-mart-levels" type="number" value="5" min="1" max="8" oninput="onMartParamChange()">
          </div>
        </div>
        <div class="input-group" style="margin-bottom:8px">
          <label class="input-label">Daftar Ladder Kompensasi (Otomatis / Kustom)</label>
          <input class="input-field" id="inp-mart-ladder" value="14000, 35000, 87500, 218750, 546875" oninput="ladderManualOverride = true">
          <div style="font-size:10px;color:var(--dim);margin-top:3px">
            ⚡ <i>Otomatis tersinkronisasi saat Multiplier / Level diubah. Anda juga bisa mengedit nominal di atas secara bebas.</i>
          </div>
          <button class="feed-btn" style="margin-top:6px" onclick="recalcMartingaleLadder()">🔄 Sinkronkan Ulang dari Perkalian</button>
        </div>
        <!-- PREVIEW MARTINGALE -->
        <div style="font-size:10px;font-weight:800;color:var(--dim);margin-bottom:4px">PRATINJAU LEVEL KOMPENSASI:</div>
        <div id="martingale-preview-box" style="background:#0f090c;border:1px solid #2e151b;border-radius:8px;padding:10px;font-size:11px;line-height:1.6">
        </div>
      </div>

      <!-- TARGET PROFIT & STOP LOSS (BERSIH) -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
        <div class="input-group">
          <label class="input-label">Target Profit Bersih (Rp)</label>
          <input class="input-field" id="inp-tp" type="number" value="500000">
        </div>
        <div class="input-group">
          <label class="input-label">Stop Loss Bersih (Rp)</label>
          <input class="input-field" id="inp-sl" type="number" value="250000">
        </div>
      </div>

      <div class="input-group">
        <label class="input-label">Status Auto-Order</label>
        <select class="input-field" id="inp-auto-order">
          <option value="true" selected>✅ Aktif (Auto Execute Order Sesuai MM)</option>
          <option value="false">❌ Nonaktif (Signal Only)</option>
        </select>
      </div>

      <div style="display:flex;gap:8px">
        <button class="btn-connect" style="flex:2;background:#1e7e34" onclick="saveMMSettings()">💾 SIMPAN PENGATURAN MM</button>
        <button class="btn-connect" style="flex:1;background:#3a2028" onclick="resetMMSession()">↺ RESET SESI</button>
      </div>

      <!-- STATUS SESI BOX -->
      <div style="margin-top:12px;padding:12px;background:#0a0b10;border:1px solid #232738;border-radius:9px;font-size:11px;color:var(--dim)">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #1a1e2d">
          <span style="font-weight:800;color:#fff">Total Net PnL (Bersih):</span>
          <span id="sess-net-pnl" style="font-size:13px;font-weight:900;font-family:monospace;color:var(--green)">Rp 0</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span>Win Rate & Order:</span>
          <span id="sess-winrate" style="color:#fff;font-weight:800">0.0% (0W / 0L / 0D)</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span>Profit Sesi (Gross):</span>
          <span id="sess-profit" style="color:var(--green);font-weight:800">Rp 0</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span>Loss Sesi (Gross):</span>
          <span id="sess-loss" style="color:var(--red);font-weight:800">Rp 0</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px">
          <span>Mode Aktif:</span>
          <span id="sess-mode" style="color:#fff;font-weight:800">PARLAY (Langkah 1/2)</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span>Bet Berikutnya:</span>
          <span id="sess-next-bet" style="color:var(--gold);font-weight:800">Rp 14.000</span>
        </div>
      </div>
    </div>

    <!-- MULTI-PAIR PANEL -->
    <div class="connect-panel" style="margin-top:0px">
      <div class="connect-title">📡 Multi-Pair Monitor</div>
      <!-- Filter toolbar -->
      <div style="display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap">
        <input id="mp-search" type="text" placeholder="🔍 Cari aset..." oninput="filterMultiPairList()" style="flex:1;min-width:0;background:var(--bg);border:1px solid var(--edge);border-radius:7px;color:var(--txt);font-size:11px;padding:5px 8px;outline:none">
        <select id="mp-cat-filter" onchange="filterMultiPairList()" style="background:var(--bg);border:1px solid var(--edge);border-radius:7px;color:var(--txt);font-size:11px;padding:5px 6px">
          <option value="">Semua Kategori</option>
          <option value="Crypto">Crypto</option>
          <option value="Currenc">Forex</option>
          <option value="Commodit">Komoditas</option>
          <option value="Equit">Saham</option>
        </select>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <label style="font-size:11px;color:var(--green);display:flex;align-items:center;gap:5px;cursor:pointer">
          <input type="checkbox" id="mp-only-open" checked onchange="filterMultiPairList()" style="accent-color:var(--green)"> Hanya Aset BUKA
        </label>
        <span style="font-size:10px;color:var(--dim)" id="mp-open-count"></span>
      </div>
      <!-- Quick select buttons -->
      <div style="display:flex;gap:5px;margin-bottom:8px;flex-wrap:wrap">
        <button onclick="selectTopN(4)" style="flex:1;font-size:10px;padding:5px 4px;background:#1a3a2a;border:1px solid var(--green);border-radius:6px;color:var(--green);cursor:pointer">🟢 Top 4 Buka</button>
        <button onclick="selectTopN(10)" style="flex:1;font-size:10px;padding:5px 4px;background:#1a2a3a;border:1px solid var(--cyan);border-radius:6px;color:var(--cyan);cursor:pointer">⭐ Top 10 Buka</button>
        <button onclick="clearMultiPairs()" style="flex:1;font-size:10px;padding:5px 4px;background:#2a1a1a;border:1px solid var(--red);border-radius:6px;color:var(--red);cursor:pointer">🗑️ Reset</button>
      </div>
      <!-- Checklist area -->
      <div id="multi-asset-checklist" style="max-height:220px;overflow-y:auto;border:1px solid var(--edge);border-radius:8px;padding:6px;margin-bottom:8px;background:var(--bg)">
        <div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">Memuat daftar aset...</div>
      </div>
      <!-- Counter & activate -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <span id="mp-counter" style="font-size:11px;color:var(--dim)">0 aset dipilih</span>
        <span id="mp-warn" style="font-size:10px;color:var(--gold);display:none">⚠️ Maks 20 dianjurkan</span>
      </div>
      <button class="btn-connect" style="background:#2a1a5a" onclick="saveMultiPairs()">📡 Aktifkan Multi-Pair</button>
    </div>
  </div>

  <!-- MULTI-PAIR VIEW -->
  <div id="view-multi" class="view-content">
    <div class="section-title">📡 Monitor Multi-Pair</div>
    <div id="multi-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 16px 14px 16px"></div>
    <div class="section-title" style="margin-top:4px">Order History</div>
    <div id="order-hist-list" style="margin:0 16px"></div>
  </div>

  <!-- BOTTOM NAV -->
  <div class="bottom-nav">
    <div class="nav-item active" id="tab-dash" onclick="changeTab('dashboard')">
      <div class="nav-icon">📊</div>
      <div class="nav-txt">DASHBOARD</div>
    </div>
    <div class="nav-item" id="tab-hist" onclick="changeTab('history')">
      <div class="nav-icon">📋</div>
      <div class="nav-txt">RIWAYAT</div>
    </div>
    <div class="nav-item" id="tab-sett" onclick="changeTab('settings')">
      <div class="nav-icon">⚙️</div>
      <div class="nav-txt">KONEKSI</div>
    </div>
    <div class="nav-item" id="tab-multi" onclick="changeTab('multi')">
      <div class="nav-icon">📡</div>
      <div class="nav-txt">MULTI</div>
    </div>
  </div>
</div>

<!-- REAL WALLET MODAL -->
<div class="modal-overlay" id="real-modal">
  <div class="modal-card">
    <div class="modal-title">⚠ AKUN REAL</div>
    <div class="modal-body">Kamu akan beralih ke akun REAL. Bot ini hanya memberikan sinyal (Signal Only). Transaksi tetap dilakukan manual oleh kamu. Lanjutkan?</div>
    <div class="modal-btns">
      <button class="m-btn m-cancel" onclick="closeRealModal()">Batal</button>
      <button class="m-btn m-confirm" onclick="confirmReal()">Lanjutkan</button>
    </div>
  </div>
</div>

<!-- PAIR SELECTION MODAL -->
<div class="modal-overlay" id="pair-modal">
  <div class="modal-card" style="max-width:440px;text-align:left;max-height:85vh;display:flex;flex-direction:column">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="modal-title" style="margin:0;color:var(--gold);font-size:14px">⚡ PILIH ASET PAIR BROKER (150 ASET)</div>
      <button class="feed-btn" onclick="closePairModal()">✕</button>
    </div>
    <div style="margin-bottom:8px">
      <input class="input-field" id="search-pair-input" placeholder="🔍 Cari nama pair atau RIC (cth: CRY, BTC, BOOO, AUD)..." oninput="filterPairModal(this.value)" style="margin-bottom:6px">
      <div style="display:flex;gap:6px;overflow-x:auto;padding-bottom:4px">
        <button class="feed-btn" onclick="filterPairCategory('ALL')">Semua</button>
        <button class="feed-btn" onclick="filterPairCategory('Crypto')">Crypto</button>
        <button class="feed-btn" onclick="filterPairCategory('Currencies')">Forex</button>
        <button class="feed-btn" onclick="filterPairCategory('Commodities')">Komoditas</button>
        <button class="feed-btn" onclick="filterPairCategory('Equities')">Saham</button>
      </div>
    </div>
    <div id="pair-modal-list" style="flex:1;overflow-y:auto;max-height:50vh;padding-right:4px">
      <!-- Diisi otomatis oleh JavaScript -->
    </div>
    <div style="margin-top:10px;display:flex;justify-content:space-between;align-items:center">
      <button class="feed-btn" onclick="loadBrokerAssets(true)">🔄 Segarkan dari Broker</button>
      <button class="m-btn m-cancel" style="padding:8px 16px;width:auto;display:inline-block" onclick="closePairModal()">Tutup</button>
    </div>
  </div>
</div>

<script>
// ── Toast System ─────────────────────────────────────────────────────────────
function showToast(msg, type='info', duration=3000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = msg;
  document.getElementById('toast-container').appendChild(toast);
  setTimeout(() => toast.style.opacity = '1', 10);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 400); }, duration);
}

// ── Audio Beep ────────────────────────────────────────────────────────────────
let audioCtx = null;
function getAudioCtx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  return audioCtx;
}
function beep(freq=880, dur=0.3, vol=0.3) {
  try {
    const ctx = getAudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = 'sine';
    gain.gain.setValueAtTime(vol, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + dur);
  } catch(e) {}
}
function beepBuy()  { beep(880, 0.25, 0.3); setTimeout(() => beep(1100, 0.2, 0.2), 280); }
function beepSell() { beep(440, 0.25, 0.3); setTimeout(() => beep(330, 0.2, 0.2), 280); }
function beepTP()   { [0,300,600].forEach(d => setTimeout(() => beep(880+d*0.5, 0.2, 0.3), d)); }
function beepSL()   { [0,300,600].forEach(d => setTimeout(() => beep(880-d*0.5, 0.2, 0.3), d)); }

// ── State & Polling ──────────────────────────────────────────────────────────
let state = {};
let currentTab = 'dashboard';
const canvas = document.getElementById('chart-canvas');
const ctx = canvas.getContext('2d');
let lastAlertTs = 0;
let lastSigId   = '';

function fmt(n) {
  const cur = (state && state.currency === 'USD') ? '$ ' : 'Rp ';
  return cur + Number(n || 0).toLocaleString('id-ID');
}

setInterval(poll, 400);
setInterval(updateClock, 500);
poll();

// Multi-pair poller (every 1s, only when multi tab active)
setInterval(async () => {
  if (currentTab !== 'multi') return;
  try {
    const r = await fetch('/api/multi_state');
    const d = await r.json();
    renderMultiGrid(d);
  } catch(e) {}
}, 1000);

// Order history poller (every 2s, only when multi tab active)
setInterval(async () => {
  if (currentTab !== 'multi') return;
  try {
    const r = await fetch('/api/orders_history');
    const d = await r.json();
    renderOrderHistory(d);
  } catch(e) {}
}, 2000);

async function poll() {
  try {
    const r = await fetch('/api/state');
    state = await r.json();
    render(state);
  } catch(e) {}
}

function updateClock() {
  const now = new Date();
  const hms = now.toTimeString().slice(0,8);
  document.getElementById('chart-time').textContent = hms;
}

function render(d) {
  // Top bar
  document.getElementById('broker-name').textContent = (d.broker||'BINOMO').toUpperCase();
  const wp = document.getElementById('wallet-pill');
  wp.className = 'wallet-pill' + (d.wallet === 'REAL' ? ' real' : '');
  document.getElementById('wallet-label').textContent = d.wallet || 'DEMO';

  // Feed status bar
  const mode   = d.feed_mode   || 'SIMULATOR';
  const status = d.feed_status || 'IDLE';
  const dot    = document.getElementById('feed-dot');
  dot.className = 'feed-dot ' + (
    mode === 'LIVE' && status === 'CONNECTED' ? 'live' :
    mode === 'LIVE' && status.startsWith('CONN') ? 'conn' :
    mode === 'LIVE' && status.startsWith('ERROR') ? 'err' : 'sim'
  );
  document.getElementById('feed-mode-label').textContent = mode;
  document.getElementById('feed-status-label').textContent = ' · ' + status;
  document.getElementById('tick-count-label').textContent = (d.tick_count||0) + ' ticks';

  // Balance
  const bal = d.wallet === 'REAL' ? d.balance_real : d.balance_demo;
  document.getElementById('balance-amt').textContent = fmt(bal);

  // Candle
  const cd = d.candle_id ? new Date(d.candle_id * 1000).toISOString().slice(11,16) : '--:--';
  document.getElementById('candle-label').textContent = 'CANDLE ' + cd;

  // Price
  document.getElementById('price-display').textContent = (d.price||0).toFixed(4);

  // Pair & Strategy
  document.getElementById('pair-val').textContent   = d.pair    || 'Z-CRY/IDX';
  document.getElementById('chart-pair').textContent = d.pair    || 'Z-CRY/IDX';
  const strat = (d.strategy||'OPEN_PRICE_PATTERN').replace('_',' ').replace('PATTERN','').trim();
  document.getElementById('strategy-val').textContent = strat;

  // Second bar
  document.getElementById('second-bar').style.width = ((d.second||0) / 60 * 100) + '%';

  // Signal
  const sc = document.getElementById('signal-card');
  const sig = d.last_signal;
  if (sig && d.bot_running) {
    const dir = (sig.direction||'').toUpperCase();
    sc.className = 'signal-card ' + (dir === 'BUY' ? 'buy' : 'sell');
    document.getElementById('sig-status').textContent = '⚡ SINYAL TERDETEKSI';
    document.getElementById('sig-action').className   = 'sig-action-txt ' + (dir === 'BUY' ? 'buy' : 'sell');
    document.getElementById('sig-action').textContent = dir === 'BUY' ? '▲ NAIK' : '▼ TURUN';
    document.getElementById('sig-reason').textContent = sig.pattern + ' · ' + sig.reason + ' · ' + sig.second + 's';
  } else {
    sc.className = 'signal-card';
    document.getElementById('sig-status').textContent = d.bot_running ? 'MENGAMATI PASAR' : 'BOT BERHENTI';
    document.getElementById('sig-action').className   = 'sig-action-txt';
    document.getElementById('sig-action').textContent = '—';
    document.getElementById('sig-reason').textContent = d.bot_running ? d.reason||'OBSERVING' : 'Tekan START untuk memulai';
  }

  // Bot button
  const btn = document.getElementById('btn-toggle');
  if (d.bot_running) {
    btn.textContent = '⬛ STOP';
    btn.style.background = '#3a1020';
    btn.style.borderColor = '#7e1530';
    btn.style.boxShadow   = 'none';
  } else {
    btn.textContent = '▶ START';
    btn.style.background = 'var(--red)';
    btn.style.borderColor = 'var(--red)';
    btn.style.boxShadow   = '0 4px 14px rgba(225,28,64,0.35)';
  }

  // Metrics
  document.getElementById('m-step').textContent  = (d.step||0).toFixed(2);
  document.getElementById('m-cross').textContent = d.crossings||0;
  document.getElementById('m-side').textContent  = d.side||'NEUTRAL';
  document.getElementById('m-reason').textContent = (d.reason||'OBSERVING').slice(0,10);
  document.getElementById('m-sec').textContent   = (d.second||0).toFixed(1);

  // Session stats (settings tab)
  // ── Money Management UI Update ──────────────────────────────────────────
  const mmMode = d.mm_mode || 'HYBRID';
  const modeLbl = document.getElementById('mm-status-mode');
  if (modeLbl) {
    if (mmMode === 'HYBRID') modeLbl.textContent = 'GABUNGAN (MARTI + PARLAY)';
    else if (mmMode === 'PARLAY') modeLbl.textContent = 'COMPOUND / PARLAY';
    else if (mmMode === 'MARTINGALE') modeLbl.textContent = 'MARTINGALE';
    else modeLbl.textContent = 'FLAT BET';
  }

  const stepBadge = document.getElementById('mm-step-badge');
  if (stepBadge) {
    if (mmMode === 'HYBRID') {
      if ((d.current_martingale_lvl || 0) > 0) {
        stepBadge.className = 'mm-badge mart';
        stepBadge.textContent = `🛡️ KOMPENSASI LV ${d.current_martingale_lvl}/${d.martingale_max_level||5}`;
      } else {
        stepBadge.className = 'mm-badge';
        stepBadge.textContent = `🔥 PARLAY LANGKAH ${(d.current_parlay_step||0) + 1}/${d.parlay_target_steps||2}`;
      }
    } else if (mmMode === 'PARLAY') {
      stepBadge.className = 'mm-badge';
      stepBadge.textContent = `LANGKAH ${(d.current_parlay_step||0) + 1} / ${d.parlay_target_steps||2}`;
    } else if (mmMode === 'MARTINGALE') {
      stepBadge.className = 'mm-badge mart';
      stepBadge.textContent = `LEVEL ${d.current_martingale_lvl||0} / ${d.martingale_max_level||5}`;
    } else {
      stepBadge.className = 'mm-badge';
      stepBadge.textContent = 'FLAT BET';
    }
  }

  const nextBetEl = document.getElementById('mm-next-bet');
  if (nextBetEl) nextBetEl.textContent = 'Rp ' + Number(d.next_bet_amount || d.base_bet || 14000).toLocaleString('id-ID');

  const cycleWinsEl = document.getElementById('mm-cycle-wins');
  if (cycleWinsEl) cycleWinsEl.textContent = (d.parlay_cycle_wins || 0) + 'x';

  // Calculate Net PnL & Win Rate
  const sessProfit = d.session_profit || 0;
  const sessLoss = d.session_loss || 0;
  const netPnl = sessProfit - sessLoss;
  const wins = d.session_wins || 0;
  const losses = d.session_losses || 0;
  const draws = d.session_draws || 0;
  const totalDecided = wins + losses;
  const wr = totalDecided > 0 ? ((wins / totalDecided) * 100).toFixed(1) : '0.0';

  const netFormatted = (netPnl > 0 ? '+Rp ' : (netPnl < 0 ? '-Rp ' : 'Rp ')) +
                       Math.abs(Math.round(netPnl)).toLocaleString('id-ID');
  const netColor = netPnl > 0 ? 'var(--green)' : (netPnl < 0 ? 'var(--red)' : 'var(--dim)');

  // Main Dashboard Net PnL & Win Rate
  const mmNetPnl = document.getElementById('mm-net-pnl');
  if (mmNetPnl) {
    mmNetPnl.textContent = netFormatted;
    mmNetPnl.style.color = netColor;
  }
  const mmWr = document.getElementById('mm-winrate');
  if (mmWr) mmWr.textContent = `${wr}% (${wins}W / ${losses}L)`;

  // Session stats (settings tab)
  const sp = document.getElementById('sess-profit');
  const sl = document.getElementById('sess-loss');
  const sm = document.getElementById('sess-mode');
  const sn = document.getElementById('sess-next-bet');
  const sNet = document.getElementById('sess-net-pnl');
  const sWr = document.getElementById('sess-winrate');
  if (sNet) {
    sNet.textContent = netFormatted;
    sNet.style.color = netColor;
  }
  if (sWr) sWr.textContent = `${wr}% (${wins}W / ${losses}L / ${draws}D)`;
  if (sp) sp.textContent = 'Rp ' + Number(sessProfit).toLocaleString('id-ID');
  if (sl) sl.textContent = 'Rp ' + Number(sessLoss).toLocaleString('id-ID');
  if (sm) {
    if (mmMode === 'HYBRID') {
      const sub = (d.current_martingale_lvl||0) > 0
        ? `Kompensasi Lv ${d.current_martingale_lvl}/${d.martingale_max_level||5}`
        : `Parlay Langkah ${(d.current_parlay_step||0)+1}/${d.parlay_target_steps||2}`;
      sm.textContent = `GABUNGAN (${sub})`;
    } else if (mmMode === 'PARLAY') {
      sm.textContent = `PARLAY (Langkah ${(d.current_parlay_step||0)+1}/${d.parlay_target_steps||2})`;
    } else if (mmMode === 'MARTINGALE') {
      sm.textContent = `MARTINGALE (Level ${d.current_martingale_lvl||0}/${d.martingale_max_level||5})`;
    } else {
      sm.textContent = 'FLAT BET';
    }
  }
  if (sn) sn.textContent = 'Rp ' + Number(d.next_bet_amount || d.base_bet || 14000).toLocaleString('id-ID');

  // Payout Auto Sync indicator & value
  const autoPayout = d.auto_payout_sync !== false;
  const chkAuto = document.getElementById('chk-auto-payout');
  const inpPayout = document.getElementById('inp-parlay-payout');
  const badgePayout = document.getElementById('broker-payout-badge');
  if (badgePayout) badgePayout.textContent = (d.broker_payout_pct || 82) + '%';
  if (chkAuto && document.activeElement !== chkAuto) chkAuto.checked = autoPayout;
  if (autoPayout && inpPayout && document.activeElement !== inpPayout) {
    inpPayout.value = d.parlay_payout_pct || d.broker_payout_pct || 82;
  }

  // History
  renderHistory(d.signals_history||[]);

  // Connection status box (settings tab)
  const sb = document.getElementById('conn-status-box');
  if (sb) {
    sb.textContent = 'Mode: ' + mode + '\nStatus: ' + status +
      (d.feed_error ? '\nError: ' + d.feed_error : '') +
      '\nTicks: ' + (d.tick_count||0) +
      '\nBroker: ' + (d.broker||'') +
      '\nPair: ' + (d.pair||'') +
      '\nAuto Order: ' + (d.auto_order_enabled ? 'AKTIF' : 'NON-AKTIF') +
      '\nNet PnL: ' + netFormatted +
      '\nWin Rate: ' + wr + '% (' + wins + 'W / ' + losses + 'L / ' + draws + 'D)' +
      '\nProfit Sesi: Rp ' + Number(sessProfit).toLocaleString('id-ID') +
      '\nLoss Sesi: Rp ' + Number(sessLoss).toLocaleString('id-ID');
  }

  // ── Alert Detection ──────────────────────────────────────────────────────
  if (d.last_alert && d.last_alert.ts > lastAlertTs) {
    lastAlertTs = d.last_alert.ts;
    const a = d.last_alert;
    showToast(a.message, a.type);
    if (a.type === 'tp') beepTP();
    else if (a.type === 'sl') beepSL();
  }

  // ── New signal beep/toast ─────────────────────────────────────────────────
  const sigId = d.last_signal ? (d.last_signal.candle_id + '_' + d.last_signal.second) : '';
  if (sigId && sigId !== lastSigId && d.bot_running) {
    lastSigId = sigId;
    const dir = (d.last_signal.direction||'').toUpperCase();
    if (dir === 'BUY') {
      beepBuy();
      showToast('⬆ SINYAL NAIK · ' + d.last_signal.pattern, 'buy');
    } else {
      beepSell();
      showToast('⬇ SINYAL TURUN · ' + d.last_signal.pattern, 'sell');
    }
  }

  // Chart
  renderChart(d);
}

function renderHistory(sigs) {
  const el = document.getElementById('hist-list');
  if (!sigs || !sigs.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:12px;margin-top:40px">Belum ada sinyal</div>';
    return;
  }
  el.innerHTML = sigs.slice(0,50).map(s => {
    const dir = (s.direction||'').toUpperCase();
    const badge = dir === 'BUY'
      ? '<span class="hist-dir buy">▲ NAIK</span>'
      : '<span class="hist-dir sell">▼ TURUN</span>';
    const modeTag = s.mode === 'LIVE'
      ? '<span style="color:var(--green);font-size:9px"> [LIVE]</span>'
      : '<span style="color:var(--dim);font-size:9px"> [SIM]</span>';
    return `<div class="hist-item">
      <div>${badge}<div class="hist-info">${s.pattern||''} · ${(s.price||0).toFixed(2)} · ${(s.second||0).toFixed(1)}s${modeTag}</div></div>
      <div class="hist-time">${s.timestamp||''}</div>
    </div>`;
  }).join('');
}

function renderChart(d) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const W = rect.width, H = rect.height;
  const ticks = d.ticks_history;
  if (!ticks || ticks.length < 2) return;

  let minP = Math.min(...ticks.map(t => t.price), d.open_price);
  let maxP = Math.max(...ticks.map(t => t.price), d.open_price);
  const pad = (maxP - minP) * 0.18 || 0.5;
  minP -= pad; maxP += pad;
  const getY = p => H - ((p - minP) / (maxP - minP)) * (H - 30) - 15;

  // Open price line
  const oy = getY(d.open_price);
  ctx.strokeStyle = '#394050'; ctx.setLineDash([3,3]); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, oy); ctx.lineTo(W, oy); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#697386'; ctx.font = '9px monospace';
  ctx.fillText('OPEN: ' + (d.open_price||0).toFixed(3), 6, oy - 3);

  // Side color gradient bg
  if (d.side === 'UP') {
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(56,212,161,0.07)');
    grad.addColorStop(1, 'rgba(56,212,161,0)');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, W, H);
  } else if (d.side === 'DOWN') {
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(225,28,64,0.07)');
    grad.addColorStop(1, 'rgba(225,28,64,0)');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, W, H);
  }

  // Tick line
  const lineColor = d.price >= d.open_price ? '#38D4A1' : '#E11C40';
  ctx.strokeStyle = lineColor; ctx.lineWidth = 2; ctx.lineJoin = 'round';
  ctx.beginPath();
  ticks.forEach((t, i) => {
    const x = (i / (ticks.length - 1)) * (W - 24) + 12;
    const y = getY(t.price);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Last price dot
  const lx = (W - 24) + 12, ly = getY(d.price);
  ctx.fillStyle = lineColor;
  ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fill();

  // Price label
  ctx.fillStyle = lineColor; ctx.font = 'bold 10px monospace';
  ctx.fillText((d.price||0).toFixed(3), lx + 7, ly + 4);
}

// ── Multi-pair rendering ───────────────────────────────────────────────────
function renderMultiGrid(pairs_data) {
  const el = document.getElementById('multi-grid');
  const entries = Object.entries(pairs_data);
  if (!entries.length) {
    el.innerHTML = '<div style="color:var(--dim);font-size:12px;grid-column:1/-1;text-align:center;margin-top:30px;padding:20px"><div style="font-size:28px;margin-bottom:8px">📡</div><div>Belum ada pair dipilih</div><div style="margin-top:6px;font-size:10px">Pilih aset di tab ⚙️ KONEKSI → Multi-Pair</div></div>';
    return;
  }
  // Responsive: 2 col for ≤4, 3 col for more
  el.style.gridTemplateColumns = entries.length <= 4 ? '1fr 1fr' : 'repeat(auto-fill,minmax(140px,1fr))';
  el.innerHTML = entries.map(([pair, info]) => {
    const sig = info.last_signal;
    const dir = sig ? (sig.direction||'').toUpperCase() : null;
    const cardBg = dir === 'BUY' ? '#072116' : dir === 'SELL' ? '#240b15' : 'var(--panel)';
    const dirColor = dir === 'BUY' ? 'var(--green)' : dir === 'SELL' ? 'var(--red)' : 'var(--dim)';
    const dirIcon = dir === 'BUY' ? '▲' : dir === 'SELL' ? '▼' : '—';
    // Find asset info from brokerAssets
    const assetInfo = brokerAssets.find(a => a.ric === pair);
    const assetName = assetInfo ? assetInfo.name : pair;
    const payout = assetInfo ? assetInfo.payout : null;
    const isClosed = assetInfo && assetInfo.is_open === false;
    const statusText = isClosed ? '🔴 PASAR TUTUP' : (info.status||'IDLE');
    const statusColor = isClosed ? 'var(--red)' : 'var(--dim)';
    const price = info.price || 0;
    const priceStr = price > 1000 ? price.toFixed(2) : price > 1 ? price.toFixed(4) : price.toFixed(5);
    const ticks = info.tick_count || 0;
    return `<div style="background:${cardBg};border:1px solid ${dir==='BUY'?'var(--green)':dir==='SELL'?'var(--red)':'var(--edge)'};border-radius:11px;padding:10px;position:relative;overflow:hidden">
      <div style="font-size:9px;font-weight:800;color:var(--dim);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${pair}</div>
      <div style="font-size:10px;color:var(--txt);margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0.7">${assetName}</div>
      <div style="font-size:16px;font-weight:900;font-family:monospace;letter-spacing:-0.5px">${priceStr}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:5px">
        <div style="font-size:13px;font-weight:900;color:${dirColor}">${dirIcon} ${dir||'WAIT'}</div>
        ${payout ? `<div style="font-size:9px;background:rgba(255,215,0,0.15);color:var(--gold);border-radius:4px;padding:1px 5px">${payout}%</div>` : ''}
      </div>
      <div style="font-size:8px;color:${statusColor};margin-top:3px">${statusText} · ${ticks} tick</div>
    </div>`;
  }).join('');
}

function renderOrderHistory(orders) {
  const el = document.getElementById('order-hist-list');
  if (!orders || !orders.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">Belum ada order</div>';
    return;
  }
  el.innerHTML = orders.slice(0,35).map(o => {
    const dir = (o.direction||'').toUpperCase();
    const st = (o.status||'').toUpperCase();
    const amt = Number(o.amount||0).toLocaleString('id-ID');
    const pft = Number(Math.abs(o.profit||0)).toLocaleString('id-ID');
    
    let statusColor = '#888';
    let statusTxt = '⏳ RUNNING';

    if (st === 'RUNNING') {
      statusColor = 'var(--cyan)';
      statusTxt = '⏳ RUNNING';
    } else if (st === 'WIN' || o.profit > 0) {
      statusColor = 'var(--green)';
      statusTxt = `✅ WIN +Rp ${pft}`;
    } else if (st === 'LOSE' || (st !== 'DRAW' && o.profit < 0)) {
      statusColor = 'var(--red)';
      statusTxt = `❌ LOSS -Rp ${amt}`;
    } else if (st === 'DRAW') {
      statusColor = 'var(--gold)';
      statusTxt = '⚖️ DRAW (Rp 0)';
    } else if (!o.ok) {
      statusColor = 'var(--red)';
      statusTxt = o.error ? `❌ ${o.error.slice(0, 45)}` : '❌ REJECTED';
    }

    return `<div style="background:var(--panel);border:1px solid var(--edge);border-radius:9px;padding:10px 12px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">
      <div>
        <span style="font-size:12px;font-weight:900;color:${dir==='BUY'?'var(--green)':'var(--red)'}">${dir==='BUY'?'▲':'▼'} ${dir}</span>
        <div style="font-size:9px;color:var(--dim);margin-top:2px">${o.pair||''} · Rp ${amt}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:10px;font-weight:800;color:${statusColor}">${statusTxt}</div>
        <div style="font-size:9px;color:var(--dim)">${o.timestamp||''}</div>
      </div>
    </div>`;
  }).join('');
}

// ── API Calls ─────────────────────────────────────────────────────────────
function toggleBot()      { fetch('/api/toggle_bot',      {method:'POST'}); }
function resetEngine()    { fetch('/api/reset',            {method:'POST'}); }
function switchBroker()   { fetch('/api/switch_broker',    {method:'POST'}); }
function switchStrategy() { fetch('/api/switch_strategy',  {method:'POST'}); }
function cyclePair()      { fetch('/api/cycle_pair',       {method:'POST'}); }

function toggleWallet() {
  if ((state.wallet||'DEMO') === 'DEMO') {
    document.getElementById('real-modal').style.display = 'flex';
  } else {
    fetch('/api/set_wallet?val=DEMO', {method:'POST'});
  }
}
function closeRealModal() { document.getElementById('real-modal').style.display = 'none'; }
function confirmReal()    { closeRealModal(); fetch('/api/set_wallet?val=REAL', {method:'POST'}); }

// ── Broker Assets System (150 Assets from bo-assets/v6/assets) ───────────────
let brokerAssets = [];
let selectedCategory = 'ALL';

function openPairModal() {
  document.getElementById('pair-modal').style.display = 'flex';
  if (!brokerAssets.length) {
    loadBrokerAssets();
  } else {
    renderPairModalList();
  }
}

function closePairModal() {
  document.getElementById('pair-modal').style.display = 'none';
}

function filterPairCategory(cat) {
  selectedCategory = cat;
  renderPairModalList(document.getElementById('search-pair-input').value);
}

function filterPairModal(query) {
  renderPairModalList(query);
}

function renderPairModalList(searchQuery = '') {
  const container = document.getElementById('pair-modal-list');
  if (!container) return;
  const q = (searchQuery||'').toLowerCase().trim();

  let list = brokerAssets;
  if (selectedCategory !== 'ALL') {
    list = list.filter(a => (a.category||'').toLowerCase().includes(selectedCategory.toLowerCase()));
  }
  if (q) {
    list = list.filter(a =>
      (a.ric||'').toLowerCase().includes(q) ||
      (a.name||'').toLowerCase().includes(q) ||
      (a.category||'').toLowerCase().includes(q)
    );
  }

  if (!list.length) {
    container.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:12px;padding:30px">Tidak ada pair yang cocok</div>';
    return;
  }

  const cur = (state && state.pair) || 'Z-CRY/IDX';
  container.innerHTML = list.map(a => {
    const isCur = (a.ric === cur);
    const badge = a.is_turbo ? `<span class="pair-badge-turbo">TURBO 60s</span>` : `<span style="font-size:9px;color:var(--dim)">STANDAR</span>`;
    return `<div class="pair-pick-item" style="${isCur ? 'border-color:var(--green);background:#072116' : ''}" onclick="selectPair('${a.ric}')">
      <div>
        <div style="font-weight:800;font-size:12px;color:#fff">${a.name} <span style="font-size:10px;color:var(--dim)">(${a.ric})</span></div>
        <div style="font-size:10px;color:var(--dim);margin-top:2px">${a.category || 'Asset'} · Payout: <b style="color:var(--green)">${a.payout}%</b></div>
      </div>
      <div style="text-align:right">
        ${badge}
        ${isCur ? '<div style="font-size:9px;color:var(--green);font-weight:800;margin-top:2px">AKTIF</div>' : ''}
      </div>
    </div>`;
  }).join('');
}

function selectPair(ric) {
  closePairModal();
  fetch('/api/set_pair', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({pair: ric})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      showToast(`⚡ Pair diubah ke ${ric}`, 'info');
      const pv = document.getElementById('pair-val');
      if (pv) pv.textContent = ric;
      const cp = document.getElementById('chart-pair');
      if (cp) cp.textContent = ric;
      const sel = document.getElementById('inp-pair');
      if (sel) sel.value = ric;
    }
  });
}

function onPairDropdownChange() {
  const ric = document.getElementById('inp-pair').value;
  if (ric) selectPair(ric);
}

function loadBrokerAssets(forceRefresh = false) {
  const hint = document.getElementById('asset-count-hint');
  if (hint) hint.textContent = 'Memuat aset resmi broker...';
  if (forceRefresh) {
    fetch('/api/refresh_assets', {method: 'POST'});
  }
  fetch('/api/assets')
    .then(r => r.json())
    .then(assets => {
      if (assets && assets.length) {
        brokerAssets = assets;
        const cur = (state && state.pair) || 'Z-CRY/IDX';
        const turboCount = assets.filter(a => a.is_turbo).length;
        if (hint) hint.innerHTML = `✅ <b>${assets.length} Aset</b> termuat (${turboCount} Turbo aktif) dari broker!`;

        // Update dropdown #inp-pair
        const sel = document.getElementById('inp-pair');
        if (sel) {
          const byCat = {};
          assets.forEach(a => {
            const c = a.category || 'Lainnya';
            if (!byCat[c]) byCat[c] = [];
            byCat[c].push(a);
          });
          let html = '';
          for (const [cat, items] of Object.entries(byCat)) {
            html += `<optgroup label="${cat}">`;
            items.forEach(a => {
              const tag = a.is_turbo ? ` · ${a.payout}%` : ' (Std)';
              html += `<option value="${a.ric}" ${a.ric === cur ? 'selected' : ''}>${a.name} (${a.ric}${tag})</option>`;
            });
            html += `</optgroup>`;
          }
          sel.innerHTML = html;
          sel.value = cur;
        }

        renderPairModalList();
        renderMultiPairChecklist();
        if (forceRefresh) showToast(`✅ ${assets.length} aset broker berhasil dimuat!`, 'info');
      }
    })
    .catch(() => {
      if (hint) hint.textContent = 'Gagal memuat aset otomatis (Gunakan daftar bawaan).';
    });
}

function onMMModeChange() {
  const mode = document.getElementById('inp-mm-mode').value;
  const showParlay = (mode === 'PARLAY' || mode === 'HYBRID');
  const showMart   = (mode === 'MARTINGALE' || mode === 'HYBRID');
  const showHybrid = (mode === 'HYBRID');

  document.getElementById('sec-parlay').style.display     = showParlay ? 'block' : 'none';
  document.getElementById('sec-martingale').style.display = showMart   ? 'block' : 'none';
  const hInfo = document.getElementById('sec-hybrid-info');
  if (hInfo) hInfo.style.display = showHybrid ? 'block' : 'none';

  updateMMPreview();
}

function updateMMPreview() {
  const mode = document.getElementById('inp-mm-mode').value;
  const base = parseFloat(document.getElementById('inp-base-bet').value) || 14000;

  // Pratinjau Parlay jika mode PARLAY atau HYBRID
  if (mode === 'PARLAY' || mode === 'HYBRID') {
    const steps = parseInt(document.getElementById('inp-parlay-steps').value) || 2;
    const payout = (parseFloat(document.getElementById('inp-parlay-payout').value) || 82) / 100.0;

    let html = '';
    let bet = base;
    let totalProfit = 0;
    for (let i = 1; i <= steps; i++) {
      const profit = bet * payout;
      totalProfit += profit;
      const isLast = (i === steps);
      html += `<div style="display:flex;justify-content:space-between;margin-bottom:4px;color:${i===1?'#87b2ff':'#38D4A1'}">
        <span><b>Langkah ${i}</b> ${i===1?'(OP Awal)':`(Compound Lv ${i-1})`}: Bet <b>Rp ${Math.round(bet).toLocaleString('id-ID')}</b></span>
        <span>Untung: +Rp ${Math.round(profit).toLocaleString('id-ID')}</span>
      </div>`;
      if (isLast) {
        html += `<div style="margin-top:6px;padding-top:6px;border-top:1px dashed #23344d;display:flex;justify-content:space-between;color:var(--gold);font-weight:800">
          <span>🏆 Total Profit 1 Siklus:</span>
          <span>Rp ${Math.round(totalProfit).toLocaleString('id-ID')} (Selesai -> Reset ke OP Awal)</span>
        </div>`;
      }
      bet = bet + profit;
    }
    const box = document.getElementById('parlay-preview-box');
    if (box) box.innerHTML = html;
  }

  // Pratinjau Martingale jika mode MARTINGALE atau HYBRID
  if (mode === 'MARTINGALE' || mode === 'HYBRID') {
    const mult = parseFloat(document.getElementById('inp-mart-mult').value) || 2.5;
    const levels = parseInt(document.getElementById('inp-mart-levels').value) || 5;

    let html = '';
    let val = base;
    let autoLadder = [];
    for (let l = 0; l < levels; l++) {
      const rounded = Math.round(val);
      autoLadder.push(rounded);
      html += `<div style="display:flex;justify-content:space-between;margin-bottom:3px;color:${l===0?'#fff':'#ff8899'}">
        <span><b>Level ${l}</b> ${l===0?'(OP Awal)':`(Kompensasi ${l})`}:</span>
        <span style="font-family:monospace;font-weight:800">Rp ${rounded.toLocaleString('id-ID')}</span>
      </div>`;
      val *= mult;
    }
    const box = document.getElementById('martingale-preview-box');
    if (box) box.innerHTML = html;

    // Otomatis sinkronkan nilai ke input Daftar Ladder Kompensasi
    if (!ladderManualOverride) {
      const ladInput = document.getElementById('inp-mart-ladder');
      if (ladInput && document.activeElement !== ladInput) {
        ladInput.value = autoLadder.join(', ');
      }
    }
  }
}

let ladderManualOverride = false;

function onMartParamChange() {
  ladderManualOverride = false;
  updateMMPreview();
}

function toggleAutoPayout(enabled) {
  const inp = document.getElementById('inp-parlay-payout');
  if (enabled && state.broker_payout_pct) {
    inp.value = state.broker_payout_pct;
  }
  updateMMPreview();
}

function onPayoutManualInput() {
  const chk = document.getElementById('chk-auto-payout');
  if (chk) chk.checked = false;
  updateMMPreview();
}

function recalcMartingaleLadder() {
  ladderManualOverride = false;
  const base = parseFloat(document.getElementById('inp-base-bet').value) || 14000;
  const mult = parseFloat(document.getElementById('inp-mart-mult').value) || 2.5;
  const levels = parseInt(document.getElementById('inp-mart-levels').value) || 5;
  let ladder = [];
  let val = base;
  for (let l = 0; l < levels; l++) {
    ladder.push(Math.round(val));
    val *= mult;
  }
  document.getElementById('inp-mart-ladder').value = ladder.join(', ');
  updateMMPreview();
  showToast('🔄 Ladder kompensasi disinkronkan!', 'info');
}

function saveMMSettings() {
  const mm_mode = document.getElementById('inp-mm-mode').value;
  const base_bet = parseFloat(document.getElementById('inp-base-bet').value) || 14000;
  const parlay_steps = parseInt(document.getElementById('inp-parlay-steps').value) || 2;
  const parlay_payout = parseFloat(document.getElementById('inp-parlay-payout').value) || 82;
  const mart_mult = parseFloat(document.getElementById('inp-mart-mult').value) || 2.5;
  const mart_levels = parseInt(document.getElementById('inp-mart-levels').value) || 5;
  const ladderStr = document.getElementById('inp-mart-ladder').value;
  const ladder = ladderStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n) && n > 0);
  const tp = parseFloat(document.getElementById('inp-tp').value) || 500000;
  const sl = parseFloat(document.getElementById('inp-sl').value) || 250000;
  const auto_order = document.getElementById('inp-auto-order').value === 'true';
  const auto_payout_sync = document.getElementById('chk-auto-payout') ? document.getElementById('chk-auto-payout').checked : true;

  fetch('/api/set_mm_settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      mm_mode,
      base_bet,
      parlay_target_steps: parlay_steps,
      parlay_payout_pct: parlay_payout,
      auto_payout_sync: auto_payout_sync,
      martingale_multiplier: mart_mult,
      martingale_max_level: mart_levels,
      martingale_ladder: ladder.length ? ladder : null,
      tp_target: tp,
      sl_amount: sl,
      auto_order_enabled: auto_order
    })
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      showToast('✅ Pengaturan Money Management berhasil disimpan!', 'info');
    } else {
      showToast('❌ Gagal menyimpan pengaturan', 'loss');
    }
  });
}

function resetMMSession() {
  if (!confirm('Reset profit/loss sesi dan kembali ke langkah awal?')) return;
  fetch('/api/reset_mm', {method: 'POST'}).then(r => r.json()).then(d => {
    showToast('↺ Sesi Money Management direset ke awal!', 'info');
  });
}

setTimeout(updateMMPreview, 300);

function exportCSV() {
  fetch('/api/export_csv', {method:'POST'}).then(r => r.json()).then(d => {
    if (d.ok) showToast(`📊 Ekspor: ${d.filename} (${d.count} sinyal)`, 'info', 5000);
    else showToast('❌ Ekspor gagal', 'loss');
  });
}

// ── Multi-Pair Checklist ───────────────────────────────────────────────────
let _mpFiltered = []; // current filtered list

function renderMultiPairChecklist() {
  const q = (document.getElementById('mp-search') || {}).value || '';
  const cat = (document.getElementById('mp-cat-filter') || {}).value || '';
  const onlyOpen = (document.getElementById('mp-only-open') || {}).checked !== false;
  const el = document.getElementById('multi-asset-checklist');
  if (!el) return;
  if (!brokerAssets || !brokerAssets.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">Memuat daftar aset dari broker...</div>';
    return;
  }
  const totalOpen = brokerAssets.filter(a => a.is_open).length;
  const countEl = document.getElementById('mp-open-count');
  if (countEl) countEl.textContent = `${totalOpen} aset buka`;

  _mpFiltered = brokerAssets.filter(a => {
    const matchQ = !q || a.ric.toLowerCase().includes(q.toLowerCase()) || a.name.toLowerCase().includes(q.toLowerCase());
    const matchCat = !cat || (a.category||'').toLowerCase().includes(cat.toLowerCase()) || (a.name||'').toLowerCase().includes(cat.toLowerCase());
    const matchOpen = !onlyOpen || a.is_open;
    return matchQ && matchCat && matchOpen;
  });
  if (!_mpFiltered.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--dim);font-size:11px;padding:20px">Tidak ada aset yang cocok</div>';
    return;
  }
  el.innerHTML = _mpFiltered.map(a => {
    const isTurbo = a.is_turbo || a.turbo;
    const isOpen = a.is_open;
    const chk = document.querySelector(`#multi-asset-checklist input[value="${a.ric}"]`);
    const checked = chk ? chk.checked : false;
    const opacity = isOpen ? '1' : '0.45';
    const statusBadge = isOpen ?
      `<span style="font-size:8px;background:rgba(56,212,161,0.18);color:var(--green);border-radius:3px;padding:1px 4px;font-weight:700">🟢 BUKA</span>` :
      `<span style="font-size:8px;background:rgba(225,28,64,0.18);color:var(--red);border-radius:3px;padding:1px 4px;font-weight:700">🔴 TUTUP</span>`;
    return `<label style="display:flex;align-items:center;gap:7px;padding:5px 4px;border-bottom:1px solid rgba(255,255,255,0.04);cursor:pointer;font-size:11px;opacity:${opacity}" title="${a.name} ${isOpen?'(Pasar Buka)':'(Pasar Tutup)'}">
      <input type="checkbox" value="${a.ric}" onchange="updateMpCounter()" ${checked?'checked':''} style="accent-color:var(--cyan);width:14px;height:14px;flex-shrink:0">
      <span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
        <span style="font-weight:700;color:var(--txt)">${a.ric}</span>
        <span style="color:var(--dim);font-size:10px;margin-left:4px">${a.name}</span>
      </span>
      <span style="display:flex;gap:3px;flex-shrink:0;align-items:center">
        ${statusBadge}
        ${isTurbo ? '<span style="font-size:8px;background:rgba(0,200,255,0.15);color:var(--cyan);border-radius:3px;padding:1px 4px">T</span>' : ''}
        <span style="font-size:8px;background:rgba(255,215,0,0.12);color:var(--gold);border-radius:3px;padding:1px 4px">${a.payout||0}%</span>
      </span>
    </label>`;
  }).join('');
  updateMpCounter();
}

function filterMultiPairList() {
  renderMultiPairChecklist();
}

function updateMpCounter() {
  const checkboxes = document.querySelectorAll('#multi-asset-checklist input[type="checkbox"]');
  const count = Array.from(checkboxes).filter(c => c.checked).length;
  const counter = document.getElementById('mp-counter');
  const warn = document.getElementById('mp-warn');
  if (counter) counter.textContent = `${count} aset dipilih`;
  if (warn) warn.style.display = count > 20 ? 'block' : 'none';
}

function selectAllTurbo() {
  const checkboxes = document.querySelectorAll('#multi-asset-checklist input[type="checkbox"]');
  checkboxes.forEach(cb => {
    const asset = brokerAssets.find(a => a.ric === cb.value);
    cb.checked = asset ? ((asset.is_turbo || asset.turbo) && asset.is_open) : false;
  });
  updateMpCounter();
  showToast('✅ Dipilih semua aset turbo yang sedang buka', 'info');
}

function selectTopN(n) {
  // Sort by payout desc, pick top N yang SEDANG BUKA dan TURBO
  const topRics = [...brokerAssets]
    .filter(a => (a.is_turbo || a.turbo) && a.is_open)
    .sort((a,b) => (b.payout||0) - (a.payout||0))
    .slice(0, n)
    .map(a => a.ric);
  const checkboxes = document.querySelectorAll('#multi-asset-checklist input[type="checkbox"]');
  checkboxes.forEach(cb => { cb.checked = topRics.includes(cb.value); });
  updateMpCounter();
  if (topRics.length) showToast(`🟢 Dipilih ${topRics.length} aset pasar buka teraktif`, 'info');
}

function clearMultiPairs() {
  document.querySelectorAll('#multi-asset-checklist input[type="checkbox"]').forEach(cb => cb.checked = false);
  updateMpCounter();
}

function saveMultiPairs() {
  const checkboxes = document.querySelectorAll('#multi-asset-checklist input[type="checkbox"]:checked');
  const pairs = Array.from(checkboxes).map(cb => cb.value);
  if (!pairs.length) { showToast('⚠️ Pilih minimal 1 aset', 'info'); return; }
  fetch('/api/set_multi_pairs', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({pairs, enabled: true})
  }).then(r => r.json()).then(d => {
    showToast(d.msg || `📡 ${pairs.length} pair diaktifkan`, 'info');
  });
}

function autoLoginConnect() {
  const broker   = document.getElementById('inp-broker').value;
  const email    = document.getElementById('inp-auto-email').value.trim();
  const password = document.getElementById('inp-auto-pass').value.trim();
  const pair     = document.getElementById('inp-pair').value;

  if (!email || !password) {
    alert('Email dan password wajib diisi!');
    return;
  }
  document.getElementById('conn-status-box').textContent = 'Melakukan login ke ' + broker + '...';

  fetch('/api/auto_login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({broker, email, password, pair})
  }).then(r => r.json()).then(d => {
    document.getElementById('conn-status-box').textContent = (d.ok ? '✅ ' : '❌ ') + (d.msg || '');
    if (d.ok) {
      if (d.authtoken) document.getElementById('inp-authtoken').value = d.authtoken;
      if (d.device_id) document.getElementById('inp-deviceid').value = d.device_id;
      if (d.user_id)   document.getElementById('inp-userid').value = d.user_id;
    }
  }).catch(e => {
    document.getElementById('conn-status-box').textContent = 'Error: ' + e;
  });
}

function connectLive() {
  const broker    = document.getElementById('inp-broker').value;
  const authtoken = document.getElementById('inp-authtoken').value.trim();
  const device_id = document.getElementById('inp-deviceid').value.trim();
  const pair      = document.getElementById('inp-pair').value;
  const user_id   = document.getElementById('inp-userid').value.trim();
  const email     = document.getElementById('inp-email').value.trim();

  if (!authtoken) { alert('authtoken wajib diisi!'); return; }
  if (!device_id) { alert('device_id wajib diisi!');  return; }

  document.getElementById('conn-status-box').textContent = 'Menghubungkan...';

  fetch('/api/connect_live', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({broker, authtoken, device_id, pair, user_id, email})
  }).then(r => r.json()).then(d => {
    document.getElementById('conn-status-box').textContent =
      (d.ok ? 'OK · ' : 'ERROR · ') + (d.msg||'');
  });
}

function disconnectLive() {
  fetch('/api/disconnect_live', {method:'POST'}).then(() => {
    document.getElementById('conn-status-box').textContent = 'Kembali ke Simulator';
  });
}

function changeTab(tab) {
  currentTab = tab;
  const tabMap = {
    dashboard: {view:'view-dashboard', nav:'tab-dash'},
    history:   {view:'view-hist',      nav:'tab-hist'},
    settings:  {view:'view-sett',      nav:'tab-sett'},
    multi:     {view:'view-multi',     nav:'tab-multi'},
  };
  Object.entries(tabMap).forEach(([t, ids]) => {
    document.getElementById(ids.view).className = 'view-content ' + (t === tab ? 'active' : '');
    document.getElementById(ids.nav).className  = 'nav-item '     + (t === tab ? 'active' : '');
  });
  // Refresh multi-pair checklist when opening settings tab
  if (tab === 'settings' || tab === 'multi') {
    if (brokerAssets && brokerAssets.length) renderMultiPairChecklist();
  }
}

// Muat kredensial otomatis saat halaman dibuka
async function loadAutoCredentials() {
  try {
    const r = await fetch('/api/credentials');
    const d = await r.json();
    if (d.authtoken) document.getElementById('inp-authtoken').value = d.authtoken;
    if (d.device_id) document.getElementById('inp-deviceid').value  = d.device_id;
    if (d.user_id)   document.getElementById('inp-userid').value    = d.user_id;
    if (d.email) {
      document.getElementById('inp-email').value      = d.email;
      const autoEmail = document.getElementById('inp-auto-email');
      if (autoEmail) autoEmail.value = d.email;
    }
    if (d.broker) {
      const bSel = document.getElementById('inp-broker');
      if (bSel) bSel.value = d.broker;
    }
    const sb = document.getElementById('conn-status-box');
    if (sb && (d.authtoken || d.logged_in)) {
      sb.textContent = '✅ Akun terhubung otomatis dari sesi tersimpan!';
    }
  } catch(e) {}
}
loadAutoCredentials();
setTimeout(loadBrokerAssets, 500);
</script>
</body>
</html>
"""

PAIRS = ['Z-CRY/IDX', 'Z-CRY/IDX-OTC', 'EURUSD-OTC', 'GBPUSD-OTC']


def fetch_broker_deals_history(acc_type: str = 'demo') -> list:
    """Ambil riwayat order resmi langsung dari broker Binomo/Stockity."""
    token = app_state.get('authtoken', '')
    dev_id = app_state.get('device_id', '')
    if not token or not dev_id:
        return []
    try:
        api_dom = 'stockity.com' if app_state.get('broker') == 'Stockity' else 'binomo5.com'
        url = f"https://api.{api_dom}/bo-deals-history/v3/deals/trade?type={acc_type.lower()}&locale=en"
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            headers={
                'authorization-token': token,
                'device-id':           dev_id,
                'device-type':         'web',
                'User-Agent':          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept':              'application/json',
            }
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
            raw_deals = data.get('data', {}).get('standard_trade_deals', [])

            # Auto-detect payment_rate dari broker untuk pair aktif
            for d in raw_deals[:15]:
                rate = d.get('payment_rate')
                if rate and float(rate) > 0:
                    pair_name = d.get('asset_ric', '')
                    current_pair = app_state.get('pair', '')
                    if not pair_name or pair_name == current_pair:
                        with lock:
                            app_state['broker_payout_pct'] = float(rate)
                            if app_state.get('auto_payout_sync', True):
                                app_state['parlay_payout_pct'] = float(rate)
                        break

            orders = []
            for d in raw_deals[:35]:
                amt = float(d.get('amount') or d.get('bet') or 0) / 100.0
                win_val = float(d.get('win') or 0) / 100.0
                st = str(d.get('status', '')).lower()
                if st == 'won':
                    profit = win_val - amt
                    st_text = 'WIN'
                elif st == 'lost':
                    profit = -amt
                    st_text = 'LOSE'
                elif st == 'open':
                    profit = 0
                    st_text = 'RUNNING'
                else:
                    profit = 0
                    st_text = 'DRAW'

                created_str = d.get('created_at', '')
                time_str = ''
                if created_str:
                    try:
                        dt = datetime.datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                        local_dt = dt.astimezone()
                        time_str = local_dt.strftime('%H:%M:%S')
                    except Exception:
                        time_str = created_str[:19].replace('T', ' ')

                trend = str(d.get('trend', '')).lower()
                direction = 'BUY' if trend == 'call' else 'SELL'

                orders.append({
                    'deal_id':   d.get('uuid'),
                    'timestamp': time_str,
                    'direction': direction,
                    'amount':    int(round(amt)),
                    'pair':      d.get('asset_ric', 'Z-CRY/IDX'),
                    'pattern':   'AUTO',
                    'ok':        True,
                    'status':    st_text,
                    'profit':    profit,
                })
            return orders
    except Exception:
        return []


def fetch_broker_assets_list(broker=None, authtoken=None, device_id=None) -> list:
    """Ambil daftar resmi 150 aset broker langsung dari API bo-assets/v6/assets."""
    global PAIRS
    if not broker:
        broker = app_state.get('broker', 'Binomo')
    if not authtoken:
        authtoken = app_state.get('authtoken', '')
    if not device_id:
        device_id = app_state.get('device_id', '')

    domain = 'stockity1.id' if str(broker).lower() == 'stockity' else 'binomo5.com'
    url = f"https://api.{domain}/bo-assets/v6/assets?locale=id"
    headers = {
        'authorization-token': authtoken,
        'device-id': device_id,
        'device-type': 'web',
        'Cookie': f'authtoken={authtoken}; device_id={device_id}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }

    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            raw_assets = data.get('data', {}).get('assets', [])

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        day_str = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][now_utc.weekday()]
        now_hm = now_utc.strftime('%H:%M')

        parsed = []
        turbo_rics = []
        for a in raw_assets:
            ric = a.get('ric', '')
            if not ric:
                continue
            name = a.get('name', ric)
            cat = a.get('type_name', 'Lainnya')
            ftt = a.get('trading_tools_settings', {}).get('ftt', {})
            types = ftt.get('enabled_trading_types', [])
            is_turbo = 'turbo' in types
            rate = float(ftt.get('base_payment_rate_turbo') or ftt.get('base_payment_rate_standard') or 0)

            sched = ftt.get('schedule', {}).get(day_str, [])
            is_open = any(len(w) == 2 and w[0] <= now_hm < w[1] for w in sched)

            user_statuses = ftt.get('user_statuses', {})
            user_allowed = any(s.get('active', False) for s in user_statuses.values()) if user_statuses else True

            item = {
                'ric': ric,
                'name': name,
                'category': cat,
                'is_turbo': is_turbo,
                'turbo': is_turbo,
                'active': is_open and user_allowed,
                'is_open': is_open,
                'payout': rate
            }
            parsed.append(item)
            if is_turbo and is_open:
                turbo_rics.append(ric)

        # Urutkan: Aset yang sedang BUKA dahulu, lalu Turbo, lalu payout tertinggi, lalu nama
        parsed.sort(key=lambda x: (not x['is_open'], not x['is_turbo'], -x['payout'], x['name']))

        with lock:
            app_state['available_assets'] = parsed
            if turbo_rics:
                PAIRS = turbo_rics

        print(f"[+] Sinkronisasi {len(parsed)} aset resmi broker berhasil ({len(turbo_rics)} Turbo).")
        return parsed
    except Exception as e:
        print(f"[-] Gagal fetch broker assets: {e}")
        return app_state.get('available_assets', [])


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP Handler
# ══════════════════════════════════════════════════════════════════════════════
class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            body = DASHBOARD_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == '/logo.png':
            logo_path = os.path.join(BASE_DIR, 'logo.png')
            if not os.path.exists(logo_path):
                # Fallback jika di internal Android storage
                logo_path = os.path.join(os.environ.get('ANDROID_FILES_DIR', BASE_DIR), 'logo.png')
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    logo_bytes = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(logo_bytes)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(logo_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/api/state':
            with lock:
                data = {k: v for k, v in app_state.items()
                        if k not in ('authtoken', 'device_id')}  # Jangan expose credentials
            self._send_json(data)

        elif self.path == '/api/multi_state':
            with lock:
                self._send_json(app_state.get('pairs_data', {}))

        elif self.path == '/api/orders_history':
            acc_type = app_state.get('wallet', 'DEMO').lower()
            broker_deals = fetch_broker_deals_history(acc_type)
            with lock:
                local_running = [o for o in app_state.get('orders_history', []) if o.get('status') == 'RUNNING']
            known_ids = {o.get('deal_id') for o in broker_deals if o.get('deal_id')}
            combined = [o for o in local_running if o.get('deal_id') not in known_ids] + broker_deals
            self._send_json(combined)

        elif self.path == '/api/credentials':
            cfg = load_session_config()
            with lock:
                self._send_json({
                    'authtoken': app_state.get('authtoken', '') or cfg.get('authtoken', ''),
                    'device_id': app_state.get('device_id', '') or cfg.get('device_id', ''),
                    'user_id':   app_state.get('user_id', '') or cfg.get('user_id', ''),
                    'email':     app_state.get('email', '') or cfg.get('email', ''),
                    'broker':    app_state.get('broker', 'Binomo') or cfg.get('broker', 'Binomo'),
                    'logged_in': bool(app_state.get('logged_in') or cfg.get('authtoken')),
                })

        elif self.path == '/api/assets':
            assets = app_state.get('available_assets', [])
            if not assets and app_state.get('authtoken'):
                assets = fetch_broker_assets_list()
            self._send_json(assets)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split('?')[0]
        query = {}
        if '?' in self.path:
            query = parse_qs(self.path.split('?', 1)[1])

        # Read body jika ada
        body = {}
        clen = int(self.headers.get('Content-Length', 0))
        if clen > 0:
            try:
                body = json.loads(self.rfile.read(clen))
            except Exception:
                body = {}

        # ── Setiap handler mengambil lock SENDIRI (granular) ───────────────
        # Ini mencegah satu request blocking semua request lain
        if path == '/api/toggle_bot':
            with lock:
                app_state['bot_running'] = not app_state['bot_running']
                if not app_state['bot_running']:
                    app_state['last_signal'] = None
                if app_state['bot_running']:
                    app_state['tp_hit'] = False
                    app_state['sl_hit'] = False
                result = {'ok': True, 'bot_running': app_state['bot_running']}
            self._send_json(result)
            return

        # ── Gunakan blok if/elif tanpa satu with lock global ──────────────
        elif path == '/api/reset':
            with lock:
                app_state['signals_history'].clear()
                app_state['last_signal'] = None
                app_state['tick_count']  = 0
                app_state['session_profit'] = 0.0
                app_state['session_loss']   = 0.0
                app_state['session_wins']   = 0
                app_state['session_losses'] = 0
                app_state['session_draws']  = 0
                app_state['current_ladder_idx'] = 0
                app_state['current_martingale_lvl'] = 0
                app_state['current_parlay_step'] = 0
                app_state['tp_hit'] = False
                app_state['sl_hit'] = False
                app_state['next_bet_amount'] = calculate_current_bet()
            try:
                android_bridge.reset()
                android_bridge.reset_pc()
            except Exception:
                pass
            self._send_json({'ok': True})

        elif path == '/api/set_mm_settings':
            with lock:
                mm_mode = body.get('mm_mode', app_state.get('mm_mode', 'HYBRID'))
                base_bet = float(body.get('base_bet', app_state.get('base_bet', 14000)))
                parlay_steps = int(body.get('parlay_target_steps', app_state.get('parlay_target_steps', 2)))
                parlay_payout = float(body.get('parlay_payout_pct', app_state.get('parlay_payout_pct', 82.0)))
                mart_mult = float(body.get('martingale_multiplier', app_state.get('martingale_multiplier', 2.5)))
                mart_levels = int(body.get('martingale_max_level', app_state.get('martingale_max_level', 5)))
                custom_ladder = body.get('martingale_ladder')
                tp = float(body.get('tp_target', app_state.get('tp_target', 500000)))
                sl = float(body.get('sl_amount', app_state.get('sl_amount_ao', 250000)))
                auto_order = bool(body.get('auto_order_enabled', app_state.get('auto_order_enabled', True)))
                app_state['mm_mode'] = mm_mode
                app_state['base_bet'] = int(base_bet)
                app_state['bet_amount'] = int(base_bet)
                app_state['parlay_target_steps'] = parlay_steps
                auto_payout = bool(body.get('auto_payout_sync', app_state.get('auto_payout_sync', True)))
                app_state['auto_payout_sync'] = auto_payout
                if auto_payout:
                    app_state['parlay_payout_pct'] = float(app_state.get('broker_payout_pct', parlay_payout))
                else:
                    app_state['parlay_payout_pct'] = parlay_payout
                app_state['martingale_multiplier'] = mart_mult
                app_state['martingale_max_level'] = mart_levels
                if custom_ladder and isinstance(custom_ladder, list):
                    app_state['martingale_ladder'] = [int(x) for x in custom_ladder if int(x) > 0]
                else:
                    app_state['martingale_ladder'] = generate_martingale_ladder(int(base_bet), mart_mult, mart_levels)
                app_state['tp_target'] = tp
                app_state['sl_amount_ao'] = sl
                app_state['auto_order_enabled'] = auto_order
                app_state['current_martingale_lvl'] = 0
                app_state['current_parlay_step'] = 0
                app_state['active_order_running'] = False
                app_state['next_bet_amount'] = calculate_current_bet()
                result = {
                    'ok': True,
                    'msg': 'Pengaturan MM berhasil disimpan',
                    'mm_mode': app_state['mm_mode'],
                    'next_bet': app_state['next_bet_amount']
                }
            self._send_json(result)

        elif path == '/api/reset_mm':
            with lock:
                app_state['current_martingale_lvl'] = 0
                app_state['current_parlay_step'] = 0
                app_state['session_profit'] = 0.0
                app_state['session_loss'] = 0.0
                app_state['session_wins'] = 0
                app_state['session_losses'] = 0
                app_state['session_draws'] = 0
                app_state['tp_hit'] = False
                app_state['sl_hit'] = False
                app_state['next_bet_amount'] = calculate_current_bet()
            self._send_json({'ok': True, 'msg': 'Sesi MM direset'})

        elif path == '/api/switch_broker':
            with lock:
                app_state['broker'] = 'Stockity' if app_state['broker'] == 'Binomo' else 'Binomo'
                result = {'ok': True, 'broker': app_state['broker']}
            self._send_json(result)

        elif path == '/api/switch_strategy':
            with lock:
                app_state['strategy'] = (
                    'RANGE_REACTION' if app_state['strategy'] == 'OPEN_PRICE_PATTERN'
                    else 'OPEN_PRICE_PATTERN'
                )
                result = {'ok': True, 'strategy': app_state['strategy']}
            self._send_json(result)

        elif path == '/api/cycle_pair':
            with lock:
                idx = PAIRS.index(app_state['pair']) if app_state['pair'] in PAIRS else 0
                app_state['pair'] = PAIRS[(idx + 1) % len(PAIRS)]
                result = {'ok': True, 'pair': app_state['pair']}
            self._send_json(result)

        elif path == '/api/set_pair':
            new_pair = body.get('pair', '').strip()
            if new_pair:
                with lock:
                    app_state['pair'] = new_pair
                    app_state['last_ordered_candle'] = None
                    app_state['ticks_history'].clear()
                    for a in app_state.get('available_assets', []):
                        if a.get('ric') == new_pair and a.get('payout'):
                            app_state['broker_payout_pct'] = float(a['payout'])
                            if app_state.get('auto_payout_sync', True):
                                app_state['parlay_payout_pct'] = float(a['payout'])
                            break
                    _feed_mode = app_state.get('feed_mode')
                    _auth = app_state.get('authtoken')
                    _broker = app_state.get('broker')
                    _dev_id = app_state.get('device_id')
                    _payout = app_state.get('broker_payout_pct', 82.0)
                if _feed_mode == 'LIVE' and _auth:
                    threading.Thread(target=lambda: start_live_feed(
                        _broker, _auth, _dev_id, new_pair
                    ), daemon=True).start()
                save_session_config({
                    'broker': _broker or 'Binomo',
                    'email': app_state.get('email', ''),
                    'authtoken': _auth or '',
                    'device_id': _dev_id or '',
                    'user_id': app_state.get('user_id', ''),
                    'pair': new_pair
                })
                self._send_json({'ok': True, 'pair': new_pair, 'payout': _payout})
            else:
                self._send_json({'ok': False, 'error': 'Pair kosong'})

        elif path == '/api/refresh_assets':
            threading.Thread(target=fetch_broker_assets_list, daemon=True).start()
            self._send_json({'ok': True, 'msg': 'Sinkronisasi aset broker dimulai'})

        elif path == '/api/set_wallet':
            val = str(query.get('val', ['DEMO'])[0]).upper()
            if val not in ('DEMO', 'REAL'):
                val = 'DEMO'
            with lock:
                app_state['wallet'] = val
                app_state['active_order_running'] = False
                _last_ordered_deals.clear()
                _bal_real = app_state['balance_real']
                _bal_demo = app_state['balance_demo']
                _broker = app_state.get('broker', 'Binomo')
                _auth = app_state.get('authtoken', '')
                _dev_id = app_state.get('device_id', '')
                _pair = app_state.get('pair', 'Z-CRY/IDX')
            save_session_config({'wallet': val})
            threading.Thread(target=refresh_balance, daemon=True).start()
            if _auth and _dev_id:
                try:
                    get_order_client(broker=_broker, authtoken=_auth, device_id=_dev_id, pair=_pair)
                except Exception:
                    pass
            self._send_json({
                'ok': True,
                'wallet': val,
                'balance': _bal_real if val == 'REAL' else _bal_demo
            })

        elif path == '/api/set_auto_order':
            with lock:
                app_state['auto_order_enabled'] = body.get('enabled', False)
                app_state['tp_target']          = float(body.get('tp_target', 500000))
                app_state['sl_amount_ao']        = float(body.get('sl_amount', 250000))
                app_state['current_ladder_idx'] = 0
                app_state['session_profit']     = 0.0
                app_state['session_loss']       = 0.0
                app_state['tp_hit']             = False
                app_state['sl_hit']             = False
                result = {'ok': True, 'auto_order_enabled': app_state['auto_order_enabled']}
            self._send_json(result)

        elif path == '/api/export_csv':
            fname, count = export_signals_csv()
            self._send_json({'ok': True, 'filename': os.path.basename(fname), 'count': count})

        elif path == '/api/set_multi_pairs':
            pairs   = body.get('pairs', [])
            enabled = body.get('enabled', False)
            if enabled and pairs and app_state.get('authtoken'):
                t = threading.Thread(target=lambda: start_multi_feeds(pairs), daemon=True)
                t.start()
                self._send_json({'ok': True, 'msg': f'Monitoring {len(pairs)} pair'})
            else:
                global _multi_feed_client
                _mc = _multi_feed_client
                _multi_feed_client = None
                if _mc:
                    try: _mc.stop()
                    except: pass
                for f in list(_multi_feeds.values()):
                    try: f.stop()
                    except: pass
                _multi_feeds.clear()
                with lock:
                    app_state['multi_pair_mode'] = False
                    app_state['pairs_data'] = {}
                self._send_json({'ok': True, 'msg': 'Multi-pair dihentikan'})

        elif path == '/api/auto_login':
            broker   = body.get('broker',   'Binomo')
            email    = body.get('email',    '').strip()
            password = body.get('password', '').strip()
            pair     = body.get('pair',     app_state['pair'])
            if not email or not password:
                self._send_json({'ok': False, 'msg': 'Email & password wajib diisi'})
                return
            res = broker_login(broker, email, password)
            if not res.get('ok'):
                self._send_json({'ok': False, 'msg': res.get('error', 'Login gagal')})
                return
            authtoken = res['authtoken']
            device_id = res['device_id']
            user_id   = res.get('user_id', '')
            with lock:
                app_state['authtoken'] = authtoken
                app_state['device_id'] = device_id
                app_state['broker']    = broker
                app_state['pair']      = pair
                app_state['user_id']   = user_id
                app_state['email']     = email
                app_state['logged_in'] = True
            
            # Simpan kredensial ke session_config.json agar tidak perlu login ulang saat app dibuka
            save_session_config({
                'broker':    broker,
                'email':     email,
                'password':  password,
                'authtoken': authtoken,
                'device_id': device_id,
                'user_id':   user_id,
                'pair':      pair,
            })
            try:
                GATE.set_session({
                    'broker':    broker.lower(),
                    'user_id':   user_id or '0',
                    'token':     authtoken,
                    'device_id': device_id,
                })
            except Exception:
                pass
            threading.Thread(
                target=lambda: start_live_feed(broker, authtoken, device_id, pair),
                daemon=True
            ).start()
            self._send_json({
                'ok': True,
                'msg': f'Login berhasil! Terhubung ke {broker} ({pair})',
                'user_id': user_id,
                'authtoken': authtoken,
                'device_id': device_id
            })

        elif path == '/api/connect_live':
            broker    = body.get('broker',    'Binomo')
            authtoken = body.get('authtoken', '').strip()
            device_id = body.get('device_id', '').strip()
            pair      = body.get('pair', app_state['pair'])
            user_id   = body.get('user_id', '').strip()
            email     = body.get('email', '').strip()
            if not authtoken:
                self._send_json({'ok': False, 'msg': 'authtoken kosong'})
                return
            if not device_id:
                self._send_json({'ok': False, 'msg': 'device_id kosong'})
                return
            with lock:
                app_state['authtoken'] = authtoken
                app_state['device_id'] = device_id
                app_state['broker']    = broker
                app_state['pair']      = pair
                app_state['logged_in'] = True
                if user_id: app_state['user_id'] = user_id
                if email:   app_state['email']   = email

            # Simpan sesi token & device_id aktif
            save_session_config({
                'broker':    broker,
                'authtoken': authtoken,
                'device_id': device_id,
                'user_id':   user_id or app_state.get('user_id', ''),
                'email':     email or app_state.get('email', ''),
                'pair':      pair,
            })
            try:
                GATE.set_session({
                    'broker':    broker.lower(),
                    'user_id':   user_id or '0',
                    'token':     authtoken,
                    'device_id': device_id,
                })
            except Exception:
                pass
            threading.Thread(
                target=lambda: start_live_feed(broker, authtoken, device_id, pair),
                daemon=True
            ).start()
            self._send_json({
                'ok':  True,
                'msg': f'Menghubungkan ke {broker} ({pair})...',
            })

        elif path == '/api/disconnect_live':
            threading.Thread(target=stop_live_feed, daemon=True).start()
            self._send_json({'ok': True, 'msg': 'Kembali ke mode simulator'})

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 70)
    print(">> B7PROXTRADING v1.0 - REAL LIVE WEBSOCKET DASHBOARD")
    print("=" * 70)
    print(f"[*] URL       : http://localhost:{PORT}")
    print("[*] Engine    : Chaquopy android_bridge + open_price_pattern")
    print("[*] Feed Mode : SIMULATOR (default) -> LIVE (isi credentials)")
    print("[*] Fitur Baru: Auto-Order TP/SL - Multi-Pair - CSV Export - Alert Suara")
    print("=" * 70)
    print()
    print("  -> Buka tab [KONEKSI] di dashboard")
    print("  -> Isi authtoken + device_id atau email/password Binomo/Stockity")
    print("  -> Klik CONNECT LIVE / LOGIN untuk data real")
    print("  -> Aktifkan Auto-Order di panel [Auto-Order] untuk eksekusi otomatis")
    print("  -> Monitor multi-pair di tab [MULTI]")
    print()

    # Pastikan CSV header
    ensure_csv_header()

    # Otomatis baca / pastikan authtoken & device_id aktif
    print("[*] Memeriksa & memuat credentials broker otomatis...")
    has_creds = auto_ensure_credentials()

    # Start live balance poller, broker assets sync & watchdog
    threading.Thread(target=balance_poller, daemon=True, name='BalancePoller').start()
    threading.Thread(target=fetch_broker_assets_list, daemon=True, name='AssetFetcher').start()
    threading.Thread(target=_order_watchdog, daemon=True, name='OrderWatchdog').start()

    # Hubungkan otomatis ke LIVE broker WebSocket jika credentials siap
    if has_creds and app_state.get('authtoken') and app_state.get('device_id'):
        print(f"[*] Menghubungkan otomatis ke LIVE {app_state['broker']} ({app_state['pair']})...")
        start_live_feed(
            app_state['broker'],
            app_state['authtoken'],
            app_state['device_id'],
            app_state['pair']
        )
    else:
        start_simulator()

    server = ThreadingHTTPServer(('127.0.0.1', PORT), DashboardHandler)
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()

    try:
        print(f"[+] Server berjalan di http://localhost:{PORT}")
        print("[+] Tekan Ctrl+C untuk berhenti\n")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server dimatikan.")
        if _live_feed:
            _live_feed.stop()
        if _multi_feed_client:
            try: _multi_feed_client.stop()
            except: pass
        for f in list(_multi_feeds.values()):
            try: f.stop()
            except: pass


if __name__ == '__main__':
    main()
