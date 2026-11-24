@echo off
chcp 65001 > nul
echo ========================================================
echo   UPLOAD PROYEK KE GITHUB & TRIGGER BUILD APK OTOMATIS
echo ========================================================
echo.

set /p REPO_URL="Masukkan URL Repository GitHub Anda (contoh: https://github.com/username/b7prox.git): "

if "%REPO_URL%"=="" (
    echo [!] URL tidak boleh kosong!
    pause
    exit /b
)

echo.
echo [*] Menyiapkan file & commit...
git add .
git commit -m "Setup Android Chaquopy & GitHub Actions APK Build"

echo [*] Mengarahkan branch ke main...
git branch -M main

echo [*] Menghubungkan remote origin...
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo [*] Mengupload kode ke GitHub...
git push -u origin main

echo.
echo ========================================================
echo  SELESAI! GitHub sekarang sedang mem-build APK Anda.
echo  Buka tab 'Actions' di halaman repository GitHub Anda
echo  untuk melihat proses dan mendownload file APK-nya!
echo ========================================================
echo.
pause
