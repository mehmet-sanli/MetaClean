# Üçüncü taraf bileşenler

MetaClean paketleri aşağıdaki bileşenleri içerir. Her biri kendi lisansıyla dağıtılır.

| Bileşen | Ne için | Lisans | Kaynak |
|---|---|---|---|
| ExifTool (Phil Harvey) | Meta veri tarama, HEIC/AVIF temizliği | Perl lisansı (Artistic 1.0 ya da GPL 1+) | https://exiftool.org |
| FFmpeg / ffprobe | Ses ve videoyu yeniden kodlamadan yeniden paketleme, doğrulama | Linux: LGPL 2.1+ derlemesi (BtbN). macOS: GPL derlemesi (ffmpeg.martin-riedl.de) | https://ffmpeg.org |
| Qt 6 / PySide6 | Arayüz | LGPL 3 | https://www.qt.io, https://pypi.org/project/PySide6 |
| Python | Çalışma ortamı | PSF lisansı | https://www.python.org |
| Pillow | Görüntü çözme (doğrulama) | MIT-CMU (HPND) | https://python-pillow.org |
| pillow-heif / libheif | HEIC çözme | BSD-3 / LGPL 3 | https://github.com/bigcat88/pillow_heif |
| mutagen | Ses etiketleri | GPL 2+ | https://github.com/quodlibet/mutagen |

**LGPL ve GPL bileşenleri için:** Paketler bu kütüphaneleri değiştirmeden içerir; kaynak kodları yukarıdaki
adreslerdedir. FFmpeg'in GPL derlemesini içeren macOS paketini dağıtırken GPL koşulları (kaynak koda
erişim bildirimi) geçerlidir.
