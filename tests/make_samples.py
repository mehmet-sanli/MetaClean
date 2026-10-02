"""Bol meta verili test dosyaları üretir (ffmpeg, exiftool, Pillow, mutagen gerekir).

Kullanım: python tests/make_samples.py HEDEF_KLASÖR
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

from PIL import Image, ImageCms
from PIL.PngImagePlugin import PngInfo

FF = ["ffmpeg", "-y", "-hide_banner", "-v", "error"]


def sh(*args):
    subprocess.run(args, check=True)


def exif_bytes(orientation: int) -> bytes:
    im = Image.new("RGB", (1, 1))
    ex = im.getexif()
    ex[0x010F] = "Canon"            # Make
    ex[0x0110] = "EOS Gizli"        # Model
    ex[0x0131] = "Firmware 1.2.3"   # Software
    ex[0x0132] = "2026:08:15 14:22:33"
    ex[0x013B] = "Ayşe Yılmaz"      # Artist
    ex[0x0112] = orientation
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (41.0, 0.0, 29.5), "E", (28.0, 58.0, 42.0)
    ex.get_ifd(0x8769)[0x9003] = "2026:08:15 14:22:33"  # DateTimeOriginal
    ex.get_ifd(0x8769)[0xA431] = "SN-123456"            # BodySerialNumber
    return ex.tobytes()


def gradient(w=320, h=200, mode="RGB"):
    im = Image.new("RGB", (w, h))
    im.putdata([(x * 255 // w, y * 255 // h, (x + y) % 256) for y in range(h) for x in range(w)])
    return im.convert(mode)


def main(d: str):
    os.makedirs(d, exist_ok=True)
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    p = lambda n: os.path.join(d, n)

    # JPEG: EXIF(+GPS, küçük resim), XMP, IPTC, COM, ICC, sonda "hareketli fotoğraf" videosu
    gradient().save(p("foto.jpg"), quality=90, exif=exif_bytes(6), icc_profile=srgb, comment=b"gizli yorum")
    thumb = io.BytesIO()
    gradient(80, 50).save(thumb, "JPEG")
    open(p("thumb.jpg"), "wb").write(thumb.getvalue())
    sh("exiftool", "-q", "-overwrite_original", "-XMP-dc:Creator=Ayşe", "-XMP-photoshop:City=İstanbul",
       "-IPTC:Keywords=gizli", "-IPTC:By-line=Ayşe", f"-ThumbnailImage<={p('thumb.jpg')}", p("foto.jpg"))
    os.remove(p("thumb.jpg"))
    with open(p("foto.jpg"), "ab") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"VIDEO" * 100)

    # PNG: metin, eXIf (yön 8), ICC, pHYs, tIME, IEND sonrası çöp
    info = PngInfo()
    info.add_text("Author", "Ayşe Yılmaz")
    info.add_itxt("Comment", "Konum: İstanbul")
    gradient(mode="RGBA").save(p("ekran.png"), pnginfo=info, exif=exif_bytes(8), icc_profile=srgb, dpi=(144, 144))
    sh("exiftool", "-q", "-overwrite_original", "-PNG:CreationTime=2026:08:15 14:22:33", "-XMP-dc:Rights=gizli", p("ekran.png"))
    with open(p("ekran.png"), "ab") as f:
        f.write(b"TRAILER")

    # WebP: EXIF, XMP, ICC
    gradient().save(p("resim.webp"), quality=80, exif=exif_bytes(3), icc_profile=srgb,
                    xmp=b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'/></x:xmpmeta>")

    # HEIC
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        gradient().save(p("iphone.heic"), quality=80, exif=exif_bytes(1), icc_profile=srgb)
    except Exception as e:  # noqa: BLE001
        print("HEIC atlandı:", e)

    sine = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    # MP3: ID3v2 (+kapak), ID3v1, APE
    cover = p("kapak.jpg")
    open(cover, "wb").write(thumb.getvalue())
    sh(*FF, *sine, "-i", cover, "-map", "0", "-map", "1:v",
       "-c:a", "libmp3lame", "-b:a", "128k", "-c:v", "copy", "-disposition:v", "attached_pic",
       "-id3v2_version", "3", "-write_id3v1", "1", "-metadata", "title=Gizli Kayıt", "-metadata", "artist=Ayşe",
       "-metadata", "comment=Kadıköy'de kaydedildi", p("ses.mp3"))
    from mutagen.apev2 import APEv2
    from mutagen.id3 import COMM, ID3
    t = ID3(p("ses.mp3"))
    t.add(COMM(encoding=0, lang="eng", desc="iTunSMPB", text=[" 00000000 00000210 0000096C 0000000000020D54"]))
    t.save(v2_version=3)
    ape = APEv2()
    ape["Artist"] = "Ayşe"
    ape.save(p("ses.mp3"))

    # FLAC: etiketler, kapak, kanal maskesi, dolgu
    sh(*FF, *sine, "-ac", "2", "-c:a", "flac", "-metadata", "title=Gizli", "-metadata", "artist=Ayşe", p("ses.flac"))
    from mutagen.flac import FLAC, Picture
    fl = FLAC(p("ses.flac"))
    fl["WAVEFORMATEXTENSIBLE_CHANNEL_MASK"] = "0x0003"
    fl["LOCATION"] = "İstanbul"
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", thumb.getvalue()
    fl.add_picture(pic)
    fl.save()

    meta = ["-metadata", "title=Tatil", "-metadata", "location=+41.0082+028.9784/",
            "-metadata", "creation_time=2026-08-15T14:22:33Z", "-metadata", "artist=Ayşe",
            "-metadata", "com.apple.quicktime.make=Apple", "-metadata", "com.apple.quicktime.model=iPhone 17"]
    chapters = p("bolumler.txt")
    open(chapters, "w").write(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Evde\n"
                              "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\ntitle=Sokakta\n")
    srt = p("altyazi.srt")
    open(srt, "w").write("1\n00:00:00,500 --> 00:00:02,000\nMerhaba\n")
    vid = ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3"]
    a2 = ["-f", "lavfi", "-i", "sine=frequency=880:duration=3"]

    # MP4: H.264 (B-kare), 2 ses izi, altyazı, zaman kodu, 90° döndürme, bölümler, kapak
    sh(*FF, *vid, *sine, *a2, "-i", srt, "-i", chapters,
       "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map_metadata", "4", "-map_chapters", "4",
       "-c:v", "libx264", "-bf", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "mov_text",
       "-metadata:s:a:0", "language=tur", "-metadata:s:a:1", "language=eng",
       "-metadata:s:v", "handler_name=Core Media Video", "-timecode", "14:22:33:00", *meta, p("ham.mp4"))
    sh(*FF, "-display_rotation", "90", "-i", p("ham.mp4"), "-map", "0:v", "-map", "0:a", "-map", "0:s", "-c", "copy", "-map_metadata", "0",
       "-timecode", "14:22:33:00", p("video.mp4"))
    os.remove(p("ham.mp4"))

    # MOV: HEVC hvc1, HDR renk etiketleri, Apple anahtarları
    sh(*FF, *vid, *sine, "-c:v", "libx265", "-x265-params", "log-level=error", "-tag:v", "hvc1",
       "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
       "-c:a", "aac", "-movflags", "use_metadata_tags", *meta, p("iphone.mov"))

    # M4A: AAC, kapak, iTunSMPB
    sh(*FF, *sine, "-i", cover, "-map", "0", "-map", "1:v",
       "-c:a", "aac", "-c:v", "copy", "-disposition:v", "attached_pic", "-metadata", "title=Gizli",
       "-metadata", "artist=Ayşe", p("ses.m4a"))
    from mutagen.mp4 import MP4, MP4FreeForm
    m = MP4(p("ses.m4a"))
    m["----:com.apple.iTunes:iTunSMPB"] = [MP4FreeForm(b" 00000000 00000840 000003C0 0000000000020C40")]
    m.save()

    # MKV: 2 ses, ASS altyazı + yazı tipi eki + jpeg eki, bölümler, etiketler
    ass = p("altyazi.ass")
    open(ass, "w").write("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
                         "Style: Default,Arial,20\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
                         "Dialogue: 0,0:00:00.50,0:00:02.00,Default,Merhaba\n")
    open(p("font.ttf"), "wb").write(b"\x00\x01\x00\x00" + b"\x00" * 60)
    open(p("ek.jpg"), "wb").write(thumb.getvalue())
    sh(*FF, *vid, *sine, *a2, "-i", ass, "-i", chapters,
       "-attach", p("font.ttf"), "-metadata:s:t:0", "mimetype=font/ttf",
       "-attach", p("ek.jpg"), "-metadata:s:t:1", "mimetype=image/jpeg",
       "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map_metadata", "4", "-map_chapters", "4",
       "-c:v", "libx264", "-c:a", "libopus", "-c:s", "copy", "-metadata:s:a:0", "language=tur",
       "-metadata:s:a:0", "title=Ayşe'nin yorumu", *meta, p("film.mkv"))

    # WebM
    sh(*FF, *vid, *sine, "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", *meta, p("klip.webm"))

    for n in ("kapak.jpg", "bolumler.txt", "altyazi.srt", "altyazi.ass", "font.ttf", "ek.jpg"):
        os.remove(p(n))
    print("Üretildi:", sorted(os.listdir(d)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ornekler")
