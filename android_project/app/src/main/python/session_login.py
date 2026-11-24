# uncompyle6 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)]
# Embedded file name: session_login.py
# Compiled at: 2026-11-12 20:24:15
# Size of source mod 2**32: 8032 bytes
import json, time
from pathlib import Path

def _safe_read_jsonParse error at or near `LOAD_GLOBAL' instruction at offset 0


def _safe_write_json(path: str, data: dict):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False),
      encoding="utf-8")


def extract_tokens_from_cookies(cookies):
    token_names = [
     "authtoken", "auth_token", "authorization-token", "token"]
    device_names = ["device_id", "device-id", "deviceId"]
    authtoken = ""
    device_id = ""
    for c in cookies:
        name = str(c.get("name", ""))
        value = str(c.get("value", ""))
        if not authtoken:
            if name in token_names:
                authtoken = value
        if not device_id:
            if name in device_names:
                device_id = value
        return (
         authtoken, device_id)


def _log_msg(logger, msg):
    if logger:
        logger.logmsg
    else:
        print(msg)


def _make_device_id():
    """Bikin device_id web format 32 hex seperti cookie browser."""
    import uuid
    return uuid.uuid4.hex


def direct_login_and_save(api_config: dict, logger=None):
    """
    Login langsung tanpa Chrome.
    Cocok untuk Stockity endpoint:
      POST https://api.stockity1.id/passport/v2/sign_in?locale=id

    Wajib isi di config.json bagian api:
      "login_mode": "direct",
      "email": "email_akun",
      "password": "password_akun",
      "device_id": "boleh_kosong_akan_dibuat_otomatis"
    """
    import requests
    broker = api_config.get("broker", "stockity")
    domains = api_config.get("domains", {})
    domain = domains.getbroker or ("stockity1.id" if broker == "stockity" else "binomo5.com")
    email = str(api_config.get("email", "") or "").strip
    password = str(api_config.get("password", "") or "").strip
    device_id = str(api_config.get("device_id", "") or "").strip
    if not (email and password):
        raise RuntimeError("Email/password kosong. Isi api.email dan api.password di config.json")
    if not device_id or "ISI_" in device_id:
        device_id = _make_device_id()
    user_agent = api_config.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
    locale = api_config.get("locale", "id")
    timezone = api_config.get("user_timezone", "Asia/Jakarta")
    login_endpoint = api_config.get"direct_login_url" or f"https://api.{domain}/passport/v2/sign_in?locale={locale}"
    web_origin = api_config.get"web_url" or f"https://{domain}"
    headers = {'accept':"application/json, text/plain, */*", 
     'content-type':"application/json", 
     'device-id':device_id, 
     'device-type':"web", 
     'origin':web_origin, 
     'referer':web_origin + "/", 
     'user-timezone':timezone, 
     'user-agent':user_agent, 
     'cookie':f"device_type=web; device_id={device_id}"}
    payload = {'email':email, 
     'password':password}
    _log_msg(logger, f"[LOGIN DIRECT] mencoba login {broker} email={email}")
    res = requests.post(login_endpoint, json=payload, headers=headers, timeout=(int(api_config.get("timeout_seconds", 20))))
    try:
        body = res.json
    except Exception:
        body = {}
    else:
        data = body.get("data", {}) if isinstance(body, dict) else {}
        authtoken = str(data.get"authtoken" or data.get"auth_token" or data.get"token" or "")
        user_id = str(data.get"user_id" or data.get"id" or "")
        if not (res.status_code >= 400 or authtoken):
            raise RuntimeError(f"Login direct gagal status={res.status_code} response={res.text[None[:500]]}")
        session_file = api_config.get("session_file", "session.json")
        config_file = api_config.get("config_file", "config.json")
        session = {'broker':broker, 
         'domain':domain, 
         'login_mode':"direct", 
         'authtoken':authtoken, 
         'device_id':device_id, 
         'user_id':user_id, 
         'saved_at':int(time.time)}
        _safe_write_json(session_file, session)
        cfg = _safe_read_json(config_file)
        cfg.setdefault("api", {})
        cfg["api"]["login_mode"] = "direct"
        cfg["api"]["authtoken"] = authtoken
        cfg["api"]["device_id"] = device_id
        cfg["api"]["session_file"] = session_file
        _safe_write_json(config_file, cfg)
        _log_msg(logger, f"[LOGIN DIRECT] OK token tersimpan ke {config_file} dan {session_file}")
        return (authtoken, device_id)


def open_browser_login_and_saveParse error at or near `SETUP_FINALLY' instruction at offset 0