import os
import sys
import subprocess
import shutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)

print("=" * 60)
print("🚀 Memulai proses kompilasi PyInstaller untuk B7PROXTRADING.exe...")
print("=" * 60)

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--name", "B7PROXTRADING",
    "--onefile",
    "--collect-all", "websockets",
    "main.py"
]

print("Menjalankan perintah:")
print(" ".join(cmd))
print()

res = subprocess.run(cmd)

if res.returncode == 0:
    exe_path = os.path.join(APP_DIR, "dist", "B7PROXTRADING.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print("=" * 60)
        print(f"🎉 SUKSES! File executable berhasil dibuat:")
        print(f"   Lokasi: {exe_path}")
        print(f"   Ukuran: {size_mb:.2f} MB")
        print("=" * 60)
    else:
        print("[!] File EXE tidak ditemukan di dist/")
else:
    print(f"[-] Gagal kompilasi dengan return code {res.returncode}")
    sys.exit(res.returncode)
