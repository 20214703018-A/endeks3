// Piyasa Toplayıcı — content script
// Yalnızca desteklenen emlak sonuç/piyasa sayfalarında çalışır.
// Otomatik kayıt ve gezinme, popup'taki yazılı izin onayına bağlıdır.

(() => {
  "use strict";

  const Core = globalThis.PiyasaParsers || {};

  function destekliVeriSayfasi(href = location.href) {
    let u;
    try { u = new URL(href); } catch { return false; }
    const host = u.hostname.toLocaleLowerCase("tr");
    const p = decodeURIComponent(u.pathname).toLocaleLowerCase("tr");
    const emlakKategori = /(arsa|arazi|tarla|bag|bahce|konut|daire|villa|mustakil|rezidans|is-yeri|isyeri|ticari|ofis|buro|dukkan|magaza|depo|fabrika|atolye|turistik)/;
    if (host.includes("emlakjet")) return p.startsWith("/emlak-piyasasi/") || new RegExp(`^/(satilik|kiralik|devren)-[^/]*${emlakKategori.source}`).test(p);
    if (host.includes("sahibinden")) return new RegExp(`^/(satilik|kiralik)-[^/]*${emlakKategori.source}`).test(p);
    if (host.includes("hepsiemlak")) return /-(satilik|kiralik)(?:\/|$)/.test(p) && emlakKategori.test(p);
    if (host.includes("endeksa")) return p.includes("/analiz/");
    if (host.includes("zingat")) return /(satilik|kiralik).*(arsa|konut|daire|isyeri|ticari)/.test(p);
    return false;
  }

  // Çift yükleme koruması (popup yedeğiyle sonradan enjekte edilirse)
  if (window.__piyasaToplayiciYuklu) return;
  window.__piyasaToplayiciYuklu = true;

  const $slug = (s) => (s || "").toLocaleLowerCase("tr")
    .replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ş/g, "s")
    .replace(/ı/g, "i").replace(/ö/g, "o").replace(/ç/g, "c")
    .replace(/[^a-z0-9]+/g, "");

  // "57.737 ₺/m²" -> 57737 | "%44,8" -> 44.8 | "₺6.016.788" -> 6016788
  function sayi(metin) {
    if (!metin) return null;
    let t = String(metin).replace(/[^\d.,-]/g, "");
    if (!t || !/\d/.test(t)) return null;
    if (t.includes(",") && t.includes(".")) {
      t = t.replace(/\./g, "").replace(",", ".");
    } else if (t.includes(",")) {
      t = t.replace(",", ".");
    } else if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) {
      t = t.replace(/\./g, "");
    }
    const v = parseFloat(t);
    return Number.isFinite(v) ? v : null;
  }

  function hucreMetin(el) {
    return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  }

  // URL'den coğrafya + tip tahmini (href parametresi testler için)
  function geoTahmin(href) {
    const yeni = Core.geoTahmin?.(href || location.href);
    if (yeni && ["emlakjet", "sahibinden", "hepsiemlak"].includes(yeni.site)) return yeni;
    const u = new URL(href || location.href);
    const host = u.hostname.toLocaleLowerCase("tr");
    const p = u.pathname;
    const endeksa = host.includes("endeksa");
    const emlakjet = host.includes("emlakjet");
    let m = endeksa && p.match(/^\/tr\/analiz\/turkiye\/([^/]+)\/([^/]+)\/endeks\/(satilik|kiralik)\/([^/]+)/);
    if (m) {
      return { site: "endeksa", il: m[1], ilce: m[2], tip: m[4].includes("arsa") ? "arsa" : "konut", duzey: "mahalle-listesi", url: u.href.split("?")[0] };
    }
    m = endeksa && p.match(/^\/tr\/analiz\/turkiye\/([^/]+)\/endeks\/(satilik|kiralik)\/([^/]+)/);
    if (m) {
      return { site: "endeksa", il: m[1], ilce: null, tip: m[3].includes("arsa") ? "arsa" : "konut", duzey: "ilce-listesi", url: u.href.split("?")[0] };
    }
    m = emlakjet && p.match(/^\/emlak-piyasasi\/(satilik|kiralik)-([a-z-]+?)(?:\/(.+))?$/);
    if (m) {
      const tipHam = m[2]; // konut | arsa | ...
      const tip = tipHam.includes("arsa") ? "arsa" : "konut";
      const rest = (m[3] || "").split("/")[0].split("?")[0];
      const tok = rest.split("-").filter(Boolean);
      if (!tok.length) return null;
      const il = tok[0];
      let ilce = null, mahalle = null, duzey = "ilce-listesi";
      if (tok.length >= 2) {
        if (tok[tok.length - 1] === "mahallesi" && tok.length >= 3) {
          ilce = tok[1];
          mahalle = tok.slice(2, -1).join("-");
          duzey = "mahalle";
        } else {
          ilce = tok.slice(1).join("-");
          duzey = "mahalle-listesi";
        }
      }
      return { site: "emlakjet", il, ilce, mahalle, tip, duzey, url: u.href.split("?")[0] };
    }
    return null;
  }

  // --- Genel ilan kartı dedektörü (site bağımsız, URL desensiz) ---
  // Sayfadaki ₺ fiyat düğümlerini bul → kart köküne tırman → başlık/fiyat/m²/link çıkar.
  // Yalnızca kullanıcının açtığı sayfada, istek üzerine çalışır.
  // TL eşleşmesi harf-sınırlıdır: "TL85" tutar, "ATLAS" tutmaz (innerText boşlukları yutabilir).
  const FIYAT_ISARET = /₺|(?<![A-Za-zÇĞİÖŞÜçğıöşü])TL(?![A-Za-zÇĞİÖŞÜçğıöşü])/;
  const FIYAT_ISARET_G = /₺|(?<![A-Za-zÇĞİÖŞÜçğıöşü])TL(?![A-Za-zÇĞİÖŞÜçğıöşü])/g;
  // Site-özel kapsayıcı ipuçları (bulunamazsa tüm sayfa taranır)
  function kapsayiciBul() {
    const host = location.hostname.toLocaleLowerCase("tr");
    const ipuclari = [];
    if (host.includes("sahibinden")) {
      ipuclari.push("table.searchResultsTable tbody", ".searchResultsContainer", "#searchResultsTable", "table.searchResultsTable");
    }
    if (host.includes("emlakjet")) {
      ipuclari.push("main", "[class*=listing i]", "[class*=result i]", "[class*=search i][class*=list i]");
    }
    if (host.includes("hepsiemlak") || host.includes("zingat")) {
      ipuclari.push("main", "[class*=list i]", "[class*=result i]");
    }
    for (const s of ipuclari) {
      try { const el = document.querySelector(s); if (el) return el; } catch { /* yoksay */ }
    }
    return document.body;
  }

  function fiyatDugumleri(kok) {
    const out = [];
    const walker = document.createTreeWalker(kok || kapsayiciBul(), NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const t = (n.nodeValue || "").replace(/\s+/g, " ");
      if (!FIYAT_ISARET.test(t)) continue;
      if (n.parentElement?.closest("s, del, strike")) continue; // üstü çizili eski fiyat
      if (n.parentElement && n.parentElement.offsetParent === null &&
        getComputedStyle(n.parentElement).position !== "fixed") continue; // gizli
      const v = sayi(t);
      if (v === null || v < 30000 || v > 2e9) continue; // ilan bandı dışı (kredi/aylık kira altı vb.)
      out.push({ el: n.parentElement, deger: v });
    }
    return out;
  }

  // Alt düğüm sınırlarına boşluk koyarak metin birleştirir:
  // innerText "TL</span><span>Brüt" ü "TLBrüt" diye yapıştırır, bu sayaç ayırır.
  function parcaliMetin(el) {
    const parcalar = [];
    for (const c of el.childNodes) {
      const t = (c.innerText ?? c.textContent ?? "").replace(/\s+/g, " ").trim();
      if (t) parcalar.push(t);
    }
    return parcalar.join(" ");
  }
  function kartBul(fiyatEl) {
    let el = fiyatEl, d = 0;
    while (el && el !== document.body && d < 7) {
      el = el.parentElement; d++;
      if (!el || el === document.body) break;
      const txt = parcaliMetin(el) || el.innerText || "";
      const fiyatSayisi = (txt.match(FIYAT_ISARET_G) || []).length;
      if (fiyatSayisi >= 1 && fiyatSayisi <= 2 && el.querySelector("a[href]")) return el;
    }
    return null;
  }

  function kartOku(kart, fiyatDeger) {
    const txt = (kart.innerText || "").replace(/\s+/g, " ");
    const linkEl = kart.querySelector("a[href]");
    let baslik = "";
    const h = kart.querySelector("h1,h2,h3,h4");
    if (h && hucreMetin(h).length > 3) baslik = hucreMetin(h);
    if (!baslik && linkEl) baslik = hucreMetin(linkEl);
    if (!baslik) baslik = txt.slice(0, 80);
    let m2 = null;
    const mm = txt.match(/(\d[\d.]*)\s*m²/);
    if (mm) {
      const v = sayi(mm[1]);
      if (v !== null && v >= 10 && v <= 100000) m2 = Math.round(v);
    }
    let link = null;
    try { if (linkEl) link = new URL(linkEl.getAttribute("href"), location.href).href.split("?")[0]; } catch { /* yoksay */ }
    const mm2 = m2Cikar(txt);
    return { baslik: baslik.slice(0, 120), fiyat: Math.round(fiyatDeger), m2: mm2.m2 ?? m2, link, konum: konumCikar(kart), metin: txt.slice(0, 500) };
  }

  // Sahibinden sonuç tablosu hızlı yolu (bulunamazsa genel dedektör devralır)
  function sahibindenSatirlari() {
    const out = [];
    const satirlar = document.querySelectorAll("tr.searchResultsItem, table.searchResultsTable tbody tr");
    for (const tr of satirlar) {
      const linkEl = tr.querySelector("a.classifiedTitle, td.searchResultsTitleValue a, a[href*='/ilan'], a[href*='/emlak']");
      const fiyatHucre = tr.querySelector(".searchResultsPriceValue, td[class*=Price], td[class*=price]");
      const fiyatMetin = hucreMetin(fiyatHucre || tr);
      const fiyat = sayi((fiyatMetin.match(/[₺\d][\d.,\s]*₺|[\d][\d.,]*\s*TL/i) || [fiyatMetin])[0]);
      if (fiyat === null || fiyat < 30000 || fiyat > 2e9) continue;
      const baslik = linkEl ? hucreMetin(linkEl) : hucreMetin(tr).slice(0, 120);
      const satirMetin = hucreMetin(tr);
      const alanHucre = tr.querySelector("td.searchResultsAttributeValue");
      let m2 = alanHucre ? sayi(hucreMetin(alanHucre)) : null;
      if (m2 === null) {
        const mm = satirMetin.match(/(\d[\d.]*)\s*m²/);
        if (mm) m2 = sayi(mm[1]);
      }
      if (m2 !== null && m2 >= 10 && m2 <= 2_000_000) m2 = Math.round(m2); else m2 = null;
      let link = null;
      try { if (linkEl) link = new URL(linkEl.getAttribute("href"), location.href).href.split("?")[0]; } catch { /* yoksay */ }
      const konumEl = tr.querySelector("td.searchResultsLocationValue, td[class*=Location i], td[class*=location i], td[class*=Adres i], td[class*=adres i]");
      const konum = konumEl ? hucreMetin(konumEl) : null;
      const birimHucreler = [...tr.querySelectorAll("td.searchResultsPriceValue")];
      const m2BirimFiyat = birimHucreler.length > 1 ? sayi(hucreMetin(birimHucreler[1])) : (m2 ? Math.round(fiyat / m2) : null);
      const tarihEl = tr.querySelector("td.searchResultsDateValue");
      if (baslik) out.push({ baslik: baslik.slice(0, 150), fiyat: Math.round(fiyat), m2,
        m2BirimFiyat: m2BirimFiyat ? Math.round(m2BirimFiyat) : null,
        ilanTarihi: tarihEl ? hucreMetin(tarihEl) : null, link, konum, metin: satirMetin.slice(0, 700) });
      if (out.length >= 100) break;
    }
    return out;
  }

  function metindenToplamFiyat(txt) {
    const es = [...String(txt || "").matchAll(/(?:₺\s*)?\d[\d.]{3,}(?:\s*₺|\s*TL)/gi)]
      .map((m) => sayi(m[0])).filter((v) => v !== null && v >= 30_000 && v <= 20_000_000_000);
    return es.length ? Math.round(es[0]) : null;
  }

  function emlakjetIlanlari() {
    const out = [];
    const gorulen = new Set();
    for (const kart of document.querySelectorAll("article")) {
      const linkEl = kart.querySelector("a.listing-card-title-link[href*='/ilan/'], h3 a[href*='/ilan/']");
      if (!linkEl) continue;
      const txt = hucreMetin(kart);
      const fiyat = metindenToplamFiyat(txt);
      if (!fiyat) continue;
      const link = Core.normalizeUrl?.(linkEl.href, location.href) || linkEl.href.split("?")[0];
      if (gorulen.has(link)) continue;
      gorulen.add(link);
      const baslik = hucreMetin(linkEl).slice(0, 150);
      const alan = (Core.m2Cikar?.(txt) || m2Cikar(txt));
      const konumEl = [...kart.querySelectorAll("p")].find((p) => /,/.test(hucreMetin(p)) && hucreMetin(p).length < 100);
      const konum = konumEl ? hucreMetin(konumEl) : null;
      const birim = txt.match(/(\d[\d.]*)\s*(?:₺\s*)?\/\s*m²/i);
      const tarih = txt.match(/\b\d{2}\.\d{2}\.20\d{2}\b/)?.[0] || null;
      out.push({ baslik, fiyat, m2: alan.m2, alanTur: alan.tur || null,
        m2BirimFiyat: birim ? sayi(birim[1]) : (alan.m2 ? Math.round(fiyat / alan.m2) : null),
        ilanTarihi: tarih, link, konum, kategori: /kategori\s+([^·]+)/i.exec(txt)?.[1]?.trim() || null, metin: txt.slice(0, 900) });
    }
    return out.slice(0, 100);
  }

  function hepsiemlakIlanlari() {
    const out = [];
    for (const kart of document.querySelectorAll("li.listingCard")) {
      const linkEl = kart.querySelector("a.listingCard__media-link[href], a.card-link[href]");
      if (!linkEl) continue;
      const txt = hucreMetin(kart);
      const fiyat = metindenToplamFiyat(txt);
      if (!fiyat) continue;
      const baslik = linkEl.getAttribute("title") || hucreMetin(linkEl);
      const alan = (Core.m2Cikar?.(txt) || m2Cikar(txt));
      const konum = txt.match(/([A-ZÇĞİÖŞÜa-zçğıöşü ]+)\s*\/\s*([A-ZÇĞİÖŞÜa-zçğıöşü ]+)\s*\/\s*([^\n]+?\s+Mah\.?)(?:\s|$)/i)?.[0]?.trim() || null;
      const birim = txt.match(/m²\s*Fiyatı\s*(\d[\d.]*)\s*TL/i);
      const tarih = txt.match(/\b\d{2}\/\d{2}\/20\d{2}\b/)?.[0] || null;
      const kategori = txt.match(/Kategori\s+([^·]+)/i)?.[1]?.trim() || null;
      out.push({ baslik: String(baslik || "").slice(0, 150), fiyat, m2: alan.m2, alanTur: alan.tur || null,
        m2BirimFiyat: birim ? sayi(birim[1]) : (alan.m2 ? Math.round(fiyat / alan.m2) : null),
        ilanTarihi: tarih, link: Core.normalizeUrl?.(linkEl.href, location.href) || linkEl.href.split("?")[0],
        konum, kategori, metin: txt.slice(0, 1000) });
    }
    return out.slice(0, 100);
  }

  function siteOzelIlanlari() {
    const host = location.hostname.toLocaleLowerCase("tr");
    if (host.includes("sahibinden")) return sahibindenSatirlari();
    if (host.includes("emlakjet")) return emlakjetIlanlari();
    if (host.includes("hepsiemlak")) return hepsiemlakIlanlari();
    return [];
  }

  function ilanTara() {
    const gorulen = new Set();
    const out = [];
    for (const k of siteOzelIlanlari()) {
      const anahtar = k.link ? "L:" + k.link : "T:" + k.baslik + "|" + k.fiyat;
      if (gorulen.has(anahtar)) continue;
      gorulen.add(anahtar);
      out.push(k);
    }
    if (out.length) return out;
    const dugumler = fiyatDugumleri();
    for (const d of dugumler) {
      const kart = kartBul(d.el);
      if (!kart) continue;
      const link = kart.querySelector("a[href]");
      let anahtar = null;
      try { if (link) anahtar = "L:" + new URL(link.getAttribute("href"), location.href).href.split("?")[0]; } catch { /* yoksay */ }
      if (!anahtar) anahtar = "T:" + hucreMetin(kart).slice(0, 120) + "|" + d.deger;
      if (gorulen.has(anahtar)) continue;
      gorulen.add(anahtar);
      const k = kartOku(kart, d.deger);
      if (k.baslik) out.push(k);
      if (out.length >= 100) break;
    }
    return out;
  }

  // URL'den il/ilçe/tip tahmini: önce bilinen desenler, sonra il-listesi eşleşmesi
  let _ilListesi = null;
  async function ilListesi() {
    if (_ilListesi) return _ilListesi;
    try {
      const base = await backendUrl();
      const j = await dayanikliGetir(base + "/api/iller");
      _ilListesi = (Array.isArray(j) ? j : []).map((x) => ({ ad: x.ad, slug: $slug(x.ad) })).filter((x) => x.slug);
    } catch { _ilListesi = []; }
    return _ilListesi;
  }

  function tipTahmin(path) {
    const p = path.toLocaleLowerCase("tr");
    if (/arsa|arazi|tarla|bahce|bağ/.test(p)) return "arsa";
    if (/is-yeri|iş-yeri|isyeri|ticari|ofis|büro|buro|dükkan|dukkan|mağaza|magaza|depo|fabrika|atölye|atolye/.test(p)) return "ticari";
    return "konut";
  }

  // Sayfa-içi coğrafya: breadcrumb + kart-içi konum yazıları
  // (URL'de il/ilçe yoksa — örn. /satilik-konut kök sayfası)
  function kirintiOku() {
    const out = [];
    const seciciler = "nav[aria-label] a, ol.breadcrumb a, ul.breadcrumb a, [class*=readcrumb i] a, [class*=readcrumb i] span";
    for (const el of document.querySelectorAll(seciciler)) {
      const t = hucreMetin(el);
      if (t && t.length < 60 && !/ana\s*sayfa|home/i.test(t)) out.push(t);
    }
    return [...new Set(out)];
  }

  function konumCikar(kartEl) {
    if (!kartEl) return null;
    const seciciler = "[class*=location i], [class*=adres i], [class*=konum i], [class*=district i], [class*=city i], [class*=semt i], [class*=mahalle i]";
    for (const el of kartEl.querySelectorAll(seciciler)) {
      const t = hucreMetin(el);
      if (t && t.length > 1 && t.length < 80) return t;
    }
    return null;
  }

  const GEO_ATLANACAK = /^(satilik|kiralik|konut|daire|arsa|arazi|tarla|emlak|ilan|ilanlar|villa|rezidans|mustakil|ofis|dukkan|depo|turkiye)$/;
  function cozKonum(metin, iller) {
    if (!metin) return null;
    const tok = String(metin).toLocaleLowerCase("tr").split(/[^a-z0-9çğıöşü]+/).filter(Boolean);
    for (let i = 0; i < tok.length; i++) {
      const es = iller.find((x) => x.slug === $slug(tok[i]));
      if (es) {
        const kalan = tok.slice(i + 1).filter((t) => t && !/^\d+$/.test(t) && !GEO_ATLANACAK.test(t));
        return { il: es.slug, ilce: kalan[0] ? $slug(kalan[0]) : null, mahalle: kalan.length > 1 ? kalan.slice(1, 4).map($slug).join("-") : null };
      }
    }
    return null;
  }

  async function geoGenel(href) {
    const u = new URL(href || location.href);
    const host = u.hostname.toLocaleLowerCase("tr");
    const path = u.pathname;
    // Emlakjet ilan sayfaları: /satilik-konut/{il}[-{ilce}]...
    let m = host.includes("emlakjet") && path.match(/^\/(satilik|kiralik)-[a-z]+\/([a-z0-9-]+)/);
    if (m) {
      const tok = m[2].split("-").filter(Boolean);
      return { site: "emlakjet", il: tok[0], ilce: tok.length > 1 ? tok.slice(1).join("-") : null, tip: tipTahmin(path), url: u.href.split("?")[0] };
    }
    // Genel: path token'larında il slug'ı ara
    const iller = await ilListesi();
    const tok = path.toLocaleLowerCase("tr").split(/[^a-z0-9çğıöşü]+/).filter(Boolean);
    for (let i = 0; i < tok.length; i++) {
      const s = $slug(tok[i]);
      const es = iller.find((x) => x.slug === s);
      if (es) {
        const kalan = tok.slice(i + 1).filter((t) => t && !/^\d+$/.test(t) && !GEO_ATLANACAK.test(t));
        return { site: "genel", il: es.slug, ilce: kalan.length ? kalan.slice(0, 3).join("-") : null, tip: tipTahmin(path), url: u.href.split("?")[0] };
      }
    }
    // URL'de yoksa: breadcrumb'ın TÜMÜ tek metin olarak (örn. "Satılık Konut İstanbul Kadıköy")
    const kirinti = kirintiOku().join(" ");
    if (kirinti) {
      const c = cozKonum(kirinti, iller);
      if (c) return { site: "genel", il: c.il, ilce: c.ilce, tip: tipTahmin(path), url: u.href.split("?")[0] };
    }
    return { site: "genel", il: null, ilce: null, tip: tipTahmin(path), url: u.href.split("?")[0] };
  }

  // --- Tam sayfa yükleme: lazy/infinite-scroll içeriği kaydırarak tetikler ---
  // Konum korunur (kullanıcının kaydırdığı yer değişmez).
  async function sayfayiYukle(maxMs = 12000) {
    const eskiY = window.scrollY;
    try {
      const bas = Date.now();
      let son = -1, sabit = 0;
      window.scrollTo(0, 0);
      while (Date.now() - bas < maxMs) {
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise((r) => setTimeout(r, 1000));
        const h = document.body.scrollHeight;
        if (h === son) { if (++sabit >= 2) break; } else { sabit = 0; son = h; }
      }
    } finally {
      window.scrollTo(0, eskiY);
    }
  }

  // --- İlan metni madenciliği: konum + m² + oda ---
  // Gazetteer: backend /api/iller, /api/ilceler/:kod, /api/coz-adres (hepsi önbellekli)
  // TKGM ara ara yavaşladığı için zaman aşımlı + 1 kez tekrar denemeli getir.
  async function dayanikliGetir(url, ms = 20000) {
    let sonHata = null;
    for (let deneme = 0; deneme < 2; deneme++) {
      const ctrl = new AbortController();
      const zaman = setTimeout(() => ctrl.abort(), ms);
      try {
        const r = await fetch(url, { signal: ctrl.signal });
        clearTimeout(zaman);
        if (!r.ok) throw new Error("HTTP " + r.status);
        return await r.json();
      } catch (e) {
        clearTimeout(zaman);
        sonHata = e;
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    throw sonHata;
  }
  const _gazeteOnbellek = {};
  async function ilceListesi(ilSlug) {
    ilSlug = $slug(ilSlug || "");
    const key = "ilceler:" + ilSlug;
    if (_gazeteOnbellek[key]) return _gazeteOnbellek[key];
    try {
      const base = await backendUrl();
      const iller = await dayanikliGetir(base + "/api/iller");
      const bul = (Array.isArray(iller) ? iller : []).find((x) => $slug(x.ad) === ilSlug);
      if (!bul) return [];
      const ilceler = await dayanikliGetir(base + "/api/ilceler/" + bul.id);
      _gazeteOnbellek[key] = (Array.isArray(ilceler) ? ilceler : [])
        .map((x) => ({ ad: x.ad, slug: $slug(x.ad) })).filter((x) => x.slug);
      try { chrome.storage.local.set({ [key]: { t: Date.now(), v: _gazeteOnbellek[key] } }); } catch { /* yoksay */ }
    } catch { _gazeteOnbellek[key] = []; }
    return _gazeteOnbellek[key];
  }
  async function mahalleListesi(ilSlug, ilceSlug) {
    ilSlug = $slug(ilSlug || ""); ilceSlug = $slug(ilceSlug || "");
    const key = `mahalle:${ilSlug}:${ilceSlug}`;
    if (_gazeteOnbellek[key]) return _gazeteOnbellek[key];
    try {
      const base = await backendUrl();
      const j = await dayanikliGetir(base + `/api/coz-adres?il=${encodeURIComponent(ilSlug)}&ilce=${encodeURIComponent(ilceSlug)}`);
      _gazeteOnbellek[key] = (j.mahalleler || []).map((x) => ({ ad: x.ad, slug: $slug(x.ad) })).filter((x) => x.slug);
    } catch { _gazeteOnbellek[key] = []; }
    return _gazeteOnbellek[key];
  }

  const CEKIM_EKLER = /^(da|de|ta|te|dan|den|tan|ten|n[ıiuü]|s[ıiuü]|[ıiuü]|n|ler|lar|mah|koyu|koy)$/;
  function esnekEsles(tokenSlug, gazSlug) {
    if (!tokenSlug || !gazSlug || gazSlug.length < 3) return false;
    if (tokenSlug === gazSlug) return true;
    if (tokenSlug.startsWith(gazSlug)) return CEKIM_EKLER.test(tokenSlug.slice(gazSlug.length));
    return false;
  }

  function m2Cikar(txt) {
    const t = (txt || "").replace(/\s+/g, " ");
    const deger = (s) => { const v = sayi(s); return (v !== null && v >= 10 && v <= 100000) ? Math.round(v) : null; };
    const net = t.match(/net\s*(\d[\d.]*)\s*(m²|m2|metrekare)/i);
    if (net) { const v = deger(net[1]); if (v) return { m2: v, tur: "net" }; }
    const brut = t.match(/br[üu]t\s*(\d[\d.]*)\s*(m²|m2|metrekare)/i);
    if (brut) { const v = deger(brut[1]); if (v) return { m2: v, tur: "brut" }; }
    const ilk = t.match(/(\d[\d.]*)\s*(m²|m2|metrekare)/i);
    if (ilk) { const v = deger(ilk[1]); if (v) return { m2: v, tur: "alan" }; }
    return { m2: null };
  }

  // Aynı adı taşıyan ilçe varsa mahalle adayını ele (örn. Çankaya mah. vs Çankaya ilçesi → ilçe kazanır)
  function suzMahalle(mahalleler, ilceler) {
    if (!mahalleler?.length || !ilceler?.length) return mahalleler || [];
    const ilceKumesi = new Set(ilceler.map((x) => x.slug));
    return mahalleler.filter((m) => !ilceKumesi.has(m.slug));
  }

  function metinMadenciligi(metin, gaz) {
    const sonuc = {};
    const ham = (metin || "").toLocaleLowerCase("tr");
    const tok = ham.split(/[^a-z0-9çğıöşü]+/).filter(Boolean).map($slug);
    const ngram = [];
    for (let i = 0; i < tok.length; i++)
      for (let n = 1; n <= 3 && i + n <= tok.length; n++)
        ngram.push(tok.slice(i, i + n).join(""));
    const bul = (liste) => {
      let enIyi = null;
      for (const g of liste || []) {
        if (ngram.some((t) => esnekEsles(t, g.slug))) {
          if (!enIyi || g.slug.length > enIyi.slug.length) enIyi = g;
        }
      }
      return enIyi;
    };
    const mah = gaz.mahalleler?.length ? bul(gaz.mahalleler) : null;
    const ilc = gaz.ilceler?.length ? bul(gaz.ilceler) : null;
    const il = bul(gaz.iller);
    if (mah) sonuc.mahalle = mah.ad;
    if (ilc) sonuc.ilce = ilc.ad;
    if (il) sonuc.il = il.ad;
    const m = m2Cikar(ham);
    if (m.m2) { sonuc.m2 = m.m2; sonuc.alanTur = m.tur; }
    const oda = ham.match(/([1-9])\s*\+\s*([0-4])\b/);
    if (oda && (ham.length < 250 || /daire|konut|\bev\b|kat|villa|rezidans|dubleks/i.test(ham))) {
      sonuc.oda = `${oda[1]}+${oda[2]}`;
    }
    return sonuc;
  }

  // Aday tabloları puanla, en uygun veri tablosunu seç
  function tabloBul() {
    const tablolar = [...document.querySelectorAll("table")];
    let enIyi = null, enIyiPuan = 0;
    for (const t of tablolar) {
      const metin = (t.innerText || "").toLocaleLowerCase("tr");
      let puan = 0;
      if (/m²|m2/.test(metin)) puan += 3;
      if (/mahalle|ilçe|ilce|semt|bölge|bolge/.test(metin)) puan += 2;
      if (/₺|tl\b|fiyat/.test(metin)) puan += 2;
      if (/%|değişim|degisim|amortisman|getiri/.test(metin)) puan += 1;
      const satirlar = t.querySelectorAll("tbody tr, tr").length;
      if (satirlar >= 3) puan += 1;
      if (puan > enIyiPuan) { enIyiPuan = puan; enIyi = t; }
    }
    return enIyiPuan >= 4 ? enIyi : null;
  }

  function tabloOku(tablo) {
    const satirlar = [...tablo.querySelectorAll("tbody tr")];
    const kaynak = satirlar.length ? satirlar : [...tablo.querySelectorAll("tr")];
    const out = [];
    for (const tr of kaynak) {
      const hucreler = [...tr.querySelectorAll("td, th")].map(hucreMetin).filter((x) => x);
      if (hucreler.length < 2) continue;
      if (/mahalle|ilçe|ilce|semt|bölge|^ad$/i.test(hucreler[0]) && !/\d/.test(hucreler[1] || "")) continue; // başlık
      const ad = hucreler[0];
      if (!ad || ad.length > 60) continue;
      let m2 = null, ortFiyat = null, degisim = null;
      for (const h of hucreler.slice(1)) {
        const alt = h.toLocaleLowerCase("tr");
        if (/%/.test(h)) {
          if (/yıl|yil|amorti/.test(alt)) continue; // "21 yıl (%4.69)" amortisman kolonu, değişim değil
          const v = sayi(h);
          if (v !== null && degisim === null && Math.abs(v) < 1000) degisim = v;
        } else if (/m²|m2/.test(alt)) {
          const v = sayi(h);
          if (v !== null && m2 === null && v > 100) m2 = Math.round(v);
        } else if (/₺|\btl\b/.test(alt)) {
          const v = sayi(h);
          if (v !== null && ortFiyat === null && v > 1000) ortFiyat = Math.round(v);
        }
      }
      if (m2 !== null || ortFiyat !== null) out.push({ ad, m2, ortFiyat, yillikDegisim: degisim });
    }
    return out;
  }

  function piyasaSeviye(tablo, geo) {
    const baslik = hucreMetin(tablo?.querySelector("tr") || tablo).toLocaleLowerCase("tr");
    if (/mahalle/.test(baslik)) return "mahalle";
    if (/ilçe|ilce/.test(baslik)) return "ilce";
    if (/iller/.test(baslik)) return "il";
    return geo?.seviye === "ilce" ? "mahalle" : geo?.seviye === "il" ? "ilce" : "il";
  }

  function piyasaAltBaglantilari(tablo, geo) {
    const seviye = piyasaSeviye(tablo, geo);
    if (seviye === "mahalle") return [];
    const kok = `/emlak-piyasasi/${geo.islem || "satilik"}-${geo.tip || "arsa"}/`;
    const out = [];
    for (const tr of tablo?.querySelectorAll("tbody tr, tr") || []) {
      const a = tr.querySelector("td:first-child a[href]");
      if (!a) continue;
      try {
        const u = new URL(a.getAttribute("href"), location.href);
        if (u.origin !== location.origin || !u.pathname.startsWith(kok)) continue;
        out.push(Core.normalizeUrl?.(u.href) || u.href.split("?")[0]);
      } catch { /* yoksay */ }
    }
    return [...new Set(out)];
  }

  // Emlak Piyasası yaprak sayfaları (mahalle) tablo içermez; sayfanın
  // kendi "Güncel İstatistikler" bloğunu tek satır olarak okur.
  function emlakjetOzetOku() {
    if (!/emlakjet\.com$/i.test(location.hostname.replace(/^www\./, ""))) return null;
    if (!/^\/emlak-piyasasi\//.test(location.pathname)) return null;
    const t = (document.body.innerText || "").replace(/\s+/g, " ");
    const m2 = sayi(t.match(/m²\s*Sat(?:ış|is)\s*Fiyat(?:ı|i)\s*([\d.,]+)\s*₺/i)?.[1]);
    if (m2 === null) return null;
    const degisim = sayi(t.match(/Y(?:ı|i)ll(?:ı|i)k\s*De(?:ğ|g)i(?:ş|s)im\s*([+-]?[\d.,]+)\s*%/i)?.[1]);
    const ort = sayi(t.match(/Ortalama\s*Fiyat\s*₺\s*([\d.,]+)/i)?.[1]);
    return { m2: Math.round(m2), ortFiyat: ort ? Math.round(ort) : null, yillikDegisim: degisim };
  }

  async function tara(urlOverride, geoOverride) {
    if (!destekliVeriSayfasi(urlOverride || location.href)) {
      return { ok: false, atla: true, hata: "Bu sayfa emlak sonuç/piyasa sayfası değil; detay, ana sayfa ve araç ilanları kaydedilmez." };
    }
    await sayfayiYukle();
    let geo = geoTahmin(urlOverride);
    if (!geo) geo = await geoGenel(urlOverride);
    if (geoOverride?.il) { geo.il = $slug(geoOverride.il); }
    if (geoOverride?.ilce !== undefined) { geo.ilce = geoOverride.ilce ? $slug(geoOverride.ilce) : null; }
    if (!geo.il) {
      const g2 = await geoGenel(urlOverride);
      if (g2.il) geo = { ...geo, ...g2 };
    }
    const tablo = tabloBul();
    if (tablo) {
      const satirlar = tabloOku(tablo);
      if (satirlar.length) return { ok: true, tur: "piyasa", geo, satirlar,
        seviye: piyasaSeviye(tablo, geo), altSayfalar: piyasaAltBaglantilari(tablo, geo),
        donem: Core.donemCikar?.(document.body.innerText) || new Date().toISOString().slice(0, 7) };
    }
    const yaprak = emlakjetOzetOku();
    if (yaprak) {
      const seviye = geo.mahalle ? "mahalle" : geo.ilce ? "ilce" : geo.il ? "il" : "ulke";
      const ad = geo.mahalle || geo.ilce || geo.il || "turkiye";
      return { ok: true, tur: "piyasa", geo, seviye, altSayfalar: [],
        satirlar: [{ ad, ...yaprak }],
        donem: Core.donemCikar?.(document.body.innerText) || new Date().toISOString().slice(0, 7) };
    }
    // Tablo yoksa: genel ilan kartı dedektörü (arama/sonuç sayfaları)
    const ilanlar = ilanTara();
    if (ilanlar.length) {
    // Her satırın kart-içi konumunu çöz + ilan METNİNDEN il/ilçe/mahalle/m²/oda çıkar
    // Aşama 1 il-bağımsızdır (sayfa coğrafyası yoksa bile çalışır).
    const tani = { ilSayisi: 0, ilceSayisi: 0, mahalleSayisi: 0, geoKaynak: "yok" };
    {
      const iller = await ilListesi();
      tani.ilSayisi = iller.length;
      for (const s of ilanlar) {
        const hizli = Core.konumParcala?.(s.konum, geo) || null;
        if (hizli?.il) {
          s.il = hizli.il;
          if (hizli.ilce) s.ilce = hizli.ilce;
          if (hizli.mahalle) s.mahalle = hizli.mahalle;
        }
        const c = !s.il && s.konum ? cozKonum(s.konum, iller) : null;
        if (c?.il) { s.il = c.il; if (c.ilce) s.ilce = c.ilce; if (c.mahalle) s.mahalle = c.mahalle; }
        if (!s.il && geo.il) { s.il = geo.il; s.ilce = s.ilce || geo.ilce; }
      }
      // Aşama 1: il + oda + m² (gazetteer: 81 il, bağlam gerekmez)
      const gazIl = { iller, ilceler: [], mahalleler: [] };
      for (const s of ilanlar) {
        const mad = metinMadenciligi(`${s.baslik || ""} ${s.konum || ""} ${s.metin || ""}`, gazIl);
        if (mad.il && !s.il) s.il = mad.il;
        if (mad.m2 && !s.m2) s.m2 = mad.m2;
        if (mad.oda) s.oda = mad.oda;
      }
      // Aşama 2: ilçe + mahalle (sayfa/-satır ili bağlamında gazetteer gerekir)
      const sayfaIl = geo.il || ilanlar.find((s) => s.il)?.il;
      if (sayfaIl) {
        const ilceler = await ilceListesi($slug(sayfaIl));
        tani.ilceSayisi = ilceler.length;
        const sayfaIlce = geo.ilce || ilanlar.find((s) => s.ilce)?.ilce;
        const mahalleler = suzMahalle(sayfaIlce ? await mahalleListesi($slug(sayfaIl), $slug(sayfaIlce)) : [], ilceler);
        tani.mahalleSayisi = mahalleler.length;
        tani.geoKaynak = `sayfa:${sayfaIl}/${sayfaIlce || "-"}`;
        const gaz = { iller, ilceler, mahalleler };
        for (const s of ilanlar) {
          if (!s.il) continue; // başka ilin ilçesini yanlış eşleme
          const mad = metinMadenciligi(`${s.baslik || ""} ${s.konum || ""} ${s.metin || ""}`, gaz);
          if (mad.mahalle && !s.mahalle && (!mad.il || mad.il === s.il)) s.mahalle = mad.mahalle;
          if (mad.ilce && !s.ilce) {
            // ilçe bu ilin listesinden mi?
            if (ilceler.some((x) => x.slug === $slug(mad.ilce))) s.ilce = mad.ilce;
          }
          if (mad.m2 && !s.m2) s.m2 = mad.m2;
          if (mad.oda && !s.oda) s.oda = mad.oda;
        }
        // Aşama 3: satırlarda beliren başka (il, ilçe) çiftleri için mahalle gazetesi
        const ciftler = new Map();
        for (const s of ilanlar) {
          if (s.il && s.ilce && !s.mahalle) {
            const k = `${$slug(s.il)}|${$slug(s.ilce)}`;
            if (!ciftler.has(k) && ciftler.size < 3) ciftler.set(k, { il: s.il, ilce: s.ilce });
          }
        }
        const tarandi = new Set();
        if (mahalleler.length && sayfaIlce) tarandi.add(`${$slug(sayfaIl)}|${$slug(sayfaIlce)}`);
        for (const { il, ilce } of ciftler.values()) {
          if (tarandi.has(`${$slug(il)}|${$slug(ilce)}`)) continue; // gerçekten tarandıysa atla
          const ayniIl = $slug(il) === $slug(sayfaIl);
          const mah2ham = await mahalleListesi($slug(il), $slug(ilce));
          const mah2 = ayniIl ? suzMahalle(mah2ham, ilceler) : mah2ham;
          if (!mah2.length) continue;
          tani.mahalleSayisi += mah2.length;
          const gaz2 = { iller, ilceler: ilceler, mahalleler: mah2 };
          for (const s of ilanlar) {
            if (s.mahalle || !s.il) continue;
            if ($slug(s.il) !== $slug(il)) continue;
            const mad = metinMadenciligi(`${s.baslik || ""} ${s.konum || ""} ${s.metin || ""}`, gaz2);
            if (mad.mahalle) s.mahalle = mad.mahalle;
          }
        }
      }
    }
    if (!geo.il) {
      const cozulen = ilanlar.filter((s) => s.il).length;
      if (!cozulen) return { ok: false, hata: "İlanlar bulundu ama il/ilçe çözülemedi — popup'taki il/ilçe kutularını doldurun.", ilanAdet: ilanlar.length, geo, tani };
      const ilk = ilanlar.find((s) => s.il);
      geo = { ...geo, il: ilk.il, ilce: ilk.ilce };
      return { ok: true, tur: "ilan", geo, ilanlar, cozulmeyen: ilanlar.length - cozulen,
        donem: new Date().toISOString().slice(0, 7), tani, sayfa: Core.sayfaNoCikar?.(location.href) || 1 };
    }
      return { ok: true, tur: "ilan", geo, ilanlar, donem: new Date().toISOString().slice(0, 7), tani,
        sayfa: Core.sayfaNoCikar?.(location.href) || 1 };
    }
    return { ok: false, atla: true, hata: "Sayfada fiyat tablosu veya ilan kartı bulunamadı. Sayfanın tam yüklendiğinden emin olun.", geo };
  }

  async function backendUrl() {
    const o = await chrome.storage.sync.get({ backend: "http://localhost:3001" });
    return (o.backend || "http://localhost:3001").replace(/\/$/, "");
  }

  async function kaydet(urlOverride, geoOverride) {
    const sonuc = await tara(urlOverride, geoOverride);
    if (!sonuc.ok) return sonuc;
    const { geo, donem } = sonuc;
    const base = await backendUrl();
    let yol, govde;
    if (sonuc.tur === "ilan") {
      yol = "/api/ilanlar";
      govde = {
        satirlar: sonuc.ilanlar.map((s) => ({
          baslik: s.baslik, fiyat: s.fiyat, m2: s.m2, m2BirimFiyat: s.m2BirimFiyat || null,
          alanTur: s.alanTur || null, oda: s.oda || null, kategori: s.kategori || null,
          il: s.il || geo.il, ilce: s.ilce || geo.ilce, mahalle: s.mahalle || null,
          tip: geo.tip, islem: geo.islem || null, site: geo.site || Core.siteTahmin?.(location.href) || "genel",
          ilanTarihi: s.ilanTarihi || null, sayfa: sonuc.sayfa || 1,
          link: s.link, donem, kaynak: Core.normalizeUrl?.(location.href) || geo.url,
          toplanmaZamani: new Date().toISOString(),
        })),
      };
    } else {
      const { satirlar } = sonuc;
      const seviye = sonuc.seviye || "ilce";
      yol = "/api/piyasa";
      govde = {
        satirlar: satirlar.map((s) => ({
          il: seviye === "il" ? s.ad : geo.il,
          ilce: seviye === "ilce" ? s.ad : (seviye === "mahalle" ? geo.ilce : null),
          mahalle: seviye === "mahalle" ? s.ad : null,
          seviye, site: geo.site || "genel", islem: geo.islem || null,
          tip: geo.tip, m2: s.m2, ortFiyat: s.ortFiyat,
          yillikDegisim: s.yillikDegisim, donem,
          kaynak: Core.normalizeUrl?.(location.href) || geo.url,
          toplanmaZamani: new Date().toISOString(),
        })),
      };
    }
    const r = await fetch(base + yol, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      return { ok: false, hata: "Backend hatası: " + (j.error || r.status) };
    }
    const j = await r.json();
    const sonuc2 = { ok: true, tur: sonuc.tur, eklenen: j.eklenen ?? j.kaydedilen ?? 0,
      guncellenen: j.guncellenen || 0, toplam: govde.satirlar.length,
      seviye: sonuc.seviye || null, altSayfalar: sonuc.altSayfalar || [], sayfa: sonuc.sayfa || 1 };
    istatistikGuncelle(sonuc2, geo.url).catch(() => {});
    return sonuc2;
  }

  // İstatistik: günlük sayaç + son kayıt + ikon rozeti (popup canlı gösterir)
  async function istatistikGuncelle(s, url) {
    const bugun = new Date().toISOString().slice(0, 10);
    let o = {};
    try { o = await chrome.storage.local.get({ gunluk: null, son: null }); } catch { /* yoksay */ }
    let gunluk = o.gunluk && o.gunluk.tarih === bugun ? o.gunluk : { tarih: bugun, adet: 0 };
    gunluk.adet += (s.eklenen || 0) + (s.guncellenen || 0);
    const son = { zaman: Date.now(), adet: (s.eklenen || 0) + (s.guncellenen || 0), tur: s.tur || null, url: url || null };
    try { await chrome.storage.local.set({ gunluk, son }); } catch { /* yoksay */ }
    try { await chrome.action?.setBadgeText?.({ text: gunluk.adet > 0 ? String(gunluk.adet > 99 ? "99+" : gunluk.adet) : "" }); } catch { /* yoksay */ }
    try { await chrome.action?.setBadgeBackgroundColor?.({ color: "#0b3d2e" }); } catch { /* yoksay */ }
  }

  // Kayan "Kaydet" düğmesi
  function dugmeEkle() {
    if (!destekliVeriSayfasi()) return;
    if (document.getElementById("piyasa-toplayici-btn")) return;
    const b = document.createElement("button");
    b.id = "piyasa-toplayici-btn";
    b.textContent = "📊 Piyasayı kaydet";
    b.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:999999;padding:10px 14px;border:0;border-radius:10px;background:#0b3d2e;color:#fff;font:600 13px system-ui;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.3)";
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "⏳ Kaydediliyor…";
      try {
        const s = await kaydet();
        b.textContent = s.ok ? `✅ ${s.eklenen + s.guncellenen} satır` : `❌ ${s.hata}`;
      } catch (e) {
        b.textContent = "❌ " + String(e.message || e).slice(0, 60);
      }
      setTimeout(() => { b.disabled = false; b.textContent = "📊 Piyasayı kaydet"; }, 4000);
    };
    document.documentElement.appendChild(b);
  }

  function sonrakiSayfaUrl() {
    const host = location.hostname.toLocaleLowerCase("tr");
    const adaylar = [];
    if (host.includes("emlakjet") || host.includes("hepsiemlak")) {
      adaylar.push('a[aria-label="Sonraki Sayfa"]');
    }
    if (host.includes("sahibinden")) adaylar.push("a.prevNextBut");
    adaylar.push('a[rel="next"]');
    for (const secici of adaylar) {
      for (const a of document.querySelectorAll(secici)) {
        const metin = hucreMetin(a);
        if (secici === "a.prevNextBut" && !/sonraki/i.test(metin)) continue;
        if (a.getAttribute("aria-disabled") === "true" || a.classList.contains("disabled")) continue;
        try {
          const u = new URL(a.getAttribute("href"), location.href);
          if (u.origin !== location.origin || !/^https?:$/.test(u.protocol)) continue;
          return Core.normalizeUrl?.(u.href) || u.href;
        } catch { /* yoksay */ }
      }
    }
    return null;
  }

  let akademikKosuyor = false;
  async function taramaDurumuOku() {
    try { return (await chrome.storage.local.get({ taramaDurumu: null })).taramaDurumu; }
    catch { return null; }
  }

  async function taramaBaslat(mod, ayar = {}) {
    const izin = await chrome.storage.sync.get({ yaziliIzin: false });
    if (izin.yaziliIzin !== true) {
      return { ok: false, hata: "Otomatik tarama için yazılı izin onayı gerekli." };
    }
    const simdi = Core.normalizeUrl?.(location.href) || location.href.split("#")[0];
    if (mod === "emlakjet_piyasa" && !/emlakjet\.com\/emlak-piyasasi\//i.test(simdi)) {
      return { ok: false, hata: "Emlakjet il-ilçe-mahalle taraması yalnızca Emlak Piyasası sayfasında başlatılabilir." };
    }
    if (mod === "ilan_sayfalama" && /\/emlak-piyasasi\//i.test(new URL(simdi).pathname)) {
      return { ok: false, hata: "Bu sayfa piyasa tablosu. İl-ilçe-mahalle tarama düğmesini kullanın." };
    }
    const maxSayfa = Math.min(2000, Math.max(1, Number(ayar.maxSayfa) || (mod === "emlakjet_piyasa" ? 1200 : 50)));
    const beklemeMs = Math.min(60_000, Math.max(4_000, (Number(ayar.beklemeSaniye) || 8) * 1000));
    const durum = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, aktif: true, mod,
      baslangicUrl: simdi, siradakiUrl: simdi, kuyruk: [simdi], ziyaretEdilen: [],
      maxSayfa, beklemeMs, islenenSayfa: 0, eklenen: 0, guncellenen: 0,
      baslamaZamani: new Date().toISOString(), sonGuncelleme: new Date().toISOString(), hata: null,
    };
    await chrome.storage.local.set({ taramaDurumu: durum });
    setTimeout(() => aktifTaramaDevam(), 100);
    return { ok: true, durum };
  }

  async function taramaDurdur(neden = "Kullanıcı durdurdu") {
    const durum = await taramaDurumuOku();
    if (!durum) return { ok: true, durum: null };
    durum.aktif = false;
    durum.bitisNedeni = neden;
    durum.sonGuncelleme = new Date().toISOString();
    await chrome.storage.local.set({ taramaDurumu: durum });
    toast(`⏹️ Tarama durdu: ${neden}`);
    return { ok: true, durum };
  }

  async function aktifTaramaDevam() {
    if (akademikKosuyor) return;
    let durum = await taramaDurumuOku();
    if (!durum?.aktif) return;
    const simdi = Core.normalizeUrl?.(location.href) || location.href.split("#")[0];
    if ((Core.normalizeUrl?.(durum.siradakiUrl) || durum.siradakiUrl) !== simdi) return;
    akademikKosuyor = true;
    try {
      const sonuc = await kaydet();
      if (!sonuc.ok && !sonuc.atla) {
        durum.aktif = false;
        durum.hata = sonuc.hata || "Sayfa okunamadı";
        durum.bitisNedeni = "Hata/engel algılandı; otomatik geçiş durduruldu";
        await chrome.storage.local.set({ taramaDurumu: durum });
        toast(`⛔ Tarama durdu: ${durum.hata}`);
        return;
      }
      // Veri çıkmayan sayfa (ör. mahalle yaprağı) taramayı bitirmez:
      // bu adres işaretlenir ve kuyruğun kalanıyla sürülür.
      if (!sonuc.ok) {
        durum.atlanan = (durum.atlanan || 0) + 1;
        durum.ziyaretEdilen.push(simdi);
        durum.ziyaretEdilen = [...new Set(durum.ziyaretEdilen)].slice(-2500);
        durum.kuyruk = (durum.kuyruk || []).filter((u) => (Core.normalizeUrl?.(u) || u) !== simdi);
        durum.siradakiUrl = durum.kuyruk[0] || null;
        durum.sonGuncelleme = new Date().toISOString();
        if (!durum.siradakiUrl) { durum.aktif = false; durum.bitisNedeni = "Tüm erişilebilir sayfalar tamamlandı"; }
        await chrome.storage.local.set({ taramaDurumu: durum });
        if (!durum.aktif) return;
        const atlaId = durum.id, atlaHedef = durum.siradakiUrl;
        setTimeout(async () => {
          const son = await taramaDurumuOku();
          if (son?.aktif && son.id === atlaId && son.siradakiUrl === atlaHedef) location.assign(atlaHedef);
        }, durum.beklemeMs);
        return;
      }

      durum.islenenSayfa += 1;
      durum.eklenen += sonuc.eklenen || 0;
      durum.guncellenen += sonuc.guncellenen || 0;
      durum.ziyaretEdilen.push(simdi);
      durum.ziyaretEdilen = [...new Set(durum.ziyaretEdilen)].slice(-2500);
      durum.kuyruk = (durum.kuyruk || []).filter((u) => (Core.normalizeUrl?.(u) || u) !== simdi);

      if (durum.mod === "emlakjet_piyasa") {
        const bilinen = new Set([...durum.ziyaretEdilen, ...durum.kuyruk].map((u) => Core.normalizeUrl?.(u) || u));
        for (const u of sonuc.altSayfalar || []) {
          const n = Core.normalizeUrl?.(u) || u;
          if (!bilinen.has(n)) { durum.kuyruk.push(n); bilinen.add(n); }
        }
      } else {
        const sonraki = sonrakiSayfaUrl();
        if (sonraki && !durum.ziyaretEdilen.includes(sonraki)) durum.kuyruk = [sonraki];
      }

      durum.sonGuncelleme = new Date().toISOString();
      if (durum.islenenSayfa >= durum.maxSayfa) {
        durum.aktif = false;
        durum.bitisNedeni = `Azami ${durum.maxSayfa} sayfa sınırına ulaşıldı`;
      } else if (!durum.kuyruk.length) {
        durum.aktif = false;
        durum.bitisNedeni = "Tüm erişilebilir sayfalar tamamlandı";
      }
      durum.siradakiUrl = durum.kuyruk[0] || null;
      await chrome.storage.local.set({ taramaDurumu: durum });
      toast(`📚 ${durum.islenenSayfa}/${durum.maxSayfa} sayfa • ${durum.eklenen} yeni • kuyruk ${durum.kuyruk.length}`);
      if (!durum.aktif || !durum.siradakiUrl) return;

      const gorevId = durum.id;
      const hedef = durum.siradakiUrl;
      setTimeout(async () => {
        const son = await taramaDurumuOku();
        const halaBurada = (Core.normalizeUrl?.(location.href) || location.href.split("#")[0]) === simdi;
        if (son?.aktif && son.id === gorevId && son.siradakiUrl === hedef && halaBurada) location.assign(hedef);
      }, durum.beklemeMs);
    } catch (e) {
      durum.aktif = false;
      durum.hata = String(e.message || e).slice(0, 300);
      durum.bitisNedeni = "Beklenmeyen hata";
      durum.sonGuncelleme = new Date().toISOString();
      await chrome.storage.local.set({ taramaDurumu: durum });
      toast(`⛔ Tarama durdu: ${durum.hata}`);
    } finally {
      akademikKosuyor = false;
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, yanıt) => {
    if (msg.type === "TARA") {
      tara(undefined, msg.geo).then(yanıt).catch((e) => yanıt({ ok: false, hata: String(e.message || e) }));
      return true;
    }
    if (msg.type === "KAYDET") {
      kaydet(undefined, msg.geo).then(yanıt).catch((e) => yanıt({ ok: false, hata: String(e.message || e) }));
      return true;
    }
    if (msg.type === "TARAMA_BASLAT") {
      taramaBaslat(msg.mod, msg.ayar).then(yanıt).catch((e) => yanıt({ ok: false, hata: String(e.message || e) }));
      return true;
    }
    if (msg.type === "TARAMA_DURDUR") {
      taramaDurdur().then(yanıt).catch((e) => yanıt({ ok: false, hata: String(e.message || e) }));
      return true;
    }
    if (msg.type === "TARAMA_DURUM") {
      taramaDurumuOku().then((durum) => yanıt({ ok: true, durum })).catch((e) => yanıt({ ok: false, hata: String(e.message || e) }));
      return true;
    }
  });

  dugmeEkle();

  // OTOMATİK PİLOT: "otomatik" açıkken, kullanıcının KENDİ açtığı sayfa
  // her yüklendiğinde VE site-içi filtre/şehir değişiminde (SPA gezinme dahil)
  // sonucu otomatik kaydeder. Aynı içerik iki kez kaydedilmez.
  // Normal otomatik pilot gezinmez. Sayfa/hiyerarsi taraması popup'tan açıkça başlatılır.
  let otoKosuyor = false, otoZamanlayici = null, sonImza = "";
  async function otomatikAcik() {
    try {
      const o = await chrome.storage.sync.get({ otomatik: false, yaziliIzin: false });
      return o.otomatik === true && o.yaziliIzin === true;
    } catch { return false; }
  }
  function icerikImzasi() {
    try {
      const fiyatlar = [...document.body.innerText.matchAll(/[₺][\s\d.]+|\d[\d.]*\s*TL/gi)]
        .map((m) => m[0]).slice(0, 12).join("|");
      return location.href.split("#")[0] + "#" + fiyatlar.length + "#" + fiyatlar.slice(0, 200);
    } catch { return location.href; }
  }
  async function otomatikCalis(sebep) {
    if (otoKosuyor) return;
    if (!(await otomatikAcik())) return;
    if ((await taramaDurumuOku())?.aktif) return;
    clearTimeout(otoZamanlayici);
    otoZamanlayici = setTimeout(async () => {
      if (otoKosuyor) return;
      otoKosuyor = true;
      try {
        const imza = icerikImzasi();
        if (imza === sonImza) return; // aynı içerik: tekrar kaydetme
        const s = await kaydet();
        window.PiyasaToplayici.sonOtomatik = { zaman: Date.now(), sebep, sonuc: s };
        if (s.ok) {
          sonImza = imza;
          if (s.eklenen + s.guncellenen > 0) {
            toast(`✅ Otomatik pilot: ${s.eklenen + s.guncellenen} satır (${s.tur === "ilan" ? "ilan" : "piyasa"})`);
          }
        } else {
          toast(`⏭️ ${s.hata || "kaydedilmedi"}`);
        }
      } catch (e) {
        toast("⏭️ " + String(e.message || e).slice(0, 80));
      } finally {
        otoKosuyor = false;
      }
    }, 2500);
  }
  // İlk yükleme
  otomatikCalis("yukleme");
  setTimeout(() => aktifTaramaDevam(), 1600);
  // SPA gezinme: filtre/şehir değişimleri genelde pushState ile olur
  for (const metot of ["pushState", "replaceState"]) {
    try {
      const orig = history[metot];
      history[metot] = function (...a) {
        const r = orig.apply(this, a);
        otomatikCalis("gecmis");
        return r;
      };
    } catch { /* yoksay */ }
  }
  window.addEventListener("popstate", () => otomatikCalis("popstate"));
  window.addEventListener("hashchange", () => otomatikCalis("hash"));
  // AJAX ile sonuç yenileyen siteler için DOM nöbetçisi (seyrek + imza korumalı)
  let gozlemSayac = 0;
  try {
    const gozlemci = new MutationObserver(() => {
      if (++gozlemSayac % 3 !== 0) return;
      otomatikCalis("dom");
    });
    gozlemci.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => gozlemci.disconnect(), 120000); // 2 dk sonra nöbeti bırak (pil/CPU)
  } catch { /* yoksay */ }

  function toast(mesaj) {
    let t = document.getElementById("piyasa-toplayici-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "piyasa-toplayici-toast";
      t.style.cssText = "position:fixed;left:16px;bottom:16px;z-index:999999;padding:10px 14px;border-radius:10px;background:#111;color:#fff;font:12px system-ui;opacity:.92;max-width:320px";
      document.documentElement.appendChild(t);
    }
    t.textContent = mesaj;
    clearTimeout(t._k);
    t._k = setTimeout(() => t.remove(), 5000);
  }

  // Konsol/test kancası (salt-okunur çıkarma)
  window.PiyasaToplayici = { tara, kaydet, geoTahmin, geoGenel, sayi, ilanTara, fiyatDugumleri, kartBul, destekliVeriSayfasi,
    kirintiOku, cozKonum, ilListesi, ilceListesi, mahalleListesi, metinMadenciligi, esnekEsles, m2Cikar,
    tabloOku, piyasaSeviye, piyasaAltBaglantilari, sonrakiSayfaUrl, taramaBaslat, taramaDurdur, taramaDurumuOku };
})();
