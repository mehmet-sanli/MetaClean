# MetaClean'i Windows'a kaynak koddan kurar. install_windows.bat dosyasına çift tıklayarak çalıştırın.
# Kurulum yeri: %LOCALAPPDATA%\MetaClean  ·  Masaüstüne "MetaClean" kısayolu oluşturur.
$ErrorActionPreference = "Stop"
$Src  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest = Join-Path $env:LOCALAPPDATA "MetaClean"

Write-Host "1/4 Python aranıyor"
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $Python = @("py", "-3") } elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = @("python") }
else {
  Write-Host "Python bulunamadı. Kuruluyor (winget)..."
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  $Python = @("py", "-3")
}

Write-Host "2/4 Kod kopyalanıyor -> $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Remove-Item -Recurse -Force (Join-Path $Dest "metaclean") -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $Src "metaclean") $Dest
Copy-Item (Join-Path $Src "requirements.txt") $Dest -Force

Write-Host "3/4 Python ortamı hazırlanıyor"
$Venv = Join-Path $Dest ".venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
  if ($Python.Count -gt 1) { & $Python[0] $Python[1] -m venv $Venv } else { & $Python[0] -m venv $Venv }
}
& (Join-Path $Venv "Scripts\python.exe") -m pip install -q --disable-pip-version-check -r (Join-Path $Dest "requirements.txt")

Write-Host "4/4 ExifTool ve FFmpeg"
foreach ($t in @(@{cmd="exiftool"; id="OliverBetz.ExifTool"}, @{cmd="ffmpeg"; id="Gyan.FFmpeg"})) {
  if (Get-Command $t.cmd -ErrorAction SilentlyContinue) { Write-Host "  OK $($t.cmd)" }
  else {
    Write-Host "  $($t.cmd) kuruluyor (winget $($t.id))"
    winget install -e --id $t.id --accept-source-agreements --accept-package-agreements
  }
}

# pythonw: konsol penceresi açmadan çalıştırır
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "MetaClean.lnk"))
$Link.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
$Link.Arguments = "-m metaclean"
$Link.WorkingDirectory = $Dest
$Link.IconLocation = Join-Path $Dest "metaclean\gui\assets\MetaClean.ico"
$Link.Description = "Paylaşmadan önce gizli bilgileri temizle"
$Link.Save()
Write-Host ""
Write-Host "Kurulum tamam. Masaüstündeki MetaClean simgesine çift tıklayın."
Write-Host "(ExifTool/FFmpeg yeni kurulduysa önce oturumu kapatıp açmanız gerekebilir.)"
