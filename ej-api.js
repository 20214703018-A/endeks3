// Emlakjet bölge endeksi — saf dönüştürme katmanı.
// Hem service worker (importScripts) hem Node testleri kullanır;
// içinde chrome.* veya DOM bağımlılığı YOKTUR.
(() => {
  "use strict";

  const API_KOK = "https://www.emlakjet.com/api/endeksa/dynamictrend";
  const REFERANS_KOK = "https://www.emlakjet.com/emlak-piyasasi";

  // Emlakjet ürün kodları (Emlak Piyasası sayfalarının kendi isteklerinden)
  const TIPLER = {
    konut: { pc: 1, pt: 4, yol: "satilik-konut" },
    arsa: { pc: 3, pt: 1, yol: "satilik-arsa" },
  };
  const SEVIYE_LEVEL = { ulke: 9, il: 1, ilce: 2, mahalle: 3 };
  const ALT_SEVIYE = { ulke: "il", il: "ilce", ilce: "mahalle", mahalle: null };
  const SEVIYE_SIRA = ["ulke", "il", "ilce", "mahalle"];

  const yuzde = (v) => (typeof v === "number" ? Math.round(v * 10000) / 100 : null);
  const sayi = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

  const slugla = (s) => String(s || "").toLocaleLowerCase("tr")
    .replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ş/g, "s")
    .replace(/ı/g, "i").replace(/ö/g, "o").replace(/ç/g, "c")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  function ayKodu(r) {
    if (!r?.PropertyYear || !r?.PropertyMonth) return null;
    return `${r.PropertyYear}-${String(r.PropertyMonth).padStart(2, "0")}`;
  }

  // Ay kodunu (YYYY-AA) geriye doğru kaydırır
  function gecmisAy(kod, geriAy = 1) {
    const m = /^(\d{4})-(\d{2})$/.exec(kod || "");
    if (!m) return null;
    const toplam = Number(m[1]) * 12 + (Number(m[2]) - 1) - geriAy;
    if (toplam < 0) return null;
    return `${Math.floor(toplam / 12)}-${String((toplam % 12) + 1).padStart(2, "0")}`;
  }
  const oncekiAy = (kod) => gecmisAy(kod, 1);

  // Sayfadaki "1 Yıl / 2 Yıl / 5 Yıl değişim" kutuları yanıtta hazır gelmez;
  // Emlakjet bunları seriden hesaplar: güncel ay ÷ n yıl önceki AYNI ay.
  // (Türkiye konut 2026-08 için 42.488 / 3.773 = %1026,1 — sayfayla birebir.)
  // Seri yoksa (alt bölge özetleri) değerler null kalır.
  function donemDegisimleri(seri, donem) {
    const out = { degisim1Yil: null, degisim2Yil: null, degisim5Yil: null };
    const harita = new Map((Array.isArray(seri) ? seri : []).map((x) => [x.ay, x]));
    const simdi = harita.get(donem);
    if (!simdi) return out;
    // m² fiyatı esastır; yoksa endeks aynı oranı verir
    const alan = simdi.m2Fiyat != null ? "m2Fiyat" : "endeks";
    const guncel = simdi[alan];
    if (!guncel) return out;
    for (const [ad, yil] of [["degisim1Yil", 1], ["degisim2Yil", 2], ["degisim5Yil", 5]]) {
      const eski = harita.get(gecmisAy(donem, yil * 12))?.[alan];
      if (eski) out[ad] = Math.round((guncel / eski - 1) * 10000) / 100;
    }
    return out;
  }

  // Yanıtın "güncel dönem"i: General varsa onun ayı, yoksa TrendDate'in bir
  // öncesi. (Emlakjet güncel ayı bir gecikmeyle yayımlar; arsa mahalle
  // düzeyinde General hiç gelmez, o zaman seriden okunur.)
  function guncelDonem(j) {
    const genel = Array.isArray(j?.General) ? j.General[0] : null;
    if (genel) return ayKodu(genel);
    const t = String(j?.TrendDate || "").slice(0, 7);
    return /^\d{4}-\d{2}$/.test(t) ? oncekiAy(t) : null;
  }

  // Güncel ölçüm + hangi alandan geldiği (akademik kullanımda kaynak izi)
  function guncelOlcum(j) {
    const genel = Array.isArray(j?.General) ? j.General[0] : null;
    if (genel) return { donem: ayKodu(genel), olcumKaynagi: "General", ...olcumler(genel) };
    const donem = guncelDonem(j);
    const satir = (Array.isArray(j?.Trend) ? j.Trend : []).find((r) => ayKodu(r) === donem);
    if (!satir) return { donem, olcumKaynagi: null };
    return { donem, olcumKaynagi: "Trend", ...olcumler(satir) };
  }

  // Aylık seri; güncel dönemden SONRAKİ aylar Emlakjet'in projeksiyonudur,
  // akademik kullanımda ayrıştırılabilsin diye işaretlenir.
  function trendSatirlari(j) {
    const donem = guncelDonem(j);
    return (Array.isArray(j?.Trend) ? j.Trend : [])
      .map((r) => {
        const ay = ayKodu(r);
        return ay ? { ay, projeksiyon: donem && ay > donem ? 1 : 0, ...olcumler(r) } : null;
      })
      .filter(Boolean);
  }

  function apiUrl(g) {
    const kod = TIPLER[g.tip];
    if (!kod) throw new Error(`Bilinmeyen tip: ${g.tip}`);
    if (!SEVIYE_LEVEL[g.seviye]) throw new Error(`Bilinmeyen seviye: ${g.seviye}`);
    const p = new URLSearchParams({
      BuildYear: "5",
      CountryId: "1",
      Details: "true",
      FloorNumber: "5",
      HeatType: "3",
      Level: String(SEVIYE_LEVEL[g.seviye]),
      PropertyCategory: String(kod.pc),
      PropertyType: String(kod.pt),
      Rooms: "5",
      Static: "true",
      Trend: "true",
      Types: "true",
      Wkt: "",
    });
    if (g.cityId) p.set("CityId", String(g.cityId));
    if (g.countyId) p.set("CountyId", String(g.countyId));
    if (g.districtId) p.set("DistrictId", String(g.districtId));
    return `${API_KOK}?${p.toString()}`;
  }

  // Kaynak gösterimi: verinin göründüğü insan-okunur Emlak Piyasası sayfası
  function kaynakUrl(g) {
    const yol = TIPLER[g.tip]?.yol || "satilik-konut";
    const parcalar = [g.ilSlug, g.ilceSlug, g.mahalleSlug].filter(Boolean);
    if (!parcalar.length) return `${REFERANS_KOK}/${yol}`;
    const son = g.mahalleSlug ? `${parcalar.join("-")}-mahallesi` : parcalar.join("-");
    return `${REFERANS_KOK}/${yol}/${son}`;
  }

  // Emlakjet satırı satılık + kiralık ölçümleri birlikte taşır
  function olcumler(r) {
    if (!r) return {};
    return {
      m2Fiyat: sayi(r.UnitPriceForSale),
      m2FiyatMin: sayi(r.MinUnitPriceForSale),
      m2FiyatMax: sayi(r.MaxUnitPriceForSale),
      ortFiyat: sayi(r.PriceForSale),
      ortM2: sayi(r.ComparableAreaForSale),
      ilanSayisi: sayi(r.CountForSale),
      aylikDegisim: yuzde(r.PriceChangeSale),
      yillikDegisim: yuzde(r.UnitPriceSaleAnnualChange),
      amortisman: sayi(r.Amortization),
      getiri: sayi(r.Yield),
      ortBinaYasi: sayi(r.AverageAgeForSale),
      ilanSuresi: sayi(r.ListingPeriodForSale),
      endeks: sayi(r.IndexSale),
      kiraM2Fiyat: sayi(r.UnitPriceForRent),
      kiraFiyat: sayi(r.PriceForRent),
      kiraOrtM2: sayi(r.ComparableAreaForRent),
      kiraIlanSayisi: sayi(r.CountForRent),
      kiraYillikDegisim: yuzde(r.UnitPriceRentAnnualChange),
      kiraIlanSuresi: sayi(r.ListingPeriodForRent),
      kiraEndeks: sayi(r.IndexRent),
    };
  }

  // Yaş / oda / kat / ısıtma / alan kırılımları — bölge düzeyinde
  // "bina yaşı, oda sayısı, m² bandı" verisi buradan gelir.
  function dagilimSatirlari(j) {
    const kaynaklar = {
      yas: j?.Age, oda: j?.HouseType, kat: j?.FloorSergment,
      isitma: j?.Heating, alan: j?.AreaSegment,
    };
    const out = [];
    for (const [dagilim, liste] of Object.entries(kaynaklar)) {
      for (const r of Array.isArray(liste) ? liste : []) {
        if (!r?.ListingType) continue;
        out.push({
          dagilim, segment: String(r.ListingType),
          oran: yuzde(r.CountForSaleRatio), ay: ayKodu(r), ...olcumler(r),
        });
      }
    }
    return out;
  }

  // Static = bir alt seviyedeki bölgelerin listesi (kimlik + ad + güncel özet).
  // Emlakjet sayfadaki tabloyu da bu veriden çizer.
  function cocuklar(j, g) {
    const alt = ALT_SEVIYE[g.seviye];
    if (!alt) return [];
    const out = [];
    for (const r of Array.isArray(j?.Static) ? j.Static : []) {
      const kimlikVar = alt === "il" ? r.CityId : alt === "ilce" ? r.CountyId : r.DistrictId;
      if (!kimlikVar) continue;
      const ad = alt === "il" ? r.CityName : alt === "ilce" ? r.CountyName : r.DistrictName;
      if (!ad) continue;
      out.push({
        tip: g.tip,
        seviye: alt,
        cityId: alt === "il" ? r.CityId : g.cityId || null,
        countyId: alt === "ilce" ? r.CountyId : g.countyId || null,
        districtId: alt === "mahalle" ? r.DistrictId : null,
        il: alt === "il" ? ad : g.il || null,
        ilce: alt === "ilce" ? ad : g.ilce || null,
        mahalle: alt === "mahalle" ? ad : null,
        ozet: olcumler(r),
        ay: ayKodu(r),
      });
    }
    return out;
  }

  const api = {
    API_KOK, REFERANS_KOK, TIPLER, SEVIYE_LEVEL, ALT_SEVIYE, SEVIYE_SIRA,
    yuzde, sayi, ayKodu, oncekiAy, gecmisAy, slugla, apiUrl, kaynakUrl, olcumler,
    guncelDonem, guncelOlcum, trendSatirlari, donemDegisimleri, dagilimSatirlari, cocuklar,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.EmlakjetApi = api;
})();
