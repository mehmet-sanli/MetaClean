"""Ses: MP3 ve FLAC. Etiket blokları kesilir, ses çerçeveleri bayt bayt aynen kalır."""
from __future__ import annotations

import struct
from typing import List, Tuple

from .. import ffverify
from ..report import Gate
from .base import Context, Handler


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def strip_leading_id3(data: bytes, start: int, removed: List[str]) -> int:
    while data[start:start + 3] == b"ID3" and len(data) >= start + 10:
        size = _syncsafe(data[start + 6:start + 10]) + 10 + (10 if data[start + 5] & 0x10 else 0)
        removed.append(f"ID3v2.{data[start + 3]} etiketi (başlık, sanatçı, kapak resmi, yorum…), {size} bayt")
        start += size
    if data[start:start + 8] == b"APETAGEX":
        size = struct.unpack("<I", data[start + 12:start + 16])[0] + 32
        removed.append(f"APE etiketi (dosya başı), {size} bayt")
        start += size
    return start


def strip_trailing_tags(data: bytes, start: int, end: int, removed: List[str]) -> int:
    while True:
        if end - start >= 128 and data[end - 128:end - 125] == b"TAG":
            end -= 128
            removed.append("ID3v1 etiketi, 128 bayt")
            if data[end - 227:end - 223] == b"TAG+":
                end -= 227
                removed.append("ID3v1 genişletilmiş (TAG+), 227 bayt")
        elif data[end - 32:end - 24] == b"APETAGEX":
            size, _, flags = struct.unpack("<III", data[end - 20:end - 8])
            total = size + (32 if flags & 0x80000000 else 0)
            end -= total
            removed.append(f"APE etiketi, {total} bayt")
        elif data[end - 9:end] == b"LYRICS200" and data[end - 15:end - 9].isdigit():
            total = int(data[end - 15:end - 9]) + 15
            end -= total
            removed.append(f"Lyrics3v2 şarkı sözü bloğu, {total} bayt")
        elif data[end - 9:end] == b"LYRICSEND":
            i = data.rfind(b"LYRICSBEGIN", max(start, end - 5100), end)
            if i < 0:
                break
            removed.append(f"Lyrics3v1 şarkı sözü bloğu, {end - i} bayt")
            end = i
        elif data[end - 10:end - 7] == b"3DI":
            total = _syncsafe(data[end - 4:end]) + 20
            end -= total
            removed.append(f"Dosya sonundaki ID3v2 etiketi, {total} bayt")
        else:
            return end


class _FfmpegAudioVerify:
    def verify(self, ctx: Context, out: str) -> Tuple[Gate, Gate]:
        ctx.progress("Bütünlük: temiz dosya PCM'e çözülüyor")
        clean, err = ffverify.decode_hashes(out, ["-map", "0:a"], video=False, ctx=ctx)
        if clean is None:
            return (Gate("Bütünlük", False, [f"Temiz dosya hatasız çözülemedi: {err}"]),
                    Gate("Eşdeğerlik", False, ["Bütünlük geçilmediği için denenmedi."]))
        integrity = Gate("Bütünlük", True, ["Temiz dosya baştan sona hatasız çözüldü (ffmpeg -xerror)."])
        ctx.progress("Eşdeğerlik: orijinal PCM'e çözülüyor")
        orig, err = ffverify.decode_hashes(ctx.src, ["-map", "0:a"], video=False, ctx=ctx)
        if orig is None:
            return integrity, Gate("Eşdeğerlik", False, [f"Orijinal çözülemedi, eşitlik kanıtlanamaz: {err}"])
        details = [f"Orijinal PCM: {h}" for h in orig] + [f"Temiz PCM:    {h}" for h in clean]
        problems = [] if orig == clean else ["Çözülmüş ses örnekleri farklı"]
        pa = [s for s in ffverify.probe(ctx.src, ctx=ctx)["streams"] if s["codec_type"] == "audio"]
        pb = [s for s in ffverify.probe(out, ctx=ctx)["streams"] if s["codec_type"] == "audio"]
        # MP3'te Xing başlığı yoksa süre dosya boyutundan tahmin edilir; PCM eşitliği zaten uzunluğu kanıtlar
        for s in pa + pb:
            s.pop("duration", None)
        problems += ffverify.compare_streams(pa, pb)
        details += problems
        if not problems:
            details.append("Ses örnekleri bit düzeyinde aynı; kodek, örnekleme hızı ve kanal düzeni eşleşiyor.")
        return integrity, Gate("Eşdeğerlik", not problems, details)


# ---------------------------------------------------------------- MP3

