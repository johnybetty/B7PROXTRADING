# Panduan Build APK B7PROX TRADING (Chaquopy + WebView)

Project Android ini membungkus engine trading Python (`B7PROXTRADING`) ke dalam aplikasi native Android (`*.apk`) menggunakan **Chaquopy** dan **Android WebView**.

---

## 📁 Struktur Folder Project
```text
BPRO7X_app/
├── android_project/            <-- Buka folder ini di Android Studio
│   ├── app/
│   │   ├── src/main/
│   │   │   ├── java/.../MainActivity.kt        (WebView & Lifecycle Controller)
│   │   │   ├── java/.../TradingService.kt     (Foreground Service 24/7 background)
│   │   │   ├── python/                         (Seluruh modul engine Python)
│   │   │   │   ├── entrypoint_android.py       (Bridge pemanggil main.py di Android)
│   │   │   │   ├── main.py
│   │   │   │   └── ...
│   │   │   └── AndroidManifest.xml
│   │   └── build.gradle                        (Konfigurasi Chaquopy & Dependencies)
│   ├── build.gradle
│   └── settings.gradle
└── sync_python_to_android.ps1  <-- Jalankan jika ada perubahan kode Python
```

---

## 🚀 Langkah-langkah Build APK di Android Studio

### 1. Buka Project di Android Studio
1. Buka aplikasi **Android Studio** di PC Anda (`C:\Program Files\Android\Android Studio`).
2. Pilih menu **File** -> **Open...**
3. Arahkan ke folder:
   ```text
   C:\Users\biOnic\Downloads\antigravity\BPRO7X_app\android_project
   ```
4. Klik **OK / Trust Project**.

### 2. Tunggu Gradle Sync
1. Saat pertama kali dibuka, Android Studio akan otomatis mengunduh plugin Chaquopy dan paket Python (`requests`, `websockets`).
2. Pastikan PC terhubung ke internet saat proses Gradle Sync pertama kali.
3. Tunggu hingga status di bawah menunjukkan **"Gradle sync finished"**.

### 3. Build File APK
1. Di menu atas Android Studio, klik:
   ```text
   Build  ->  Build Bundle(s) / APK(s)  ->  Build APK(s)
   ```
2. Tunggu proses kompilasi selesai (biasanya 1 - 3 menit).
3. Setelah selesai, notifikasi pop-up akan muncul di pojok kanan bawah:
   ```text
   APK(s) generated successfully for 1 module: 'app'.  [locate]
   ```
4. Klik link **locate**, Anda akan langsung diarahkan ke file APK:
   ```text
   android_project\app\build\outputs\apk\debug\app-debug.apk
   ```

### 4. Install di HP Android
1. Kirim file `app-debug.apk` ke HP Android Anda (bisa lewat WhatsApp, Telegram, Google Drive, atau kabel data USB).
2. Di HP Android, buka file `.apk` tersebut dan pilih **Install**.
3. Jika muncul peringatan *"Install from unknown source"*, aktifkan izin instalasi aplikasi tidak dikenal.
4. Buka aplikasi **B7PROX TRADING**.

---

## ⚙️ Fitur Android Native yang Sudah Terpasang:
- **Foreground Service & Sticky Notification:** Menjaga engine dan WebSocket feed tetap hidup meskipun HP diminimize atau layar dikunci.
- **Auto-Loop Local Server Poller:** Menunggu hingga web server lokal Python di port 8080 siap sebelum membuka tampilan.
- **DOM & Hardware Accelerated WebView:** Performa chart dan sinyal secepat browser Chrome di HP.
- **Multi-ABI Support:** Mendukung arsitektur chip HP Android (`arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`).

---

## 🔄 Sinkronisasi Ulang (Jika Mengedit Kode Python)
Jika Anda melakukan perubahan pada file Python di folder utama `BPRO7X_app`, cukup klik kanan file:
```powershell
.\sync_python_to_android.ps1
```
Lalu pilih **Run with PowerShell** untuk menyalin pembaruan ke project Android sebelum melakukan build ulang.
