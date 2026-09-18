"""Kanita dayali, deterministik ticari sektor taksonomisi."""

from __future__ import annotations

import json


RULES = [
    ("Yeme-İçme & Gastronomi", "Kebapçı & Ocakbaşı", ["kebap", "ocakbasi", "ocakbaşı", "ciger", "ciğer"], ["Adana Kebap", "Urfa Kebap", "İskender", "Izgara"]),
    ("Yeme-İçme & Gastronomi", "3. Nesil Kahveci", ["coffee", "kahve", "roastery", "espresso"], ["Espresso", "Filtre Kahve", "Cold Brew", "Tatlı"]),
    ("Yeme-İçme & Gastronomi", "Kafe", ["cafe", "kafe", "çay bahçesi", "cay bahcesi"], ["Kahvaltı", "Sıcak İçecek", "Atıştırmalık"]),
    ("Yeme-İçme & Gastronomi", "Restoran & Lokanta", ["restaurant", "restoran", "lokanta", "ev yemegi", "ev yemeği"], ["Ana Yemek", "Günün Menüsü"]),
    ("Yeme-İçme & Gastronomi", "Fast Food", ["fast_food", "burger", "hamburger", "pizza", "doner", "döner"], ["Paket Servis", "Hızlı Servis"]),
    ("Yeme-İçme & Gastronomi", "Pastane & Fırın", ["bakery", "firin", "fırın", "pastane", "borek", "börek"], ["Ekmek", "Unlu Mamul", "Tatlı"]),
    ("Gıda & Perakende", "Süpermarket", ["supermarket", "hipermarket"], ["Gıda", "Temizlik", "Ev İhtiyaçları"]),
    ("Gıda & Perakende", "Bakkal & Market", ["convenience", "bakkal", "market"], ["Temel Gıda", "İçecek", "Atıştırmalık"]),
    ("Gıda & Perakende", "Manav", ["greengrocer", "manav"], ["Meyve", "Sebze"]),
    ("Gıda & Perakende", "Kuruyemişçi", ["nuts", "kuruyemis", "kuruyemiş"], ["Kuruyemiş", "Kuru Meyve", "Lokum"]),
    ("Gıda & Perakende", "Kasap", ["butcher", "kasap"], ["Kırmızı Et", "Beyaz Et", "Şarküteri"]),
    ("Moda & Giyim", "Giyim Mağazası", ["clothes", "giyim", "butik", "tekstil"], ["Giyim", "Aksesuar"]),
    ("Elektronik & Bilişim", "Elektronik & Bilgisayar", ["electronics", "computer", "telefoncu", "bilgisayar"], ["Elektronik", "Bilgisayar", "Telefon"]),
    ("Sağlık & Medikal", "Diş Kliniği", ["dentist", "dis klinigi", "diş kliniği"], ["Diş Tedavisi", "İmplant", "Ortodonti"]),
    ("Sağlık & Medikal", "Eczane & Medikal", ["pharmacy", "eczane", "medical", "medikal"], ["İlaç", "Medikal Ürün"]),
    ("Kişisel Bakım", "Kuaför & Güzellik", ["hairdresser", "beauty", "kuafor", "kuaför", "guzellik", "güzellik"], ["Saç", "Bakım", "Kozmetik"]),
    ("Yapı & Tesisat", "Elektrikçi", ["electrician", "elektrikci", "elektrikçi"], ["Elektrik Tesisatı", "Arıza"]),
    ("Yapı & Tesisat", "Tesisatçı", ["plumber", "tesisatci", "tesisatçı"], ["Su Tesisatı", "Isıtma"]),
    ("Otomotiv & Ulaşım", "Otomotiv Servisi", ["car_repair", "oto servis", "otomotiv"], ["Bakım", "Onarım"]),
    ("Konaklama & Turizm", "Otel & Konaklama", ["hotel", "otel", "hostel", "pansiyon"], ["Konaklama", "Kahvaltı"]),
    ("Eğitim & Kültür", "Eğitim Kurumu", ["school", "kurs", "egitim", "eğitim"], ["Eğitim", "Kurs"]),
    ("Finans & Profesyonel Hizmet", "Profesyonel Hizmet", ["bank", "sigorta", "muhasebe", "avukat"], ["Finans", "Danışmanlık"]),
]


def classify(name=None, raw_category=None, raw_subcategories=None):
    raw = raw_subcategories
    if isinstance(raw, str):
        try:
            raw = " ".join(str(x) for x in json.loads(raw))
        except Exception:
            pass
    text = f"{name or ''} {raw_category or ''} {raw or ''}".casefold()
    for sector, category, keywords, services in RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            return sector, category, json.dumps(services, ensure_ascii=False)
    return "Diğer Ticari Hizmetler", (raw_category or "Sınıflandırılmamış"), None
