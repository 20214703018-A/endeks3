"""Türkçe metin normalleştirme ve sayı biçimleme (deterministik)."""
import re
import unicodedata

_TR = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiucgiosuaiu")
# yer adlarında anlam taşımayan ekler (geo_matcher v2 ile aynı liste + birkaç yaygın yazım)
_YER_EKLERI = r"\b(mah|mh|mahallesi|mahalle|koyu|koy|beldesi|belde|bld|osb|ilcesi|ili)\b"  # 'merkez' gerçek ilçe adı olduğu için silinmez


def fold(s: str | None) -> str:
    """Küçük harf + Türkçe harfleri ASCII'ye katla; boşlukları korur."""
    if not s:
        return ""
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(_TR)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def norm(s: str | None) -> str:
    """Kanonik name_norm ile aynı: katla + harf/rakam dışını sil."""
    return fold(s).replace(" ", "")


def norm_yer(s: str | None) -> str:
    """Yer adı: 'Moda Mahallesi' → 'moda', 'Kadıköy ilçesi' → 'kadikoy'."""
    f = fold(s)
    f = re.sub(r"\((.*?)\)", " ", f)
    f = re.sub(_YER_EKLERI, " ", f)
    return re.sub(r"[^a-z0-9]+", "", f) or norm(s)


def tr_sayi(x, ondalik: int | None = None) -> str:
    """Türkçe sayı biçimi: 1.234.567,89. None → '—'."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "evet" if x else "hayır"
    if isinstance(x, float) and ondalik is None and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    if isinstance(x, int) or (isinstance(x, float) and ondalik == 0):
        return f"{round(x):,}".replace(",", ".")
    if ondalik is None:
        a = abs(x)
        ondalik = 0 if a >= 1000 else (1 if a >= 100 else 2)
    s = f"{x:,.{ondalik}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def ilk_kucuk(s: str) -> str:
    """Cümle içi kullanım: yalnız ilk harfi Türkçe kurala göre küçült ('İşletme' → 'işletme'; 'TÜİK' içeride korunur)."""
    if not s:
        return s
    ilk = {"İ": "i", "I": "ı"}.get(s[0], s[0].lower())
    return ilk + s[1:]
