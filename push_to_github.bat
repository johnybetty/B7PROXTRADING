@echo off
chcp 65001 > nul
title Push B7PROXTRADING to GitHub
echo ========================================================
echo   UPLOAD PROYEK KE GITHUB & TRIGGER BUILD APK OTOMATIS
echo ========================================================
echo.
echo Target Repository: https://github.com/johnybetty/B7PROXTRADING.git
echo.
echo [*] Memeriksa status file...
git add .
git commit -m "Setup Android Chaquopy & GitHub Actions APK Build" >nul 2>&1

echo [*] Mengupload commit ke GitHub (main branch)...
echo [i] Jika jendela browser muncul, klik tombol hijau "Authorize" / "Sign in with your browser"...
echo.

git push -u origin main

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================================
    echo  BERHASIL! Kode telah terupload ke GitHub.
    echo.
    echo  Server GitHub sekarang sedang mem-build APK Anda!
    echo  Buka link ini untuk memantau & download APK:
    echo  https://github.com/johnybetty/B7PROXTRADING/actions
    echo ========================================================
) else (
    echo.
    echo [!] Push gagal atau jendela login dibatalkan.
)

echo.
pause
