# MetaClean

Sürümü öğrenmek için: `python -m metaclean --version` ya da uygulamada **Diğer → MetaClean hakkında**.

Görsel, ses ve video dosyalarından meta veriyi **yeniden kodlamadan** silen, sonucu kanıtlamadan
kaydetmeyen, Qt6 (PySide6) arayüzlü bir masaüstü uygulaması. Windows, macOS ve Linux'ta çalışır.

## Kullanıcılar için kurulum

### Hazır paket (önerilen; hiçbir şey kurmanız gerekmez)
Python, Qt, ExifTool ve FFmpeg paketin içinde gelir. Paketler GitHub Actions ile otomatik derlenir
(`.github/workflows/build.yml`); Actions sayfasındaki **Artifacts** bölümünden ya da `v*` etiketiyle
açılan **Releases** sayfasından indirilir.

| Sistem | Dosya | Kurulum |
|---|---|---|
| Windows 10/11 | `MetaClean-Windows-x64.zip` | Zip'i açın, `MetaClean\MetaClean.exe`'ye çift tıklayın |
| macOS (M1/M2/M3…) | `MetaClean-macOS-AppleSilicon.dmg` | Açın, `MetaClean`'i Uygulamalar'a sürükleyin |
| Linux (x64) | `MetaClean-Linux-x64.tar.gz` | Açın, klasörde `./install_bundle.sh` çalıştırın |

Paketler imzasız olduğu için ilk açılışta uyarı çıkabilir:
- **macOS:** Uygulamaya sağ tıklayın → **Aç** → **Aç**.
- **Windows:** "Windows bilgisayarınızı korudu" ekranında **Ek bilgi** → **Yine de çalıştır**.

### Kaynak koddan
| Sistem | Komut |
|---|---|
| macOS | `./install_mac.sh` (gerekirse önce `brew install exiftool ffmpeg`). Uygulamalar'a **MetaClean (kaynak)** adıyla kurulur |
| Windows | `install_windows.bat` dosyasına çift tıklayın (Python, ExifTool, FFmpeg'i winget ile kurar) |
| Linux | `./install_linux.sh` (apt, dnf ya da pacman ile eksikleri kurar) |

Kurulumu sınamak için:
```
python -m metaclean --selftest
```

## Testler

```
python -m unittest discover -s tests -v
```

`tests/make_samples.py` GPS, kamera, yorum, kapak resmi, bölüm, zaman kodu izi gibi bol meta veri
içeren örnekler üretir. Testler 11 formatın üç kapıdan geçtiğini, kapıların bilerek bozulmuş
çıktıyı reddettiğini (tek bit piksel değişimi, unutulmuş yorum, kaybolan döndürme) ve kaydetme
akışını (izinler, sembolik bağ, orijinalin araya girip değişmesi, zaman damgaları) doğrular.

## Desteklenen formatlar ve yöntem

| Format | Yöntem | Bilerek korunan |
|---|---|---|
| JPEG | Segmentler Python'da ayrıştırılır, tarama verisi bayt bayt kopyalanır | Orientation (tek alanlı yeni EXIF), ICC, Adobe APP14, JFIF (küçük resimsiz) |
| PNG | Chunk izin listesi; IDAT aynen | Orientation (eXIf), iCCP, gAMA, cHRM, sRGB, cICP, mDCV, cLLI, APNG |
| WebP | RIFF chunk izin listesi, VP8X bayrakları güncellenir | Orientation, ICCP, animasyon, alfa |
| HEIC, AVIF | ExifTool `-all= --icc_profile:all` + Orientation geri yazılır | ICC/colr, irot/imir |
| MP3 | ID3v1/v2, APE, Lyrics3 kesilir | Xing/LAME başlığı, ID3 COMM:iTunSMPB |
| FLAC | Meta veri blokları yeniden yazılır | STREAMINFO, SEEKTABLE, kanal maskesi |
| MP4, MOV, M4A | `ffmpeg -c copy`, izler tek tek seçilir | Döndürme, HDR/Dolby Vision, dil, iTunSMPB |
| MKV, WebM | `ffmpeg -c copy` | Dil, ASS altyazı yazı tipleri |

Format, uzantıya göre değil dosya imzasına göre tanınır. Tanınmayan dosya reddedilir.

## Paketleme

```
pip install pyinstaller
python packaging/fetch_tools.py          # ExifTool ve FFmpeg'i packaging/bin/ içine indirir
pyinstaller --noconfirm packaging/metaclean.spec
dist/MetaClean/MetaClean --selftest      # macOS: dist/MetaClean.app/Contents/MacOS/MetaClean
```

Her paket kendi işletim sisteminde derlenmelidir; bunu GitHub Actions üç makinede (Windows, macOS, Linux) yapar.
Simgeler `metaclean/gui/assets/logo.svg`'den `python packaging/make_icons.py` ile üretilir.

**Lisanslar:** Ayrıntılar `THIRD_PARTY_NOTICES.md` dosyasında. ExifTool Perl lisansı (Artistic/GPL) ile dağıtılır. Windows ve Linux paketlerindeki
FFmpeg LGPL derlemesidir; macOS paketindeki statik derleme GPL'dir. Paketleri dağıtırken bu lisansların
koşullarına (kaynak koda erişim bildirimi) uyun.

## Yapı

```
metaclean/
  core/detect.py        imzadan format tanıma
  core/handlers/        format başına temizle + doğrula (images, audio, video)
  core/ffverify.py      PCM/kare/paket özetleri ve parametre karşılaştırması
  core/allowlist.py     ExifTool taramasının izin listesi (kapalı varsayılan)
  core/session.py       temp -> 3 kapı -> onay -> atomik kaydetme
  core/timestamps.py    SetFileTime (Windows), setattrlist (macOS), os.utime
  core/fsops.py         kilitli dosyada yeniden deneme, salt okunur denetimi
  applog.py             kayıt: dosya adı ve yolu yazmaz
  gui/                  Qt6 arayüzü (simple_window: ana ekran, advanced_window: gelişmiş mod)
packaging/              PyInstaller tarifi, araç indirici, simge üretici, Linux .desktop
.github/workflows/      Windows / macOS / Linux için otomatik test ve paketleme
install_*.sh/.bat       Kaynak koddan kurulum betikleri
```

## Gizlilik ve kayıt dosyası
MetaClean internete bağlanmaz ve veri toplamaz. Hata ayıklama kaydına dosya adı ya da yolu yazılmaz;
yollar `<dosya>.jpg` biçimine indirilir. Kayıt yeri: macOS `~/Library/Logs/MetaClean/`,
Linux `~/.local/state/metaclean/`, Windows `%LOCALAPPDATA%\MetaClean\Logs`. Dosya 512 KB'ı aşınca eskisi silinir.
