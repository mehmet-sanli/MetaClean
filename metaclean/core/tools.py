"""Harici araçları (ExifTool, FFmpeg, ffprobe) bulma ve çalıştırma."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Dict, List, Optional

from ..i18n import tr

TOOLS = ("exiftool", "ffmpeg", "ffprobe")

_overrides: Dict[str, str] = {}


class ToolError(RuntimeError):
    pass


class ToolMissing(ToolError):
    pass


class Cancelled(RuntimeError):
    pass


def set_override(name: str, path: Optional[str]) -> None:
    if path:
        _overrides[name] = path
    else:
        _overrides.pop(name, None)


def _bundled_bases() -> List[str]:
    """PyInstaller paketi ya da uygulamanın yanındaki bin/ klasörünün üstü."""
    return [b for b in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(sys.argv[0]))) if b]


def _candidates(name: str):
    exe = name + (".exe" if os.name == "nt" else "")
    if name in _overrides:
        yield _overrides[name]
    for base in _bundled_bases():
        yield os.path.join(base, "bin", exe)
        if name == "exiftool":
            yield os.path.join(base, "bin", "exiftool", "exiftool.exe" if os.name == "nt" else "exiftool")
    found = shutil.which(name)
    if found:
        yield found
    # Finder'dan açılan macOS uygulamalarının PATH'i Homebrew'u içermez
    if sys.platform == "darwin":
        for d in ("/opt/homebrew/bin", "/usr/local/bin"):
            yield os.path.join(d, name)


def _is_script(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def find_tool(name: str) -> Optional[str]:
    for c in _candidates(name):
        if not c or not os.path.isfile(c):
            continue
        if os.access(c, os.X_OK) or _is_script(c):
            return c
        # Paketlenince çalıştırma izni düşmüş olabilir; yalnızca uygulamanın kendi bin/ klasöründeki araç
        # düzeltilir, kullanıcının gösterdiği ya da PATH'teki dosyaların iznine dokunulmaz
        if any(os.path.realpath(c).startswith(os.path.realpath(os.path.join(b, "bin")) + os.sep)
               for b in _bundled_bases()):
            try:
                os.chmod(c, 0o755)
                return c
            except OSError:
                pass
    return None


def command(name: str) -> List[str]:
    """Aracı çalıştıracak komut. Paketle gelen ExifTool bir Perl betiğidir; izin
    bitinden bağımsız çalışsın diye perl ile başlatılır."""
    path = require(name)
    if os.name != "nt" and _is_script(path) and not os.access(path, os.X_OK):
        return [shutil.which("perl") or "perl", path]
    return [path]


def require(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise ToolMissing(tr("{name} bulunamadı. Kurun ya da Ayarlar'dan yolunu gösterin.", name=name))
    return path


def tool_version(name: str) -> Optional[str]:
    path = find_tool(name)
    if not path:
        return None
    try:
        arg = "-ver" if name == "exiftool" else "-version"
        out = subprocess.run([*command(name), arg], capture_output=True, timeout=20, **_popen_flags()).stdout
        return out.decode("utf-8", "replace").splitlines()[0].strip()
    except Exception:
        return None


def _popen_flags() -> dict:
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def run(args: List[str], *, log: Optional[List[str]] = None, stdin: Optional[bytes] = None,
        cancel: Optional[threading.Event] = None) -> subprocess.CompletedProcess:
    """Komutu çalıştırır; iptal edilirse süreci öldürür. Çıktılar bayt olarak döner."""
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if log is not None:
        log.append("$ " + " ".join(shlex.quote(a) for a in args))
    p = subprocess.Popen(args, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_flags())
    data = stdin
    while True:
        try:
            out, err = p.communicate(input=data, timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            data = None  # girdi ilk çağrıda gönderildi
            if cancel is not None and cancel.is_set():
                p.kill()
                p.communicate()
                raise Cancelled()
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def _argfile(args: List[str]) -> bytes:
    """ExifTool'un -@ arg dosyası her satırı ayrı bir argüman sayar. Bir dosya adında satır sonu
    varsa ad birden çok argümana bölünür ve -if/-api üzerinden ExifTool'un Perl motoruna komut
    enjekte edilebilir. Satır sonu içeren hiçbir argümanı ExifTool'a geçirme."""
    for a in args:
        if "\n" in a or "\r" in a:
            raise ToolError(tr("Dosya adında satır sonu karakteri var; güvenlik gereği işlenmedi."))
    return ("\n".join(args) + "\n").encode("utf-8")


def exiftool_scan(path: str, *, log=None, cancel=None) -> Dict[str, object]:
    """exiftool -a -u -G1 -ee taraması. Argümanlar stdin'den verilir:
    Windows'ta Unicode dosya adları komut satırından ExifTool'a bozulmadan ulaşmaz."""
    exe = command("exiftool")
    argfile = _argfile(["-j", "-a", "-u", "-G1", "-ee", "-api", "LargeFileSupport=1", path])
    cp = run([*exe, "-charset", "filename=utf8", "-@", "-"], log=log,
             stdin=argfile, cancel=cancel)
    if cp.returncode not in (0, 1) or not cp.stdout.strip():
        raise ToolError(tr("ExifTool taraması başarısız: {err}", err=cp.stderr.decode("utf-8", "replace").strip()))
    data = json.loads(cp.stdout.decode("utf-8", "replace"))
    return data[0] if data else {}


def exiftool_write(args: List[str], *, log=None, cancel=None) -> None:
    exe = command("exiftool")
    cp = run([*exe, "-charset", "filename=utf8", "-@", "-"], log=log,
             stdin=_argfile(args), cancel=cancel)
    if cp.returncode != 0:
        raise ToolError(tr("ExifTool yazamadı: {err}", err=cp.stderr.decode("utf-8", "replace").strip()))


def ffprobe_json(path: str, extra: List[str], *, log=None, cancel=None) -> dict:
    exe = require("ffprobe")
    cp = run([exe, "-v", "error", "-of", "json", *extra, path], log=log, cancel=cancel)
    if cp.returncode != 0:
        raise ToolError(tr("ffprobe başarısız: {err}", err=cp.stderr.decode("utf-8", "replace").strip()))
    return json.loads(cp.stdout.decode("utf-8", "replace") or "{}")
