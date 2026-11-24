"""auto_login.py — Auto-login ke broker Binomo/Stockity via REST API
Exact replika dari 446_BrokerSignUpError.js _authenticateBroker()

Flow:
1. Generate device_id (32-char hex random, sama seperti APK)
2. POST /passport/v2/sign_in → dapat authtoken
3. GET /platform/private/v2/profile → dapat user_id & email
4. Return credentials untuk dashboard
"""
import json
import random
import sys
import urllib.request
import urllib.error
import ssl
import time


# ── Domain Config (dari 446_BrokerSignUpError.js closure_7, closure_9) ────────
DOMAINS = {
    'Binomo':   'binomo5.com',
    'Stockity': 'stockity.com',
}
API_DOMAINS = {
    'Binomo':   'binomo5.com',       # api.binomo5.com
    'Stockity': 'stockity1.id',      # api.stockity1.id  (untuk traffic tracker)
}


def generate_device_id() -> str:
    """Generate device_id persis seperti APK (32-char hex random)."""
    return ''.join(format(random.randint(0, 15), 'x') for _ in range(32))


def broker_login(broker: str, email: str, password: str) -> dict:
    """Login ke broker dan return credentials.

    Returns:
        {
            'ok': True/False,
            'authtoken': str,
            'device_id': str,
            'user_id': str,
            'email': str,
            'broker': str,
            'error': str (jika gagal),
        }
    """
    domain     = DOMAINS.get(broker, 'binomo5.com')
    api_base   = f'https://api.{domain}'
    origin     = f'https://{domain}'
    device_id  = generate_device_id()

    # ── Headers (sama persis dengan APK line 186) ─────────────────────────
    headers = {
        'Accept':           'application/json, text/plain, */*',
        'Content-Type':     'application/json',
        'device-id':        device_id,
        'device-type':      'web',
        'Origin':           origin,
        'Referer':          f'{origin}/',
        'user-timezone':    'Asia/Jakarta',
        'Cookie':           f'device_type=web; device_id={device_id}',
        'User-Agent':       'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    }

    # Buat SSL context yang lenient (beberapa broker punya SSL issues)
    ctx = ssl.create_default_context()

    # ── STEP 1: Login ─────────────────────────────────────────────────────
    login_url = f'{api_base}/passport/v2/sign_in?locale=id'
    login_body = json.dumps({'email': email.strip(), 'password': password}).encode('utf-8')

    req = urllib.request.Request(login_url, data=login_body, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        return {
            'ok': False,
            'error': f'Login HTTP {e.code}: {e.reason}. {body[:300]}',
            'broker': broker,
        }
    except Exception as e:
        return {
            'ok': False,
            'error': f'Login error: {e}',
            'broker': broker,
        }

    # ── Extract authtoken ─────────────────────────────────────────────────
    # APK tries: data.authtoken → data.auth_token → data.token
    data = resp_data.get('data', {}) or {}
    authtoken = (
        data.get('authtoken') or
        data.get('auth_token') or
        data.get('token') or
        resp_data.get('authtoken') or
        resp_data.get('auth_token') or
        resp_data.get('token') or
        ''
    )

    if not authtoken:
        return {
            'ok': False,
            'error': f'Token tidak tersedia. Response: {json.dumps(resp_data)[:500]}',
            'broker': broker,
        }

    # ── STEP 2: Get Profile ───────────────────────────────────────────────
    profile_url = f'{api_base}/platform/private/v2/profile?locale=en'
    profile_headers = dict(headers)
    profile_headers['authorization-token'] = authtoken
    profile_headers['Cookie'] = f'device_type=web; device_id={device_id}; authtoken={authtoken}'

    req2 = urllib.request.Request(profile_url, headers=profile_headers, method='GET')

    user_id = ''
    user_email = email
    try:
        with urllib.request.urlopen(req2, context=ctx, timeout=10) as resp2:
            profile_data = json.loads(resp2.read().decode('utf-8'))
            pdata = profile_data.get('data', {}) or {}
            user_id = str(pdata.get('id', ''))
            user_email = pdata.get('email', email)
    except Exception as e:
        # Non-fatal, kita sudah punya authtoken
        pass

    # ── STEP 3: Get Balances ─────────────────────────────────────────────
    bank_url = f'{api_base}/bank/v1/read?locale=en'
    balance_demo = 0.0
    balance_real = 0.0
    currency = 'IDR'
    try:
        req3 = urllib.request.Request(bank_url, headers=profile_headers, method='GET')
        with urllib.request.urlopen(req3, context=ctx, timeout=10) as resp3:
            bdata = json.loads(resp3.read().decode('utf-8')).get('data', [])
            for item in bdata:
                acc_type = item.get('account_type')
                amt = float(item.get('amount', 0)) / 100.0
                currency = item.get('currency', currency)
                if acc_type == 'demo':
                    balance_demo = amt
                elif acc_type == 'real':
                    balance_real = amt
    except Exception:
        pass

    return {
        'ok':           True,
        'authtoken':    authtoken,
        'device_id':    device_id,
        'user_id':      user_id,
        'email':        user_email,
        'broker':       broker,
        'domain':       domain,
        'balance_demo': balance_demo,
        'balance_real': balance_real,
        'currency':     currency,
    }


def fetch_balances(broker: str, authtoken: str, device_id: str) -> dict:
    """Ambil saldo realtime dari endpoint bank broker."""
    domain   = DOMAINS.get(broker, 'binomo5.com')
    api_base = f'https://api.{domain}'
    origin   = f'https://{domain}'

    headers = {
        'Accept':              'application/json, text/plain, */*',
        'authorization-token': authtoken,
        'device-id':           device_id,
        'device-type':         'web',
        'Origin':              origin,
        'Referer':             f'{origin}/',
        'Cookie':              f'device_type=web; device_id={device_id}; authtoken={authtoken}',
        'User-Agent':          'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36',
    }

    ctx = ssl.create_default_context()
    url = f'{api_base}/bank/v1/read?locale=en'
    out = {'demo': 0.0, 'real': 0.0, 'currency': 'IDR', 'ok': False}

    try:
        req = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8')).get('data', [])
            for item in data:
                acc_type = item.get('account_type')
                amt = float(item.get('amount', 0)) / 100.0
                out['currency'] = item.get('currency', out['currency'])
                if acc_type in ('demo', 'real'):
                    out[acc_type] = amt
            out['ok'] = True
    except Exception as e:
        out['error'] = str(e)

    return out


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('=' * 60)
    print('BOT X — Auto Login Broker')
    print('=' * 60)

    if len(sys.argv) >= 4:
        broker   = sys.argv[1]
        email    = sys.argv[2]
        password = sys.argv[3]
    else:
        broker   = input('Broker [Binomo/Stockity]: ').strip() or 'Binomo'
        email    = input('Email: ').strip()
        password = input('Password: ').strip()

    print(f'\n[*] Login ke {broker} ({DOMAINS.get(broker, "?")})...')
    result = broker_login(broker, email, password)

    if result['ok']:
        print(f'\n[+] LOGIN BERHASIL!')
        print(f'    Broker:    {result["broker"]}')
        print(f'    User ID:   {result["user_id"]}')
        print(f'    Email:     {result["email"]}')
        print(f'    authtoken: {result["authtoken"][:20]}...{result["authtoken"][-10:]}')
        print(f'    device_id: {result["device_id"]}')
    else:
        print(f'\n[-] LOGIN GAGAL: {result["error"]}')

    print(f'\n{json.dumps(result, indent=2)}')
