"""Paketin içine konacak ExifTool ve FFmpeg'i indirir: packaging/bin/

Derleme makinesinde (GitHub Actions) çalışır; kullanıcı makinesinde gerekmez.
  Windows : exiftool.org Windows paketi + BtbN FFmpeg (LGPL) derlemesi
  Linux   : exiftool.org Perl dağıtımı + BtbN FFmpeg (LGPL) derlemesi
  macOS   : exiftool.org Perl dağıtımı + ffmpeg.martin-riedl.de statik derlemesi
Kullanım: python packaging/fetch_tools.py
"""
from __future__ import annotations

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


def get(url: str) -> bytes:
    print("  indiriliyor:", url, flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def get_first(urls) -> bytes:
    """exiftool.org yeni sürümün arşivini ver.txt'ten sonra yayımlayabiliyor; resmî ayna SourceForge."""
    last = None
    for u in urls:
        try:
            return get(u)
        except OSError as e:  # HTTPError, URLError
            print("    olmadı:", e)
            last = e
    raise last


def arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def fetch_exiftool() -> None:
    ver = get("https://exiftool.org/ver.txt").decode().strip()
    dest = os.path.join(BIN, "exiftool")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    if os.name == "nt":
        with zipfile.ZipFile(io.BytesIO(get_first([f"https://exiftool.org/exiftool-{ver}_64.zip",
                                                   f"https://sourceforge.net/projects/exiftool/files/exiftool-{ver}_64.zip/download"]))) as z:
            for m in z.namelist():
                rel = m.split("/", 1)[1] if "/" in m else m
                if not rel or m.endswith("/"):
                    continue
                # exiftool(-k).exe sonunda tuş bekler; exiftool.exe adıyla bu davranış kapanır
                rel = rel.replace("exiftool(-k).exe", "exiftool.exe")
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(z.read(m))
    else:
        with tarfile.open(fileobj=io.BytesIO(get_first([f"https://exiftool.org/Image-ExifTool-{ver}.tar.gz",
                                                f"https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{ver}.tar.gz/download"]))) as t:
            prefix = f"Image-ExifTool-{ver}/"
            for m in t.getmembers():
                rel = m.name[len(prefix):] if m.name.startswith(prefix) else ""
                if not m.isfile() or not (rel == "exiftool" or rel.startswith("lib/")):
                    continue
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(t.extractfile(m).read())
        os.chmod(os.path.join(dest, "exiftool"), 0o755)
    print("  ExifTool", ver)


def fetch_ffmpeg() -> None:
    names = ("ffmpeg", "ffprobe")
    if sys.platform == "darwin":
        a = "arm64" if arch() == "arm64" else "amd64"
        for n in names:
            with zipfile.ZipFile(io.BytesIO(get(f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{a}/release/{n}.zip"))) as z:
                with open(os.path.join(BIN, n), "wb") as f:
                    f.write(z.read(n))
            os.chmod(os.path.join(BIN, n), 0o755)
        return
    base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    if os.name == "nt":
        data = get(base + "ffmpeg-master-latest-win64-lgpl.zip")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.namelist():
                if m.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")):
                    with open(os.path.join(BIN, os.path.basename(m)), "wb") as f:
                        f.write(z.read(m))
        return
    target = "linuxarm64" if arch() == "arm64" else "linux64"
    data = get(base + f"ffmpeg-master-latest-{target}-lgpl.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
        for m in t.getmembers():
            if m.isfile() and m.name.endswith(("/bin/ffmpeg", "/bin/ffprobe")):
                out = os.path.join(BIN, os.path.basename(m.name))
                with open(out, "wb") as f:
                    f.write(t.extractfile(m).read())
                os.chmod(out, 0o755)


def check() -> None:
    exe = ".exe" if os.name == "nt" else ""
    for n in ("ffmpeg", "ffprobe"):
        out = subprocess.run([os.path.join(BIN, n + exe), "-version"], capture_output=True, check=True).stdout
        print("  ", out.decode(errors="replace").splitlines()[0])
    et = os.path.join(BIN, "exiftool", "exiftool" + exe)
    cmd = [et] if os.name == "nt" else ["perl", et]
    print("   exiftool", subprocess.run([*cmd, "-ver"], capture_output=True, check=True).stdout.decode().strip())


if __name__ == "__main__":
    os.makedirs(BIN, exist_ok=True)
    print("ExifTool:")
    fetch_exiftool()
    print("FFmpeg:")
    fetch_ffmpeg()
    print("Denetim:")
    check()
