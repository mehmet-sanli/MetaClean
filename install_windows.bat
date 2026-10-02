@echo off
rem MetaClean Windows kurulumu: bu dosyaya çift tıklayın.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
pause
