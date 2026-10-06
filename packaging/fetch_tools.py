"""Paketin içine konacak ExifTool ve FFmpeg'i indirir: packaging/bin/

Derleme makinesinde (GitHub Actions) çalışır; kullanıcı makinesinde gerekmez.
  Windows : ExifTool Windows paketi + BtbN FFmpeg (LGPL) derlemesi
  Linux   : ExifTool Perl dağıtımı + BtbN FFmpeg (LGPL) derlemesi
  macOS   : ExifTool Perl dağıtımı + ffmpeg.martin-riedl.de statik derlemesi (Apple Silicon)

Sürümler sabittir ve indirilen her dosyanın SHA-256 parmak izi aşağıdaki değerle karşılaştırılır; tutmazsa
derleme durur. Böylece bozuk ya da değiştirilmiş bir araç yayımlanan paketlere giremez.
Güncellemek için adresi ve kaynağın kendi yayımladığı SHA-256'yı birlikte değiştirin:
  ExifTool : https://exiftool.org/checksums.txt (arşivler SourceForge'da, tüm sürümler saklanır)
  macOS    : adresin yanındaki <dosya>.sha256
  BtbN     : sürümün checksums.sha256 dosyası. Yalnızca ay sonu derlemeleri kalıcıdır, onları seçin.
Kullanım: python packaging/fetch_tools.py
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

# Windows konsolu varsayılan olarak cp1252 kullanır; Türkçe çıktı betiği çökertmesin
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "bin")
UA = {"User-Agent": "MetaClean-build"}

EXIFTOOL_VERSION = "13.59"
_SF = "https://sourceforge.net/projects/exiftool/files/"
EXIFTOOL_UNIX = (_SF + f"Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz/download",
                 "668ea3acececb7235fbd0f4900e72d5f12c9b07e5c778fd36cb1e9b5828fd65a")
EXIFTOOL_WIN = (_SF + f"exiftool-{EXIFTOOL_VERSION}_64.zip/download",
                "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec")

_MAC = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/"
FFMPEG_MAC = {"ffmpeg": (_MAC + "ffmpeg.zip", "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"),
              "ffprobe": (_MAC + "ffprobe.zip", "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6")}
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n9.0.2-17-g2a571b6068-"
FFMPEG_WIN = (_BTBN + "win64-lgpl-9.0.zip", "6b264b9e6019103f601d98c292bd332fd87acf1c5e941ddff4fb71760fe63432")
FFMPEG_LINUX = (_BTBN + "linux64-lgpl-9.0.tar.xz", "2d41cbea0ca1a15029b638330740f78d6f5aeb062355bf425433fc938705350a")


def get(source) -> bytes:
    """(adres, sha256) indirir; parmak izi tutmazsa derlemeyi durdurur."""
    url, expected = source
    print("  indiriliyor:", url, flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        data = r.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"SHA-256 tutmadı, dosya bozuk ya da değiştirilmiş: {url}\n"
                         f"  beklenen: {expected}\n  gelen   : {actual}")
    print("    SHA-256 doğrulandı", flush=True)
    return data


def write(path: str, data: bytes, executable: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if executable:
        os.chmod(path, 0o755)


def fetch_exiftool() -> None:
    dest = os.path.join(BIN, "exiftool")
    if os.name == "nt":
        with zipfile.ZipFile(io.BytesIO(get(EXIFTOOL_WIN))) as z:
            for m in z.namelist():
                rel = m.split("/", 1)[1] if "/" in m else m
                if not rel or m.endswith("/"):
                    continue
                # exiftool(-k).exe sonunda tuş bekler; exiftool.exe adıyla bu davranış kapanır
                write(os.path.join(dest, rel.replace("exiftool(-k).exe", "exiftool.exe")), z.read(m))
    else:
        prefix = f"Image-ExifTool-{EXIFTOOL_VERSION}/"
        with tarfile.open(fileobj=io.BytesIO(get(EXIFTOOL_UNIX))) as t:
            for m in t.getmembers():
                rel = m.name[len(prefix):] if m.name.startswith(prefix) else ""
                if m.isfile() and (rel == "exiftool" or rel.startswith("lib/")):
                    write(os.path.join(dest, rel), t.extractfile(m).read(), executable=rel == "exiftool")
    print("  ExifTool", EXIFTOOL_VERSION)


def fetch_ffmpeg() -> None:
    if sys.platform == "darwin":
        for name, source in FFMPEG_MAC.items():
            with zipfile.ZipFile(io.BytesIO(get(source))) as z:
                write(os.path.join(BIN, name), z.read(name), executable=True)
    elif os.name == "nt":
        with zipfile.ZipFile(io.BytesIO(get(FFMPEG_WIN))) as z:
            for m in z.namelist():
                if m.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")):
                    write(os.path.join(BIN, os.path.basename(m)), z.read(m))
    else:
        with tarfile.open(fileobj=io.BytesIO(get(FFMPEG_LINUX)), mode="r:xz") as t:
            for m in t.getmembers():
                if m.isfile() and m.name.endswith(("/bin/ffmpeg", "/bin/ffprobe")):
                    write(os.path.join(BIN, os.path.basename(m.name)), t.extractfile(m).read(), executable=True)


def check() -> None:
    exe = ".exe" if os.name == "nt" else ""
    for n in ("ffmpeg", "ffprobe"):
        out = subprocess.run([os.path.join(BIN, n + exe), "-version"], capture_output=True, check=True).stdout
        print("  ", out.decode(errors="replace").splitlines()[0])
    et = os.path.join(BIN, "exiftool", "exiftool" + exe)
    cmd = [et] if os.name == "nt" else ["perl", et]
    print("   exiftool", subprocess.run([*cmd, "-ver"], capture_output=True, check=True).stdout.decode().strip())


if __name__ == "__main__":
    machine = platform.machine().lower()
    if (sys.platform == "darwin" and machine != "arm64") or (sys.platform != "darwin" and machine not in ("x86_64", "amd64")):
        raise SystemExit(f"Desteklenmeyen derleme makinesi: {sys.platform} {machine} "
                         "(paketler macOS Apple Silicon, Windows x64 ve Linux x64 için derlenir)")
    shutil.rmtree(BIN, ignore_errors=True)  # önceki (doğrulanmamış olabilecek) araçlar kalmasın
    os.makedirs(BIN)
    print("ExifTool:")
    fetch_exiftool()
    print("FFmpeg:")
    fetch_ffmpeg()
    print("Denetim:")
    check()
