"""B7PROXTRADING - Android Chaquopy Entrypoint
Menjalankan local server dan trading engine di background thread Android.
"""
import os
import sys
import threading
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("B7PROX_Android")

_server_started = False
_lock = threading.Lock()

def start_engine(files_dir=None):
    global _server_started
    with _lock:
        if _server_started:
            logger.info("Server engine sudah aktif.")
            return True

        if files_dir:
            try:
                os.environ['ANDROID_FILES_DIR'] = files_dir
                os.chdir(files_dir)
                if files_dir not in sys.path:
                    sys.path.insert(0, files_dir)
            except Exception as e:
                logger.warning("Gagal set working dir ke %s: %s", files_dir, e)

        # Matikan auto-open desktop browser saat di Android
        import webbrowser
        webbrowser.open = lambda *args, **kwargs: True

        try:
            import main
            
            # Jika BASE_DIR di main.py merujuk ke read-only APK asset, alihkan ke files_dir internal
            if files_dir:
                main.BASE_DIR = files_dir
                main.SIGNALS_CSV_LIVE = os.path.join(files_dir, 'signals_live.csv')

            # Jalankan main.main() di background thread
            t = threading.Thread(target=main.main, daemon=True, name="B7ServerThread")
            t.start()
            _server_started = True
            logger.info("B7PROX Trading Engine berhasil dijalankan di background.")
            return True
        except Exception as exc:
            logger.error("Error starting B7PROX engine: %s", exc, exc_info=True)
            return False
