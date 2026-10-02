"""FFmpeg/ffprobe ile bütünlük ve eşdeğerlik kanıtı."""
from __future__ import annotations

import json
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import tools

# Akış karşılaştırmasında eşit olması gereken teknik alanlar
STREAM_FIELDS = ("codec_type", "codec_name", "codec_tag_string", "profile", "level", "width", "height",
                 "pix_fmt", "color_range", "color_space", "color_transfer", "color_primaries",
                 "chroma_location", "field_order", "sample_aspect_ratio", "r_frame_rate",
                 "sample_rate", "channels", "channel_layout", "sample_fmt", "bits_per_raw_sample",
                 "extradata_hash")


def _err(cp) -> str:
    return cp.stderr.decode("utf-8", "replace").strip()


def decode_hashes(path: str, maps: List[str], *, video: bool, ctx) -> Tuple[Optional[List[str]], str]:
    """Akışları ham kare/örneğe çözer ve her akışın SHA-256'sını verir.
    Ses pcm_f64le'ye çözülür: 32 bit tamsayı ve float örnekleri kayıpsız taşır.
    -xerror: ilk çözme hatasında durur; hata metni boş değilse bütünlük kapısı kapanır."""
    args = [tools.require("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", path, *maps,
            "-c:a", "pcm_f64le"]
    if video:
        args += ["-c:v", "rawvideo", "-fps_mode", "passthrough"]
    args += ["-f", "streamhash", "-hash", "sha256", "-"]
    cp = tools.run(args, log=ctx.log, cancel=ctx.cancel)
    err = _err(cp)
    if cp.returncode != 0 or err:
        return None, err or f"ffmpeg çıkış kodu {cp.returncode}"
    return [l.strip() for l in cp.stdout.decode().splitlines() if l.strip()], ""


def packet_table(path: str, maps: List[str], *, ctx) -> Tuple[Optional[Dict[int, list]], str]:
    """Yeniden kodlamadan (-c copy) her paketin zamanını, boyutunu ve MD5'ini çıkarır."""
    args = [tools.require("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", path, *maps,
            "-c", "copy", "-f", "framemd5", "-"]
    cp = tools.run(args, log=ctx.log, cancel=ctx.cancel)
    err = _err(cp)
    if cp.returncode != 0 or err:
        return None, err or f"ffmpeg çıkış kodu {cp.returncode}"
    tbs: Dict[int, Fraction] = {}
    streams: Dict[int, list] = {}
    for line in cp.stdout.decode().splitlines():
        if line.startswith("#tb "):
            idx, tb = line[4:].split(":")
            tbs[int(idx)] = Fraction(tb.strip())
        elif line and not line.startswith("#"):
            parts = [p.strip() for p in line.split(",")]
            i, dts, pts, dur, size, md5 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), parts[5]
            tb = tbs[i]
            streams.setdefault(i, []).append((dts * tb, pts * tb, dur * tb, size, md5, tb))
    return streams, ""


def compare_packets(a: Dict[int, list], b: Dict[int, list]) -> List[str]:
    """Paket verisi birebir; zamanlar, iki dosyanın ortak başlangıç kayması düşülerek
    (zaman tabanı değişebileceği için) bir tik toleransla karşılaştırılır."""
    problems = []
    if sorted(a) != sorted(b):
        return [f"Akış sayısı farklı: {len(a)} ≠ {len(b)}"]

    def origin(t):
        return min(s[0][0] for s in t.values() if s)

    oa, ob = origin(a), origin(b)
    for i in sorted(a):
        pa, pb = a[i], b[i]
        if len(pa) != len(pb):
            problems.append(f"Akış {i}: paket sayısı farklı ({len(pa)} ≠ {len(pb)})")
            continue
        for n, (x, y) in enumerate(zip(pa, pb)):
            if x[3:5] != y[3:5]:
                problems.append(f"Akış {i}, paket {n}: veri farklı")
                break
            tol = max(x[5], y[5])
            if abs((x[1] - oa) - (y[1] - ob)) > tol or abs(x[2] - y[2]) > tol:
                problems.append(f"Akış {i}, paket {n}: zamanlama farklı "
                                f"({float(x[1] - oa):.6f}s ≠ {float(y[1] - ob):.6f}s)")
                break
    return problems


def probe(path: str, *, ctx) -> dict:
    return tools.ffprobe_json(path, ["-show_streams", "-show_format", "-show_chapters",
                                     "-show_data_hash", "sha256"], log=ctx.log, cancel=ctx.cancel)


def _side_data(s: dict) -> str:
    sd = [{k: v for k, v in d.items()} for d in s.get("side_data_list", [])]
    return json.dumps(sorted(sd, key=lambda d: d.get("side_data_type", "")), sort_keys=True)


def compare_streams(a: List[dict], b: List[dict], *, fields=STREAM_FIELDS) -> List[str]:
    problems = []
    if len(a) != len(b):
        return [f"İz sayısı farklı: {len(a)} ≠ {len(b)}"]
    for i, (x, y) in enumerate(zip(a, b)):
        for f in fields:
            # FFmpeg'in MP4 okuyucusu mov_text extradata'sına her remux'ta bir 'btrt' kutusu daha ekler;
            # stil ve yazı tipi tablosu aynı kalır, paketler ayrıca bayt bayt karşılaştırılır.
            if f == "extradata_hash" and x.get("codec_name") == "mov_text":
                continue
            if x.get(f) != y.get(f):
                problems.append(f"İz {i} – {f}: {x.get(f)!r} ≠ {y.get(f)!r}")
        if _side_data(x) != _side_data(y):
            problems.append(f"İz {i} – yan veri (döndürme/HDR/Dolby Vision) farklı: "
                            f"{_side_data(x)} ≠ {_side_data(y)}")
        if x.get("disposition") != y.get("disposition"):
            problems.append(f"İz {i} – disposition farklı")
        if "nb_frames" in x and "nb_frames" in y and x["nb_frames"] != y["nb_frames"]:
            problems.append(f"İz {i} – kare sayısı: {x['nb_frames']} ≠ {y['nb_frames']}")
        da, db = x.get("duration"), y.get("duration")
        if da and db and abs(float(da) - float(db)) > 0.05:
            problems.append(f"İz {i} – süre: {da} ≠ {db}")
    return problems


def describe(s: dict) -> str:
    t = s.get("codec_type")
    if t == "video":
        rot = next((d.get("rotation") for d in s.get("side_data_list", []) if "rotation" in d), 0)
        return (f"video {s.get('codec_name')} {s.get('width')}x{s.get('height')} {s.get('pix_fmt')} "
                f"{s.get('color_transfer') or ''} döndürme {rot}°").strip()
    if t == "audio":
        return f"ses {s.get('codec_name')} {s.get('sample_rate')} Hz {s.get('channels')} kanal"
    return f"{t} {s.get('codec_name')}"
