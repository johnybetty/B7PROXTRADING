"""Ephemeral broker-session gate for Android. No network calls or orders."""
import threading
import re

class SessionGate:

    def __init__(self):
        self._lock = threading.RLock()
        self._session = None
        self._vip_verified = False

    def set_session(self, broker, user_id, token, device_id):
        if broker not in ('binomo', 'stockity'):
            raise ValueError('BROKER_UNSUPPORTED')
        if not re.fullmatch('[1-9][0-9]*', str(user_id)):
            raise ValueError('USER_ID_INVALID')
        if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in '\r\n'):
            raise ValueError('TOKEN_INVALID')
        if not isinstance(device_id, str) or not re.fullmatch('[A-Za-z0-9_-]{8,128}', device_id):
            raise ValueError('DEVICE_ID_INVALID')
        with self._lock:
            self._session = {
                'broker': broker,
                'user_id': str(user_id),
                'token': token,
                'device_id': device_id,
            }
            self._vip_verified = False
        return self.status()

    def vip_start_verified(self, user_id):
        with self._lock:
            if not self._session or self._session["user_id"] != str(user_id):
                raise ValueError("BROKER_SESSION_REQUIRED")
            self._vip_verified = True
        return self.status()

    def preflight(self, wallet='DEMO'):
        with self._lock:
            errors = []
            if not self._session:
                errors.append('BROKER_SESSION_REQUIRED')
            if not self._vip_verified:
                errors.append('VIP_START_REQUIRED')
            if wallet != 'DEMO':
                errors.append('DEMO_ONLY')
            return {
                'ready_for_broker_connection_test': not errors,
                'errors': errors,
                'orders_enabled': False,
            }

    def reset_vip(self):
        with self._lock:
            self._vip_verified = False
        return self.status()

    def clear(self):
        with self._lock:
            self._session = None
            self._vip_verified = False
        return self.status()

    def status(self):
        with self._lock:
            return {
                'session_present': self._session is not None,
                'broker': self._session['broker'] if self._session else None,
                'user_id': self._session['user_id'] if self._session else None,
                'vip_start_verified': self._vip_verified,
                'orders_enabled': False,
            }

GATE = SessionGate()
