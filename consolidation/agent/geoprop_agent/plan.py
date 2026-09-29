"""Analiz planı (iş emri) şeması. Doğal dil katmanı YALNIZ bu şemaya uyan planlar üretebilir.

Her analiz tipi ayrı bir pydantic modeli; 'analiz' alanı ayırt edicidir.
Şema JSON olarak dışa verilir (MCP/Claude API araç tanımları buradan üretilir).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Seviye = Literal["mahalle", "ilce", "il"]
Yon = Literal["azalan", "artan"]

YER_ACIKLAMA = "Yer: resmî ad ('Bostanlı, Karşıyaka, İzmir'), geo_id ('GEO_025130') veya 'enlem, boylam'."
OLCU_ACIKLAMA = "Ölçü kimliği ya da eş anlamı (ör. 'konut_satis_m2', 'kira getirisi', 'nüfus'). ontoloji_ara ile bulunur."
DONEM_ACIKLAMA = "'son' (verisi olan en güncel dönem) ya da açık dönem: 'YYYY-MM', 'YYYY', 'YYYY-Qn'."
BOYUT_ACIKLAMA = ("Alt kırılım: işletme ölçülerinde sektör ('Yeme-İçme') veya kategori ('Kafe'); "
                  "harita_nesnesi_sayisi'nda nesne türü ('okul', 'eczane', 'otobüs durağı'); "
                  "kart ölçülerinde BKM sektörü ('YEMEK'); ruhsat alanında bina türü.")


class _Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")


class YerProfili(_Plan):
    """Bir yerin güncel tüm ölçüleri + üst birim (ilçe/il) değerleri + işletme sektör kırılımı."""
    analiz: Literal["yer_profili"]
    yer: str = Field(description=YER_ACIKLAMA)


class Karsilastir(_Plan):
    """Birden çok yeri seçilen ölçülerde yan yana koyar."""
    analiz: Literal["karsilastir"]
    yerler: list[str] = Field(min_length=2, max_length=20, description="Karşılaştırılacak yerler. " + YER_ACIKLAMA)
    olculer: list[str] = Field(min_length=1, max_length=12, description=OLCU_ACIKLAMA)
    donem: str = Field("son", description=DONEM_ACIKLAMA)
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)


class Sirala(_Plan):
    """Kapsam içindeki alt birimleri bir ölçüye göre sıralar (en yüksek/en düşük N)."""
    analiz: Literal["sirala"]
    olcu: str = Field(description=OLCU_ACIKLAMA)
    seviye: Seviye = Field(description="Sıralanacak birimlerin seviyesi")
    kapsam: str | None = Field(None, description="Sıralamanın yapılacağı üst yer (ör. 'İzmir'); boşsa Türkiye. " + YER_ACIKLAMA)
    yon: Yon = "azalan"
    limit: int = Field(10, ge=1, le=200)
    donem: str = Field("son", description=DONEM_ACIKLAMA)
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)
    min_nufus: int | None = Field(None, ge=0, description="Bu nüfusun altındaki birimleri dışla (küçük köy gürültüsünü önlemek için)")
    min_ilan: int | None = Field(None, ge=0, description="Fiyat ölçülerinde, aynı dönemde bu sayının altında ilanı olan birimleri dışla")


class ZamanSerisi(_Plan):
    """Bir veya birkaç yerin bir ölçüdeki dönemsel seyri ve değişim özetleri."""
    analiz: Literal["zaman_serisi"]
    yerler: list[str] = Field(min_length=1, max_length=10, description=YER_ACIKLAMA)
    olcu: str = Field(description=OLCU_ACIKLAMA)
    baslangic: str | None = Field(None, description="İlk dönem (dahil). Boşsa serinin başı.")
    bitis: str | None = Field(None, description="Son dönem (dahil). Boşsa serinin sonu.")
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)
    projeksiyon_dahil: bool = Field(False, description="Kaynağın 2026-08 sonrası TAHMİN aylarını da göster (etiketli)")


class Dagilim(_Plan):
    """Kapsam içindeki alt birimlerin bir ölçüdeki dağılım istatistikleri."""
    analiz: Literal["dagilim"]
    olcu: str = Field(description=OLCU_ACIKLAMA)
    seviye: Seviye
    kapsam: str | None = Field(None, description=YER_ACIKLAMA + " Boşsa Türkiye.")
    donem: str = Field("son", description=DONEM_ACIKLAMA)
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)


class YakinCevre(_Plan):
    """Bir nokta/yer çevresindeki işletmeler, harita nesneleri ve alan-ağırlıklı yaklaşık nüfus."""
    analiz: Literal["yakin_cevre"]
    merkez: str = Field(description="'enlem, boylam' veya yer adı (yer adıysa poligon merkezi kullanılır).")
    yaricap_m: int = Field(500, ge=50, le=5000)
    sektor: str | None = Field(None, description="Yalnız bu sektör (ör. 'Yeme-İçme')")
    kategori: str | None = Field(None, description="Yalnız bu kategori (ör. 'Kafe')")
    liste_limiti: int = Field(20, ge=0, le=200, description="En yakın kaç işletme listelensin")


class Yogunluk(_Plan):
    """Kapsamdaki alt birimlerde işletme sayısı, 1.000 kişi ve km² başına işletme."""
    analiz: Literal["yogunluk"]
    seviye: Seviye
    kapsam: str | None = Field(None, description=YER_ACIKLAMA + " Boşsa Türkiye.")
    sektor: str | None = None
    kategori: str | None = None
    yon: Yon = "azalan"
    limit: int = Field(20, ge=1, le=500)
    min_nufus: int | None = Field(1000, ge=0, description="Oranların küçük nüfusta şişmesini önlemek için alt sınır")


class KategoriDagilimi(_Plan):
    """Bir yerin işletmelerinin sektör veya kategori kırılımı; üst birimin paylarıyla birlikte."""
    analiz: Literal["kategori_dagilimi"]
    yer: str = Field(description=YER_ACIKLAMA)
    duzey: Literal["sektor", "kategori"] = "sektor"


class BenzerYerler(_Plan):
    """Seçilen ölçülerde en benzer N yer (log dönüşümü + z-skor + Öklid uzaklığı)."""
    analiz: Literal["benzer_yerler"]
    yer: str = Field(description=YER_ACIKLAMA)
    olculer: list[str] | None = Field(None, max_length=10, description="Boşsa seviyeye göre varsayılan ölçü seti")
    kapsam: str | None = Field(None, description="Adaylar bu yerin içinden seçilir; boşsa Türkiye")
    n: int = Field(10, ge=1, le=50)


class HaritaHareketliligi(_Plan):
    """OSM kesitleri arasında haritaya eklenen/çıkan nesneler (açılış/kapanış DEĞİLDİR)."""
    analiz: Literal["harita_hareketliligi"]
    yer: str = Field(description=YER_ACIKLAMA)
    kategori_grubu: Literal["all", "yeme_icme", "perakende", "hizmet", "saglik", "egitim", "konaklama", "sanayi", "diger"] = "all"


class IsletmeListesi(_Plan):
    """Koordinatlı işletme listesi (kapsam içinde veya merkez çevresinde)."""
    analiz: Literal["isletme_listesi"]
    kapsam: str | None = Field(None, description="Yer (mahalle/ilçe/il). merkez verilmezse zorunlu.")
    merkez: str | None = Field(None, description="'enlem, boylam' veya yer; yaricap_m ile birlikte")
    yaricap_m: int = Field(500, ge=50, le=5000)
    sektor: str | None = None
    kategori: str | None = None
    min_puan: float | None = Field(None, ge=0, le=5)
    min_yorum: int | None = Field(None, ge=0)
    siralama: Literal["puan", "yorum_sayisi", "mesafe", "ad"] = "puan"
    limit: int = Field(50, ge=1, le=500)


class Iliski(_Plan):
    """İki ölçü arasında alt birimler üzerinden Pearson ve Spearman korelasyonu."""
    analiz: Literal["iliski"]
    olcu_x: str = Field(description=OLCU_ACIKLAMA)
    olcu_y: str = Field(description=OLCU_ACIKLAMA)
    seviye: Seviye
    kapsam: str | None = Field(None, description=YER_ACIKLAMA + " Boşsa Türkiye.")
    donem_x: str = "son"
    donem_y: str = "son"
    boyut_x: str | None = None
    boyut_y: str | None = None


class VeriKapsami(_Plan):
    """Bir ölçünün hangi seviyede, hangi dönemlerde, kaç yerde bulunduğu."""
    analiz: Literal["veri_kapsami"]
    olcu: str = Field(description=OLCU_ACIKLAMA)


# ------------------------------------------------------------------------------------------ ileri modüller
class Kriter(_Plan):
    olcu: str = Field(description=OLCU_ACIKLAMA)
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)
    yon: Literal["arti", "eksi"] = Field(description="arti: yüksek değer daha uygun; eksi: yüksek değer daha az uygun")
    agirlik: float = Field(1.0, gt=0, le=10)
    neden: str | None = Field(None, description="Bu kriterin gerekçesi (künyeye yazılır)")


class DisVarsayim(_Plan):
    """Web veya kullanıcıdan gelen, KAYNAĞI BELİRTİLMİŞ dış bilgi. Motor yalnız tanıdığı anahtarları hesaba katar;
    diğerleri 'bağlam' olarak künyeye yazılır. Plan parmak izine dahildir (aynı varsayım → aynı sonuç)."""
    anahtar: Literal["yillik_enflasyon", "nufus_yillik_buyume", "baglam"] = Field(
        description="yillik_enflasyon: % (reel dönüşüm için; donem='YYYY'); nufus_yillik_buyume: % (resmî/dış senaryo); "
                    "baglam: hesaba girmeyen bilgi (planlanan metro hattı, büyük proje, yasal değişiklik...)")
    deger: float | None = Field(None, description="Sayısal değer (baglam için boş olabilir)")
    birim: str | None = None
    donem: str | None = Field(None, description="Değerin geçerli olduğu dönem, ör. '2027'")
    aciklama: str = Field(description="Bilginin kısa açıklaması")
    kaynak_adi: str = Field(description="Kurum/yayın adı, ör. 'TCMB Piyasa Katılımcıları Anketi Eylül 2026'")
    kaynak_url: str = Field(description="Kaynak adresi (zorunlu)")
    erisim_tarihi: str = Field(description="YYYY-MM-DD")
    alinti: str | None = Field(None, description="Kaynaktan kısa alıntı (sayının geçtiği cümle)")


class UygunBolge(_Plan):
    """İşletme, yazılım veya ürün için uygun bölgeleri çok kriterli, açık formüllü skorla sıralar.
    Hazır profil (profil_listesi) veya özel kriter listesi kullanılır; ağırlıklar değiştirilebilir."""
    analiz: Literal["uygun_bolge"]
    profil: str | None = Field(None, description="Hazır profil kimliği (ör. 'kafe', 'restoran_yazilimi', 'premium_urun')")
    kriterler: list[Kriter] | None = Field(None, max_length=15, description="Özel kriterler (profil yerine ya da profile ek)")
    agirliklar: dict[str, float] | None = Field(None, description="Profil kriteri ağırlığını değiştir: {'olcu' veya 'olcu[boyut]': yeni ağırlık}; 0 = çıkar")
    seviye: Seviye = Field(description="Aday birimlerin seviyesi")
    kapsam: str | None = Field(None, description="Adayların aranacağı yer; boşsa Türkiye. " + YER_ACIKLAMA)
    limit: int = Field(15, ge=1, le=200)
    min_nufus: int | None = Field(None, ge=0)
    min_kapsama: float = Field(0.7, ge=0.1, le=1.0, description="Adayın skoru için gereken en düşük veri kapsaması (ağırlık payı)")


class GelecekProjeksiyonu(_Plan):
    """Bir yerin bir ölçüdeki geleceğe dönük istatistiksel projeksiyonu: geçmişte sınanmış model topluluğu,
    %80 belirsizlik aralığı, kaynağın kendi tahmini (varsa) ve dış varsayımlarla reel/senaryo karşılaştırması."""
    analiz: Literal["gelecek_projeksiyonu"]
    yer: str = Field(description=YER_ACIKLAMA)
    olcu: str = Field(description=OLCU_ACIKLAMA + " Zaman serisi olan ölçüler: fiyatlar (aylık), nüfus (yıllık), konut satışı, ruhsat, banka.")
    ufuk: int | None = Field(None, ge=1, le=36, description="Kaç dönem ileri (aylık serilerde ay, yıllıkta yıl). Boşsa aylık 12, yıllık 5.")
    boyut: str | None = Field(None, description=BOYUT_ACIKLAMA)
    dis_varsayimlar: list[DisVarsayim] = Field(default_factory=list, max_length=20)


class BolgeGelecekRaporu(_Plan):
    """Bir bölgenin geleceğine dair çok ölçülü rapor: nüfus, konut fiyatı, kira, satış hacmi projeksiyonları +
    öncü göstergeler (ruhsat hattı, göç, OSM işletme akışı) + dış kaynak bağlamı."""
    analiz: Literal["bolge_gelecek_raporu"]
    yer: str = Field(description=YER_ACIKLAMA)
    ufuk_ay: int = Field(12, ge=3, le=36, description="Aylık serilerde ufuk (ay); yıllık serilerde ceil(ufuk_ay/12)+2 yıl")
    dis_varsayimlar: list[DisVarsayim] = Field(default_factory=list, max_length=20)


MODELLER = (YerProfili, Karsilastir, Sirala, ZamanSerisi, Dagilim, YakinCevre, Yogunluk, KategoriDagilimi,
            BenzerYerler, HaritaHareketliligi, IsletmeListesi, Iliski, VeriKapsami,
            UygunBolge, GelecekProjeksiyonu, BolgeGelecekRaporu)
Plan = Annotated[Union[MODELLER], Field(discriminator="analiz")]
PLAN_ADAPTOR = TypeAdapter(Plan)
PLAN_MODELLERI = {m.model_fields["analiz"].annotation.__args__[0]: m for m in MODELLER}


def dogrula(veri: dict):
    """dict → tipli plan (hatalıysa pydantic ValidationError)."""
    return PLAN_ADAPTOR.validate_python(veri)


def json_sema() -> dict:
    return PLAN_ADAPTOR.json_schema()
