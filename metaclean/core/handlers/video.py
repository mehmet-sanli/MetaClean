"""MP4/MOV/M4A ve MKV/WebM: FFmpeg ile remux (-c copy), izler tek tek seçilir."""
from __future__ import annotations

from typing import List, Tuple

from ...i18n import tr
from .. import ffverify, tools
from ..report import Gate
from .base import Context, Handler

MUXERS = {"mp4": ("mp4", ".mp4"), "mov": ("mov", ".mov"), "m4a": ("mp4", ".m4a"),
          "mkv": ("matroska", ".mkv"), "webm": ("webm", ".webm")}
FONT_MIMES = ("font/", "application/x-truetype-font", "application/vnd.ms-opentype",
              "application/x-font", "application/font-sfnt")
DATA_NAMES = {"gpmd": "GoPro GPMF telemetri (GPS, ivmeölçer)", "camm": "Kamera hareket/GPS izi (camm)",
              "tmcd": "Zaman kodu izi (günün saati)", "mebx": "Apple zamanlı meta veri izi (mebx)",
              "rtmd": "Sony gerçek zamanlı meta veri (rtmd)", "text": "QuickTime bölüm adları izi", "djmd": "DJI uçuş verisi izi"}


class AvHandler(Handler):
    def __init__(self, fmt: str):
        self.muxer, self.ext = MUXERS[fmt]
        self.is_mp4 = fmt in ("mp4", "mov", "m4a")

    # ------------------------------------------------------------ temizle
    def clean(self, ctx: Context) -> str:
        info = ffverify.probe(ctx.src, ctx=ctx)
        streams = info.get("streams", [])
        has_ass = any(s.get("codec_name") in ("ass", "ssa") for s in streams if s["codec_type"] == "subtitle")
        maps: List[str] = []
        meta: List[str] = []
        self.kept_index: List[int] = []
        for s in streams:
            t, idx, tags = s["codec_type"], s["index"], s.get("tags", {})
            tag = s.get("codec_tag_string", "")
            if s.get("disposition", {}).get("attached_pic"):
                ctx.removed(tr("İz {i}: kapak resmi ({codec}) – kendi EXIF'ini taşıyabilir", i=idx, codec=s.get("codec_name")))
                continue
            if t == "data" or (t == "video" and tag in DATA_NAMES):
                ctx.removed(tr("İz {i}: {name} [{tag}]", i=idx, name=tr(DATA_NAMES.get(tag, "veri izi")),
                               tag=tag or s.get("codec_name")))
                continue
            if t == "attachment":
                mime = tags.get("mimetype", "")
                if has_ass and mime.startswith(FONT_MIMES):
                    out_i = len(self.kept_index)
                    maps += ["-map", f"0:{idx}"]
                    meta += [f"-metadata:s:{out_i}", f"filename={tags.get('filename', f'font{out_i}.ttf')}",
                             f"-metadata:s:{out_i}", f"mimetype={mime}"]
                    self.kept_index.append(idx)
                    ctx.kept(tr("Ek {i}: yazı tipi {name}", i=idx, name=tags.get("filename", "")),
                             tr("ASS/SSA altyazının doğru çizilmesi için"))
                else:
                    ctx.removed(tr("Ek {i}: {name} ({mime})", i=idx, name=tags.get("filename") or tr("adsız"),
                               mime=mime or tr("bilinmeyen tür")))
                continue
            if t not in ("video", "audio", "subtitle"):
                ctx.removed(tr("İz {i}: {kind} {codec}", i=idx, kind=t, codec=s.get("codec_name")))
                continue
            out_i = len(self.kept_index)
            maps += ["-map", f"0:{idx}"]
            lang = tags.get("language")
            if lang and lang != "und":
                meta += [f"-metadata:s:{out_i}", f"language={lang}"]
            self.kept_index.append(idx)
        if not self.kept_index:
            raise ValueError(tr("Dosyada kopyalanacak görüntü/ses izi yok"))
        if info.get("chapters"):
            ctx.removed(tr("{n} bölüm (adları ve zamanları)", n=len(info["chapters"])))
        fmt_tags = info.get("format", {}).get("tags", {})
        if fmt_tags:
            ctx.removed("Genel etiketler: " + ", ".join(sorted(fmt_tags)))
        stream_tags = sorted({k for s in streams for k in s.get("tags", {})} - {"language"})
        if stream_tags:
            ctx.removed(tr("İz etiketleri: {tags}", tags=", ".join(stream_tags)))

        out = ctx.out_path(self.ext)
        args = [tools.require("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error", "-i", ctx.src, *maps,
                "-c", "copy", "-map_metadata", "-1", "-map_chapters", "-1", *meta, "-fflags", "+bitexact"]
        if self.is_mp4:
            # Dolby Vision yapılandırması (dvcC) yalnızca 'unofficial' kipte yazılır
            args += ["-strict", "unofficial", "-write_tmcd", "0"]
        args += ["-f", self.muxer, out]
        ctx.progress("Remux: ffmpeg -c copy")
        cp = tools.run(args, log=ctx.log, cancel=ctx.cancel)
        if cp.returncode != 0:
            raise tools.ToolError(tr("ffmpeg remux başarısız: {err}", err=cp.stderr.decode("utf-8", "replace").strip()))

        smpb = fmt_tags.get("iTunSMPB")
        if smpb and self.is_mp4:
            from mutagen.mp4 import MP4, MP4FreeForm
            m = MP4(out)
            m["----:com.apple.iTunes:iTunSMPB"] = [MP4FreeForm(smpb.encode("ascii", "replace"))]
            m.save()
            ctx.kept("iTunSMPB", tr("Kesintisiz çalma ve doğru süre (kodlayıcı gecikmesi / dolgu)"))

        for s in streams:
            if s["index"] in self.kept_index and s["codec_type"] in ("video", "audio", "subtitle"):
                ctx.kept(tr("İz {i}: {desc}", i=s["index"], desc=ffverify.describe(s)), tr("Kopyalandı (-c copy), yeniden kodlanmadı"))
            sd = {d.get("side_data_type") for d in s.get("side_data_list", [])}
            if s["index"] in self.kept_index and sd:
                ctx.kept(tr("İz {i} yan verisi: {data}", i=s["index"], data=", ".join(sorted(filter(None, sd)))),
                         tr("Döndürme matrisi / HDR / Dolby Vision; görüntülemeyi belirler"))
        ctx.kept(tr("Dil etiketleri"), tr("İz seçimi için; kimlik bilgisi taşımaz"))
        return out

    # ------------------------------------------------------------ doğrula
    def _src_maps(self) -> List[str]:
        return [a for i in self.kept_index for a in ("-map", f"0:{i}")]

    def verify(self, ctx: Context, out: str) -> Tuple[Gate, Gate]:
        pa_all = ffverify.probe(ctx.src, ctx=ctx)["streams"]
        pb = ffverify.probe(out, ctx=ctx)["streams"]
        pa = [next(s for s in pa_all if s["index"] == i) for i in self.kept_index]
        av = [s for s in pb if s["codec_type"] in ("video", "audio")]
        av_out = [a for s in av for a in ("-map", f"0:{s['index']}")]
        av_src = [a for i, s in zip(self.kept_index, pa) if s["codec_type"] in ("video", "audio")
                  for a in ("-map", f"0:{i}")]
        has_video = any(s["codec_type"] == "video" for s in av)
        decode = ctx.options.full_video_decode or not has_video

        ctx.progress(tr("Bütünlük: temiz dosyanın paketleri okunuyor"))
        table_b, err = ffverify.packet_table(out, ["-map", "0"], ctx=ctx)
        integrity_details = []
        if table_b is None:
            return (Gate(tr("Bütünlük"), False, [tr("Temiz dosya okunamadı: {err}", err=err)]),
                    Gate(tr("Eşdeğerlik"), False, [tr("Bütünlük geçilmediği için denenmedi.")]))
        integrity_details.append(tr("{n} paket hatasız okundu.", n=sum(len(v) for v in table_b.values())))
        hashes_b = None
        if decode and av_out:
            ctx.progress(tr("Bütünlük: temiz dosya baştan sona çözülüyor"))
            hashes_b, err = ffverify.decode_hashes(out, av_out, video=has_video, ctx=ctx)
            if hashes_b is None:
                return (Gate(tr("Bütünlük"), False, [tr("Çözme hatası: {err}", err=err)]),
                        Gate(tr("Eşdeğerlik"), False, [tr("Bütünlük geçilmediği için denenmedi.")]))
            integrity_details.append(tr("Tüm kareler/örnekler hatasız çözüldü (ffmpeg -xerror)."))
        else:
            integrity_details.append(tr("Hızlı kip: kapsayıcı ve paketler doğrulandı; kare kare çözme için "
                                        "Ayarlar'dan 'Tam doğrulama'yı açın."))
        integrity = Gate(tr("Bütünlük"), True, integrity_details)

        ctx.progress(tr("Eşdeğerlik: sıkıştırılmış paketler karşılaştırılıyor"))
        table_a, err = ffverify.packet_table(ctx.src, self._src_maps(), ctx=ctx)
        if table_a is None:
            return integrity, Gate(tr("Eşdeğerlik"), False, [tr("Orijinal okunamadı: {err}", err=err)])
        problems = ffverify.compare_packets(table_a, table_b)
        details = [tr("{tracks} iz, {packets} paket: veri (MD5) ve zamanlama karşılaştırıldı.",
                      tracks=len(table_a), packets=sum(len(v) for v in table_a.values()))]
        problems += ffverify.compare_streams(pa, pb)
        if decode and av_src:
            ctx.progress(tr("Eşdeğerlik: orijinal çözülüyor"))
            hashes_a, err = ffverify.decode_hashes(ctx.src, av_src, video=has_video, ctx=ctx)
            if hashes_a is None:
                problems.append(tr("Orijinal çözülemedi: {err}", err=err))
            elif hashes_a != hashes_b:
                problems.append(tr("Çözülmüş kare/örnek özetleri farklı"))
            else:
                details += [tr("Çözülmüş: {h}", h=h) for h in hashes_a]
        details += problems
        if not problems:
            details.append(tr("Sıkıştırılmış veri bit düzeyinde aynı; çözünürlük, renk bilgisi, döndürme, "
                              "HDR yan verisi, örnekleme hızı ve kare sayısı eşleşiyor."))
        return integrity, Gate(tr("Eşdeğerlik"), not problems, details)

    def structural(self, ctx: Context, out: str) -> List[str]:
        info = ffverify.probe(out, ctx=ctx)
        bad = []
        allowed_fmt = {"major_brand", "minor_version", "compatible_brands", "iTunSMPB", "encoder"}
        for k, v in info.get("format", {}).get("tags", {}).items():
            if k not in allowed_fmt or (k == "encoder" and v != "Lavf"):
                bad.append(tr("Genel etiket kaldı: {k}={v}", k=k, v=v))
        allowed_stream = {"language", "handler_name", "vendor_id", "DURATION", "filename", "mimetype", "encoder"}
        for s in info.get("streams", []):
            for k, v in s.get("tags", {}).items():
                if k not in allowed_stream:
                    bad.append(tr("İz {i} etiketi kaldı: {k}={v}", i=s["index"], k=k, v=v))
            if s["codec_type"] == "data":
                bad.append(tr("Veri izi kaldı: {tag}", tag=s.get("codec_tag_string")))
        if info.get("chapters"):
            bad.append(tr("Bölümler kaldı"))
        return bad