class Mp3Handler(_FfmpegAudioVerify, Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            data = f.read()
        removed: List[str] = []
        start = strip_leading_id3(data, 0, removed)
        end = strip_trailing_tags(data, start, len(data), removed)
        for r in removed:
            ctx.removed(r)
        audio = data[start:end]
        if b"Xing" in audio[:200] or b"Info" in audio[:200]:
            ctx.kept("Xing/LAME başlığı (ilk ses çerçevesi)",
                     "Doğru süre, hızlı sarma ve kesintisiz çalma buna bağlı; kodlayıcı sürümünü de içerir")
        out = ctx.out_path(".mp3")
        with open(out, "wb") as f:
            f.write(audio)
        # iTunes'un kesintisiz çalma bilgisi ID3 COMM:iTunSMPB içindedir
        try:
            from mutagen.id3 import COMM, ID3, ID3NoHeaderError
            try:
                smpb = [c for c in ID3(ctx.src).getall("COMM") if c.desc == "iTunSMPB"]
            except ID3NoHeaderError:
                smpb = []
            if smpb:
                tag = ID3()
                tag.add(COMM(encoding=0, lang=smpb[0].lang, desc="iTunSMPB", text=smpb[0].text))
                tag.save(out, v2_version=3, padding=lambda info: 0)
                ctx.kept("ID3 COMM:iTunSMPB", "iTunes/Apple cihazlarda kesintisiz çalma (gapless) bilgisi")
        except ImportError:
            ctx.warn("mutagen kurulu değil; iTunSMPB korunamadı.")
        return out

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            data = f.read()
        bad: List[str] = []
        lead: List[str] = []
        start = strip_leading_id3(data, 0, lead)
        if lead:
            from mutagen.id3 import ID3
            keys = set(ID3(out).keys())
            if len(lead) > 1 or not keys <= {k for k in keys if k.startswith("COMM:iTunSMPB")}:
                bad.append(f"Dosya başında izin listesi dışı etiket: {sorted(keys)}")
        trail: List[str] = []
        strip_trailing_tags(data, start, len(data), trail)
        return bad + [f"Kalan: {t}" for t in trail]


# ---------------------------------------------------------------- FLAC

FLAC_NAMES = {0: "STREAMINFO", 1: "PADDING (dolgu)", 2: "APPLICATION (uygulama verisi)", 3: "SEEKTABLE",
              4: "VORBIS_COMMENT (etiketler ve kodlayıcı adı)", 5: "CUESHEET (katalog no, ISRC)",
              6: "PICTURE (kapak resmi; kendi EXIF'i olabilir)"}
CHANNEL_MASK = b"WAVEFORMATEXTENSIBLE_CHANNEL_MASK="


def parse_flac(data: bytes, removed: List[str]):
    off = strip_leading_id3(data, 0, removed)
    if data[off:off + 4] != b"fLaC":
        raise ValueError("FLAC imzası yok")
    pos, blocks = off + 4, []
    while True:
        hdr = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if pos + 4 + length > len(data):
            raise ValueError("FLAC meta veri bloğu dosya sonunu aşıyor")
        blocks.append((hdr & 0x7F, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
        if hdr & 0x80:
            return blocks, pos


def parse_vorbis_comment(p: bytes) -> Tuple[bytes, List[bytes]]:
    vl = struct.unpack("<I", p[:4])[0]
    vendor = p[4:4 + vl]
    pos = 4 + vl
    n = struct.unpack("<I", p[pos:pos + 4])[0]
    pos += 4
    items = []
    for _ in range(n):
        ln = struct.unpack("<I", p[pos:pos + 4])[0]
        items.append(p[pos + 4:pos + 4 + ln])
        pos += 4 + ln
    return vendor, items


def build_vorbis_comment(items: List[bytes]) -> bytes:
    return struct.pack("<I", 0) + struct.pack("<I", len(items)) + b"".join(struct.pack("<I", len(i)) + i for i in items)


class FlacHandler(_FfmpegAudioVerify, Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            data = f.read()
        removed: List[str] = []
        blocks, audio_start = parse_flac(data, removed)
        end = strip_trailing_tags(data, audio_start, len(data), removed)
        out_blocks = []
        for typ, p in blocks:
            if typ in (0, 3):
                out_blocks.append((typ, p))
                continue
            if typ == 4:
                vendor, items = parse_vorbis_comment(p)
                mask = [i for i in items if i.upper().startswith(CHANNEL_MASK)]
                if mask:
                    out_blocks.append((4, build_vorbis_comment(mask[:1])))
                    ctx.kept("Vorbis WAVEFORMATEXTENSIBLE_CHANNEL_MASK",
                             "Çok kanallı seste hoparlör düzeni; silinirse kanallar yanlış eşlenir")
                removed.append(f"VORBIS_COMMENT: {len(items) - len(mask)} etiket ve kodlayıcı adı "
                               f"'{vendor.decode('utf-8', 'replace')}', {len(p)} bayt")
                continue
            removed.append(f"{FLAC_NAMES.get(typ, f'Blok türü {typ}')}, {len(p)} bayt")
        for r in removed:
            ctx.removed(r)
        ctx.kept("STREAMINFO", "Örnekleme hızı, bit derinliği, toplam örnek sayısı ve sesin MD5'i")
        if any(t == 3 for t, _ in out_blocks):
            ctx.kept("SEEKTABLE", "Hızlı sarma; ses çerçevelerine göre konumlandığı için geçerli kalır")
        body = bytearray(b"fLaC")
        for n, (typ, p) in enumerate(out_blocks):
            last = 0x80 if n == len(out_blocks) - 1 else 0
            body += bytes([typ | last]) + len(p).to_bytes(3, "big") + p
        out = ctx.out_path(".flac")
        with open(out, "wb") as f:
            f.write(bytes(body) + data[audio_start:end])
        return out

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            data = f.read()
        lead: List[str] = []
        blocks, audio_start = parse_flac(data, lead)
        bad = [f"Kalan: {x}" for x in lead]
        for typ, p in blocks:
            if typ == 4:
                vendor, items = parse_vorbis_comment(p)
                if vendor or any(not i.upper().startswith(CHANNEL_MASK) for i in items):
                    bad.append("VORBIS_COMMENT izin listesi dışı alan içeriyor")
            elif typ not in (0, 3):
                bad.append(f"İzin listesi dışı blok: {FLAC_NAMES.get(typ, typ)}")
        trail: List[str] = []
        strip_trailing_tags(data, audio_start, len(data), trail)
        return bad + [f"Kalan: {t}" for t in trail]
