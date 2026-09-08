// Piyasa Toplayici - DOM'dan bagimsiz, test edilebilir ayrıştırma yardimcilari.
(() => {
  "use strict";

  const IL_SLUGLARI = [
    "adana", "adiyaman", "afyonkarahisar", "agri", "amasya", "ankara", "antalya", "artvin",
    "aydin", "balikesir", "bilecik", "bingol", "bitlis", "bolu", "burdur", "bursa", "canakkale",
    "cankiri", "corum", "denizli", "diyarbakir", "edirne", "elazig", "erzincan", "erzurum",
    "eskisehir", "gaziantep", "giresun", "gumushane", "hakkari", "hatay", "isparta", "mersin",
    "istanbul", "izmir", "kars", "kastamonu", "kayseri", "kirklareli", "kirsehir", "kocaeli",
    "konya", "kutahya", "malatya", "manisa", "kahramanmaras", "mardin", "mugla", "mus", "nevsehir",
    "nigde", "ordu", "rize", "sakarya", "samsun", "siirt", "sinop", "sivas", "tekirdag", "tokat",
    "trabzon", "tunceli", "sanliurfa", "usak", "van", "yozgat", "zonguldak", "aksaray", "bayburt",
    "karaman", "kirikkale", "batman", "sirnak", "bartin", "ardahan", "igdir", "yalova", "karabuk",
    "kilis", "osmaniye", "duzce"
  ];
  const IL_KUMESI = new Set(IL_SLUGLARI);

  const slug = (s) => String(s || "").toLocaleLowerCase("tr")
    .replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ş/g, "s")
    .replace(/ı/g, "i").replace(/ö/g, "o").replace(/ç/g, "c")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  // "57.737 ₺/m²" -> 57737 | "%44,8" -> 44.8 | "₺6.016.788" -> 6016788
  function sayi(metin) {
    if (!metin) return null;
    let t = String(metin).replace(/[^\d.,-]/g, "");
    if (!t || !/\d/.test(t)) return null;
    if (t.includes(",") && t.includes(".")) t = t.replace(/\./g, "").replace(",", ".");
    else if (t.includes(",")) t = t.replace(",", ".");
    else if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
    const v = Number.parseFloat(t);
    return Number.isFinite(v) ? v : null;
  }

  function tipTahmin(urlVeyaYol) {
    const p = String(urlVeyaYol || "").toLocaleLowerCase("tr");
    if (/arsa|arazi|tarla|bağ|bag-bahce|bahçe|zeytinlik|çiftlik|ciftlik|imarli/.test(p)) return "arsa";
    if (/is-yeri|iş-yeri|isyeri|ticari|ofis|büro|buro|dükkan|dukkan|mağaza|magaza|depo|antrepo|fabrika|atölye|atolye|sanayi|turistik/.test(p)) return "ticari";
    return "konut";
  }

  function islemTahmin(urlVeyaYol) {
    const p = slug(urlVeyaYol);
    if (p.includes("kiralik")) return "kiralik";
    if (p.includes("satilik")) return "satilik";
    if (p.includes("devren")) return "devren";
    if (p.includes("kat-karsiligi")) return "kat-karsiligi";
    return null;
  }

  function siteTahmin(href) {
    try {
      const h = new URL(href).hostname.toLocaleLowerCase("tr");
      if (h.includes("emlakjet")) return "emlakjet";
      if (h.includes("sahibinden")) return "sahibinden";
      if (h.includes("hepsiemlak")) return "hepsiemlak";
      if (h.includes("endeksa")) return "endeksa";
      if (h.includes("zingat")) return "zingat";
    } catch { /* gecersiz URL */ }
    return "genel";
  }

  function normalizeUrl(href, base) {
    try {
      const u = new URL(href, base);
      u.hash = "";
      for (const k of [...u.searchParams.keys()]) {
        if (/^(utm_|ref$|source$|tracking)/i.test(k)) u.searchParams.delete(k);
      }
      u.searchParams.sort();
      return u.href.replace(/\/$/, "");
    } catch { return String(href || ""); }
  }

  function ilVeKalan(segment) {
    const s = slug(segment);
    const il = IL_SLUGLARI.filter((x) => s === x || s.startsWith(x + "-"))
      .sort((a, b) => b.length - a.length)[0] || null;
    return { il, kalan: il ? s.slice(il.length).replace(/^-/, "") : "" };
  }

  function geoTahmin(href) {
    let u;
    try { u = new URL(href); } catch { return null; }
    const site = siteTahmin(u.href);
    const p = decodeURIComponent(u.pathname);
    const tip = tipTahmin(p);
    const islem = islemTahmin(p);

    if (site === "emlakjet") {
      let m = p.match(/^\/emlak-piyasasi\/(satilik|kiralik)-([^/]+)(?:\/([^/]+))?\/?$/i);
      if (m) {
        const yer = m[3] ? ilVeKalan(m[3]) : { il: null, kalan: "" };
        // "istanbul-besiktas-akat-mahallesi" → il/ilçe/mahalle.
        // İlk parça ilçe, "-mahallesi" öncesi kalan mahalledir; çok
        // parçalı ilçe adlarında bu ayrım tahminidir (kesin kimlik için
        // arka plan servisi Emlakjet bölge kimliklerini kullanır).
        let ilce = yer.kalan || null, mahalle = null, seviye = m[3] ? (yer.kalan ? "ilce" : "il") : "ulke";
        if (yer.kalan && /-mahallesi$/.test(yer.kalan)) {
          const tok = yer.kalan.replace(/-mahallesi$/, "").split("-").filter(Boolean);
          if (tok.length >= 2) { ilce = tok[0]; mahalle = tok.slice(1).join("-"); seviye = "mahalle"; }
          else if (tok.length === 1) { ilce = null; mahalle = tok[0]; seviye = "mahalle"; }
        }
        return { site, tip: tipTahmin(m[2]), islem: m[1], il: yer.il, ilce,
          mahalle, seviye, url: normalizeUrl(u.href) };
      }
      m = p.match(/^\/(satilik|kiralik)-([^/]+)(?:\/([^/]+))?/i);
      if (m) {
        const yer = m[3] ? ilVeKalan(m[3]) : { il: null, kalan: "" };
        return { site, tip: tipTahmin(m[2]), islem: m[1], il: yer.il, ilce: yer.kalan || null,
          mahalle: null, seviye: yer.il ? (yer.kalan ? "ilce" : "il") : "ulke", url: normalizeUrl(u.href) };
      }
    }

    if (site === "sahibinden") {
      const m = p.match(/^\/(satilik|kiralik)-([^/]+)(?:\/([^/]+))?/i);
      if (m) {
        const yer = m[3] ? ilVeKalan(m[3]) : { il: null, kalan: "" };
        return { site, tip: tipTahmin(m[2]), islem: m[1], il: yer.il, ilce: yer.kalan || null,
          mahalle: null, seviye: yer.il ? (yer.kalan ? "ilce" : "il") : "ulke", url: normalizeUrl(u.href) };
      }
    }

    if (site === "hepsiemlak") {
      const ilk = slug(p.split("/").filter(Boolean)[0]);
      const il = IL_KUMESI.has(ilk) ? ilk : null;
      return { site, tip, islem, il, ilce: null, mahalle: null, seviye: il ? "il" : "bilinmiyor", url: normalizeUrl(u.href) };
    }
    return { site, tip, islem, il: null, ilce: null, mahalle: null, seviye: "bilinmiyor", url: normalizeUrl(u.href) };
  }

  function m2Cikar(txt) {
    const t = String(txt || "").replace(/\s+/g, " ");
    const deger = (s) => { const v = sayi(s); return v !== null && v >= 10 && v <= 2_000_000 ? Math.round(v) : null; };
    const net = t.match(/net\s*(?:m²|m2|metrekare)?\s*[:·-]?\s*(\d[\d.]*)\s*(?:m²|m2|metrekare)?/i);
    if (net) { const v = deger(net[1]); if (v) return { m2: v, tur: "net" }; }
    const brut = t.match(/br[üu]t\s*(?:m²|m2|metrekare)?\s*[:·-]?\s*(\d[\d.]*)\s*(?:m²|m2|metrekare)?/i);
    if (brut) { const v = deger(brut[1]); if (v) return { m2: v, tur: "brut" }; }
    const ilk = t.match(/(\d[\d.]*)\s*(?:m²|m2|metrekare)/i);
    if (ilk) { const v = deger(ilk[1]); if (v) return { m2: v, tur: "alan" }; }
    return { m2: null, tur: null };
  }

  const AYLAR = { ocak: "01", subat: "02", mart: "03", nisan: "04", mayis: "05", haziran: "06", temmuz: "07", agustos: "08", eylul: "09", ekim: "10", kasim: "11", aralik: "12" };
  function donemCikar(metin, varsayilanTarih = new Date()) {
    const m = String(metin || "").match(/(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+(20\d{2})/i);
    if (m) return `${m[2]}-${AYLAR[slug(m[1]).replace(/-/g, "")]}`;
    return new Date(varsayilanTarih).toISOString().slice(0, 7);
  }

  function konumParcala(metin, geo = {}) {
    const kaynak = String(metin || "").replace(/\bMah(?:allesi)?\.?\b/gi, "").trim();
    const ayir = kaynak.includes("/") ? /\s*\/\s*/ : kaynak.includes(",") ? /\s*,\s*/ : /\s*\n+\s*/;
    const ham = kaynak.replace(/[\t ]+/g, " ");
    if (!ham) return {};
    const p = ham.split(ayir).map((x) => x.trim()).filter(Boolean);
    if (!p.length) return {};
    const sl = p.map(slug);
    let ilIndex = sl.findIndex((x) => IL_KUMESI.has(x));
    if (ilIndex >= 0) {
      const il = sl[ilIndex];
      if (p.length >= 3 && ilIndex === 0) return { il, ilce: slug(p[1]), mahalle: slug(p[2]) };
      if (p.length >= 2 && ilIndex === 0) return { il, ilce: slug(p[1]), mahalle: null };
      if (p.length >= 2 && ilIndex === 1) return { il, ilce: slug(p[0]), mahalle: p[2] ? slug(p[2]) : null };
      return { il, ilce: null, mahalle: null };
    }
    if (geo.il) return { il: slug(geo.il), ilce: slug(p[0]) || geo.ilce || null, mahalle: p[1] ? slug(p[1]) : null };
    return {};
  }

  function sayfaNoCikar(href) {
    try {
      const u = new URL(href);
      if (u.searchParams.has("sayfa")) return Math.max(1, Number(u.searchParams.get("sayfa")) || 1);
      if (u.searchParams.has("page")) return Math.max(1, Number(u.searchParams.get("page")) || 1);
      if (u.searchParams.has("pagingOffset")) return Math.floor((Number(u.searchParams.get("pagingOffset")) || 0) / 20) + 1;
    } catch { /* yoksay */ }
    return 1;
  }

  const api = { IL_SLUGLARI, slug, sayi, tipTahmin, islemTahmin, siteTahmin, normalizeUrl, geoTahmin, m2Cikar, donemCikar, konumParcala, sayfaNoCikar };
  globalThis.PiyasaParsers = api;
})();
