"""Dil desteği. Kaynak metinler Türkçedir; tr() seçili dile çevirir, çevirisi yoksa Türkçe kalır.

tr() bir Text döndürür: ekranda görünen çeviridir ama Türkçe aslını .source'ta taşır. Kod karar verirken
(ör. "silinen öğe kapak resmi mi?") görünen dile değil source()'a bakar; dil değişince mantık bozulmaz.
Dil açılışta bir kez seçilir (set_language); değişiklik uygulama yeniden açılınca geçerli olur.
"""
from __future__ import annotations

LANGS = {"tr": "Türkçe", "en": "English"}
_lang = "tr"


class Text(str):
    """Çevrilmiş metin; .source Türkçe aslıdır (değerler yerleştirilmiş hâliyle)."""
    source: str


def set_language(code: str) -> None:
    global _lang
    _lang = code if code in LANGS else "tr"


def language() -> str:
    return _lang


def resolve(choice: str, system_languages) -> str:
    """Ayardaki seçim ("auto", "tr", "en") ve sistemin tercih ettiği diller -> kullanılacak dil.
    Otomatikte Türkçe sistem Türkçe, diğer her dil İngilizce açılır."""
    if choice in LANGS:
        return choice
    first = next(iter(system_languages), "") or ""
    return "tr" if first.lower().startswith("tr") else "en"


def tr(template: str, **values) -> Text:
    # İç içe çeviride (değer de bir Text ise) Türkçe asıl, değerlerin de Türkçe aslıyla kurulur
    source = template.format(**{k: getattr(v, "source", v) for k, v in values.items()}) if values else template
    if _lang == "tr":
        shown = source
    else:
        from .i18n_en import EN
        shown = EN.get(template, template)
        shown = shown.format(**values) if values else shown
    text = Text(shown)
    text.source = source
    return text


def source(text) -> str:
    """Bir metnin Türkçe aslı (tr() ile üretilmediyse kendisi)."""
    return getattr(text, "source", text)
