"""ExifTool taramasını 'yapısal' ve 'hassas' alanlara ayırır.

Kural: listede olmayan her alan hassas sayılır (kapalı varsayılan). Liste yalnızca
dosyanın çözülüp doğru gösterilmesi için gereken yapısal alanları ve bilerek
korunan yük taşıyan alanları içerir. Temiz çıktının taramasında tek bir hassas
alan bile kalırsa Temizlik kapısı kapanır.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Union

from .report import Field

IGNORED_GROUPS = {"System", "ExifTool", "Composite"}
ZERO_DATE = re.compile(r"^0000:00:00 00:00:00")
FONT_MIME = re.compile(r"^(font/|application/(x-truetype-font|vnd\.ms-opentype|x-font|font-sfnt))")

Rule = Union[bool, Callable[[str], bool]]

_QT_STRUCT = """MajorBrand MinorVersion CompatibleBrands Wide Free Skip MediaDataSize MediaDataOffset MediaData
MovieHeaderVersion TimeScale Duration PreferredRate PreferredVolume MatrixStructure PreviewTime PreviewDuration
PosterTime SelectionTime SelectionDuration CurrentTime NextTrackID HandlerType HandlerVendorID
HEVCConfigurationVersion GeneralProfileSpace GeneralTierFlag GeneralProfileIDC GenProfileCompatibilityFlags
ConstraintIndicatorFlags GeneralLevelIDC MinSpatialSegmentationIDC ParallelismType ChromaFormat BitDepthLuma
BitDepthChroma AverageFrameRate ConstantFrameRate NumTemporalLayers TemporalIDNested ImageSpatialExtent
ImagePixelDepth Rotation ImageMirror AV1ConfigurationVersion SeqProfile SeqLevelIdx0 SeqTier0 HighBitDepth
TwelveBit Monochrome ChromaSubsamplingX ChromaSubsamplingY ChromaSamplePosition InitialDelaySamples
ColorProfiles ColorPrimaries TransferCharacteristics MatrixCoefficients VideoFullRangeFlag
PrimaryItemReference""".split()

_TRACK_STRUCT = """TrackHeaderVersion TrackID TrackDuration TrackLayer TrackVolume MatrixStructure ImageWidth ImageHeight
MediaHeaderVersion MediaTimeScale MediaDuration MediaLanguageCode HandlerClass HandlerType GraphicsMode OpColor
CompressorID SourceImageWidth SourceImageHeight XResolution YResolution BitDepth VideoFrameRate PixelAspectRatio
VideoFieldOrder AVCConfiguration HEVCConfiguration AudioFormat AudioChannels AudioBitsPerSample AudioSampleRate
Balance AverageBitrate MaxBitrate BufferSize ChunkOffset ChunkOffset64 SampleSizes SampleToChunk TimeToSampleTable
CompositionTimeToSample SyncSampleTable SampleGroupDescription SampleToGroup IdependentAndDisposableSamples
NullMediaHeader OtherFormat LayoutFlags PurchaseFileFormat AudioChannelLayout ChannelLayout
ColorProfiles ColorPrimaries TransferCharacteristics MatrixCoefficients VideoFullRangeFlag CleanAperture
FontTable Text SampleTime SampleDuration
TrackNumber TrackUID TrackLacing TrackLanguage TrackDefault TrackForced TrackType CodecID CodecPrivate
VideoScanType DisplayWidth DisplayHeight DisplayUnit DefaultDuration MaxBlockAdditionID TagTrackUID
CodecDelay SeekPreRoll""".split()

_MKV_STRUCT = """EBMLVersion EBMLReadVersion EBMLMaxIDLength EBMLMaxSizeLength DocType DocTypeVersion DocTypeReadVersion
SeekID SeekPosition CueTime CueTrack CueClusterPosition CueRelativePosition CueDuration CRC-32 Void
AttachedFileData AttachedFileUID""".split()

RULES: Dict[str, Dict[str, Rule]] = {
    "File": {"*": True, "Comment": False},  # JPEG COM yorumu File grubunda görünür
    "JFIF": {k: True for k in ("JFIFVersion", "ResolutionUnit", "XResolution", "YResolution")},
    "Adobe": {k: True for k in ("DCTEncodeVersion", "APP14Flags0", "APP14Flags1", "ColorTransform")},
    # YCbCrPositioning ve çözünürlük: ExifTool HEIC'te yeni EXIF yazarken zorunlu alan olarak ekler
    # (sürüme göre değişir); 72 dpi gibi sabit değerler, kişisel bilgi taşımaz
    "IFD0": {"Orientation": True, "YCbCrPositioning": True,
             "XResolution": True, "YResolution": True, "ResolutionUnit": True},
    "PNG": {k: True for k in ("ImageWidth", "ImageHeight", "BitDepth", "ColorType", "Compression", "Filter",
                              "Interlace", "ProfileName", "Gamma", "WhitePointX", "WhitePointY", "RedX", "RedY",
                              "GreenX", "GreenY", "BlueX", "BlueY", "SRGBRendering", "Palette", "Transparency",
                              "SignificantBits", "AnimationFrames", "AnimationPlays")},
    "RIFF": {k: True for k in ("VP8Version", "ImageWidth", "ImageHeight", "HorizontalScale", "VerticalScale",
                               "WebP_Flags", "AlphaIsUsed", "AlphaPreprocessing", "AlphaFiltering",
                               "AlphaCompression", "BackgroundColor", "AnimationLoopCount", "FrameCount")},
    "MPEG": {"*": True},   # MP3 çerçeve başlığı + Xing/LAME (bilerek korunur)
    "FLAC": {"*": True},   # STREAMINFO
    "Vorbis": {"Vendor": lambda v: v == "", "WaveformatextensibleChannelMask": True},
    "ID3v2_2": {"Comment": lambda v: v.startswith("(iTunSMPB)")},
    "ID3v2_3": {"Comment": lambda v: v.startswith("(iTunSMPB)")},
    "ID3v2_4": {"Comment": lambda v: v.startswith("(iTunSMPB)")},
    "iTunes": {"iTunSMPB": True},
    "QuickTime": {k: True for k in _QT_STRUCT},
    "Meta": {"PrimaryItemReference": True, "Free": True},
    "Track": {**{k: True for k in _TRACK_STRUCT},
              # FFmpeg'in varsayılan işleyici adları; "Core Media Video" gibi kaynak adları kalmamalı
              "HandlerDescription": lambda v: v == "" or v.endswith("Handler"),
              "VendorID": lambda v: v in ("", "FFmpeg"),
              "Duration": True},
    "Matroska": {**{k: True for k in _MKV_STRUCT},
                 # Eski ExifTool sürümleri (ör. Ubuntu 24.04'teki 12.76) Info ve Track alanlarını da bu grupta verir
                 "TimecodeScale": True, "Duration": True, "TagTrackUID": True,
                 "MuxingApp": lambda v: v == "Lavf", "WritingApp": lambda v: v == "Lavf",
                 "AttachedFileMIMEType": lambda v: bool(FONT_MIME.match(v)),
                 "AttachedFileName": lambda v: v.lower().endswith((".ttf", ".otf", ".ttc", ".woff", ".woff2"))},
    # HDR kazanç haritası (Ultra HDR / ISO 21496-1 / Apple): yalnızca görüntüleme parametreleri
    "XMP-hdrgm": {"*": True},
    "XMP-apdi": {"*": True},
    "XMP-HDRGainMap": {"*": True},
    "XMP-GContainer": {k: True for k in ("DirectoryItemSemantic", "DirectoryItemMime", "DirectoryItemLength",
                                         "DirectoryItemPadding")},
    "MPF0": {k: True for k in ("MPFVersion", "NumberOfImages")},
    "MPImage": {k: True for k in ("MPImageFlags", "MPImageFormat", "MPImageType", "MPImageLength", "MPImageStart",
                                  "DependentImage1EntryNumber", "DependentImage2EntryNumber")},
    "Google": {"GainMapImage": True},
    "Info": {"TimecodeScale": True, "Duration": True, "CRC-32": True,
             "MuxingApp": lambda v: v == "Lavf", "WritingApp": lambda v: v == "Lavf"},
}


def _norm_group(group: str) -> str:
    if re.fullmatch(r"Track\d+", group):
        return "Track"
    if re.fullmatch(r"MPImage\d+", group):
        return "MPImage"
    return group


def is_allowed(group: str, name: str, value: object) -> bool:
    if group in IGNORED_GROUPS:
        return True
    # ICC profili bilerek korunur (renk doğruluğu); alt grupları: ICC_Profile, ICC-header, ICC-chrm…
    if group.startswith("ICC"):
        return True
    g = _norm_group(group)
    rules = RULES.get(g)
    if rules is None:
        return False
    v = str(value)
    if g in ("QuickTime", "Track") and name.endswith("Date"):
        return bool(ZERO_DATE.match(v))
    # Çözülemeyen kutular/öğeler: çıktıyı FFmpeg sıfırdan yazdığı için yalnızca yapısal olabilirler
    if g in ("Track", "QuickTime") and name.startswith("Unknown_"):
        return True
    if g in ("Matroska", "Track") and name.startswith("Matroska_0x"):
        return True
    if g == "MPImage" and re.fullmatch(r"MPImage\d+", name):
        return True  # MPF'nin işaret ettiği görüntünün kendisi; içeriğini yapısal denetim doğrular
    rule = rules.get(name, rules.get("*", False))
    return rule(v) if callable(rule) else bool(rule)


def _short(v: object) -> str:
    s = str(v)
    return s if len(s) <= 160 else s[:157] + "…"


def sensitive(scan: Dict[str, object], fmt: str = "") -> List[Field]:
    out = []
    for key, value in scan.items():
        if ":" not in key:
            continue  # SourceFile
        group, name = key.split(":", 1)
        if not is_allowed(group, name, value):
            out.append(Field(group, name, _short(value)))
    return out
