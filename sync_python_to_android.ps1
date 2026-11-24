# Script untuk menyinkronkan file Python dari folder utama ke folder Chaquopy Android
$sourceDir = $PSScriptRoot
$targetDir = Join-Path $sourceDir "android_project\app\src\main\python"

if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

$filesToCopy = @(
    "android_bridge.py",
    "android_pc_host.py",
    "auto_login.py",
    "broker_api.py",
    "broker_session_gate.py",
    "host_contract.py",
    "live_feed.py",
    "main.py",
    "open_price_pattern.py",
    "pattern_engine.py",
    "pc_host_bridge.py",
    "pc_session_bridge.py",
    "pc_strategy_mixin.py",
    "phoenix_order.py",
    "radar_demo_executor.py",
    "session_login.py"
)

Write-Host "Menyalin file Python ke $targetDir..." -ForegroundColor Cyan

foreach ($f in $filesToCopy) {
    $srcPath = Join-Path $sourceDir $f
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination $targetDir -Force
        Write-Host "  -> $f berhasil disalin" -ForegroundColor Green
    } else {
        Write-Host "  [!] $f tidak ditemukan di folder utama" -ForegroundColor Yellow
    }
}

Write-Host "`nSinkronisasi selesai! Semua modul trading siap dibuild ke APK." -ForegroundColor Cyan
