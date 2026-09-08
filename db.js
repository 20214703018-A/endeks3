// Yerel veri deposu (IndexedDB) — eklentinin kendi diskindeki akademik havuz.
// Hem arka plan servisi hem veri sayfası bu katmanı kullanır.
// Kayıtlar TİPE GÖRE ayrılır (konut / arsa); tip her satırda alan olarak
// durur ve sorgular tipe göre süzülür.
(() => {
  "use strict";

  const DB_AD = "piyasa-toplayici";
  const DB_SURUM = 1;

  // bolge   : bir bölgenin bir dönemdeki özeti (m² fiyat, ort. fiyat, yaş…)
  // trend   : aynı bölgenin aylık serisi
  // dagilim : yaş / oda / kat / ısıtma / alan kırılımları
  const DEPOLAR = {
    bolge: { keyPath: "k", indeks: ["tip", "seviye", "ilSlug", "donem", "arama"] },
    trend: { keyPath: "k", indeks: ["tip", "bk", "ay"] },
    dagilim: { keyPath: "k", indeks: ["tip", "bk", "dagilim"] },
  };

  let _db = null;
  function ac() {
    if (_db) return Promise.resolve(_db);
    return new Promise((coz, red) => {
      const istek = indexedDB.open(DB_AD, DB_SURUM);
      istek.onupgradeneeded = () => {
        const db = istek.result;
        for (const [ad, tanim] of Object.entries(DEPOLAR)) {
          const depo = db.objectStoreNames.contains(ad)
            ? istek.transaction.objectStore(ad)
            : db.createObjectStore(ad, { keyPath: tanim.keyPath });
          for (const i of tanim.indeks) {
            if (!depo.indexNames.contains(i)) depo.createIndex(i, i, { unique: false });
          }
        }
      };
      istek.onsuccess = () => { _db = istek.result; coz(_db); };
      istek.onerror = () => red(istek.error);
    });
  }

  const bolgeAnahtar = (b) => [b.tip, b.seviye, b.ilSlug || "", b.ilceSlug || "", b.mahalleSlug || "", b.donem].join("|");
  const aramaMetni = (b) => [b.il, b.ilce, b.mahalle].filter(Boolean).join(" ").toLocaleLowerCase("tr");

  // Bir taramada üretilen paketleri tek işlemde yazar.
  // Aynı bölge+dönem tekrar gelirse satır çoğalmaz, üzerine yazılır
  // (kesintiden sonra yeniden tarama idempotenttir).
  async function bolgeleriYaz(bolgeler) {
    const db = await ac();
    return new Promise((coz, red) => {
      const t = db.transaction(["bolge", "trend", "dagilim"], "readwrite");
      const dBolge = t.objectStore("bolge");
      const dTrend = t.objectStore("trend");
      const dDagilim = t.objectStore("dagilim");
      let bolgeAdet = 0, satirAdet = 0;

      const ozetYaz = (kimlik, olcum, donem, kaynak, zaman) => {
        if (olcum?.m2Fiyat == null) return;
        const kayit = { ...kimlik, donem, kaynak: kaynak || null, toplanmaZamani: zaman, ...olcum };
        kayit.k = bolgeAnahtar(kayit);
        kayit.arama = aramaMetni(kayit);
        dBolge.put(kayit);
        satirAdet++;
      };

      for (const b of bolgeler) {
        const zaman = b.toplanmaZamani || new Date().toISOString();
        const donem = b.donem || new Date().toISOString().slice(0, 7);
        const kimlik = {
          tip: b.tip, seviye: b.seviye, il: b.il || null, ilce: b.ilce || null, mahalle: b.mahalle || null,
          ilSlug: b.ilSlug || "", ilceSlug: b.ilceSlug || "", mahalleSlug: b.mahalleSlug || "",
          cityId: b.cityId || null, countyId: b.countyId || null, districtId: b.districtId || null,
        };
        ozetYaz(kimlik, b.ozet, donem, b.kaynak, zaman);
        bolgeAdet++;

        // Alt seviye özetleri: ilçeye tek tek girmeden mahalle fiyatları da düşer
        for (const c of Array.isArray(b.cocukOzetleri) ? b.cocukOzetleri : []) {
          const { seviye, il, ilce, mahalle, ilSlug, ilceSlug, mahalleSlug,
            cityId, countyId, districtId, donem: cDonem, ...olcum } = c;
          ozetYaz(
            { tip: b.tip, seviye, il, ilce, mahalle, ilSlug: ilSlug || "", ilceSlug: ilceSlug || "",
              mahalleSlug: mahalleSlug || "", cityId, countyId, districtId },
            olcum, cDonem || donem, b.kaynak, zaman,
          );
        }

        const bk = bolgeAnahtar({ ...kimlik, donem });
        for (const t2 of Array.isArray(b.trend) ? b.trend : []) {
          if (!t2?.ay) continue;
          dTrend.put({ k: `${bk}|${t2.ay}`, bk, tip: b.tip, seviye: b.seviye,
            il: kimlik.il, ilce: kimlik.ilce, mahalle: kimlik.mahalle, kaynak: b.kaynak || null, ...t2 });
          satirAdet++;
        }
        for (const d of Array.isArray(b.dagilim) ? b.dagilim : []) {
          if (!d?.segment) continue;
          dDagilim.put({ k: `${bk}|${d.dagilim}|${d.segment}`, bk, tip: b.tip, seviye: b.seviye, donem,
            il: kimlik.il, ilce: kimlik.ilce, mahalle: kimlik.mahalle, kaynak: b.kaynak || null, ...d });
          satirAdet++;
        }
      }

      t.oncomplete = () => coz({ bolge: bolgeAdet, satir: satirAdet });
      t.onerror = () => red(t.error);
      t.onabort = () => red(t.error || new Error("IndexedDB işlemi iptal edildi"));
    });
  }

  function sayimAl(depo, tip) {
    return ac().then((db) => new Promise((coz, red) => {
      const d = db.transaction(depo, "readonly").objectStore(depo);
      const istek = tip ? d.index("tip").count(IDBKeyRange.only(tip)) : d.count();
      istek.onsuccess = () => coz(istek.result);
      istek.onerror = () => red(istek.error);
    }));
  }

  async function ozet() {
    const out = { bolge: {}, trend: {}, dagilim: {}, toplam: {} };
    for (const depo of ["bolge", "trend", "dagilim"]) {
      out.toplam[depo] = await sayimAl(depo);
      for (const tip of ["konut", "arsa"]) out[depo][tip] = await sayimAl(depo, tip);
    }
    return out;
  }

  // İmleçle süzerek arar: metin il/ilçe/mahalle adlarında geçer.
  // 100 binlerce satırda bile tek geçiş yeterince hızlıdır.
  async function ara({ depo = "bolge", metin = "", tip = "", seviye = "", limit = 300, sirala = "m2Fiyat" } = {}) {
    const db = await ac();
    const q = String(metin || "").trim().toLocaleLowerCase("tr");
    return new Promise((coz, red) => {
      const d = db.transaction(depo, "readonly").objectStore(depo);
      const kaynak = tip ? d.index("tip").openCursor(IDBKeyRange.only(tip)) : d.openCursor();
      const bulunan = [];
      let taranan = 0, eslesen = 0;
      kaynak.onsuccess = () => {
        const c = kaynak.result;
        if (!c) {
          if (sirala) {
            bulunan.sort((a, b) => (b[sirala] ?? -Infinity) - (a[sirala] ?? -Infinity));
          }
          return coz({ satirlar: bulunan.slice(0, limit), eslesen, taranan });
        }
        const v = c.value;
        taranan++;
        const seviyeUyar = !seviye || v.seviye === seviye;
        const metinUyar = !q || (v.arama || aramaMetni(v)).includes(q);
        if (seviyeUyar && metinUyar) {
          eslesen++;
          // Sıralama için makul bir tavana kadar biriktir
          if (bulunan.length < Math.max(limit, 2000)) bulunan.push(v);
        }
        c.continue();
      };
      kaynak.onerror = () => red(kaynak.error);
    });
  }

  // CSV dışa aktarım: tüm depo tek tipte taranır, satırlar parça parça toplanır.
  async function csvUret(depo, tip, kolonlar) {
    const db = await ac();
    const hucre = (v) => {
      if (v === null || v === undefined) return "";
      const s = typeof v === "object" ? JSON.stringify(v) : String(v);
      return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return new Promise((coz, red) => {
      const d = db.transaction(depo, "readonly").objectStore(depo);
      const kaynak = tip ? d.index("tip").openCursor(IDBKeyRange.only(tip)) : d.openCursor();
      const parcalar = ["﻿" + kolonlar.join(";") + "\n"];
      let adet = 0;
      kaynak.onsuccess = () => {
        const c = kaynak.result;
        if (!c) return coz({ parcalar, adet });
        parcalar.push(kolonlar.map((k) => hucre(c.value[k])).join(";") + "\n");
        adet++;
        c.continue();
      };
      kaynak.onerror = () => red(kaynak.error);
    });
  }

  async function temizle(depolar = ["bolge", "trend", "dagilim"]) {
    const db = await ac();
    return new Promise((coz, red) => {
      const t = db.transaction(depolar, "readwrite");
      for (const d of depolar) t.objectStore(d).clear();
      t.oncomplete = () => coz(true);
      t.onerror = () => red(t.error);
    });
  }

  const KOLONLAR = {
    bolge: ["donem", "tip", "seviye", "il", "ilce", "mahalle", "cityId", "countyId", "districtId",
      "m2Fiyat", "m2FiyatMin", "m2FiyatMax", "ortFiyat", "ortM2", "ilanSayisi", "aylikDegisim",
      "yillikDegisim", "degisim1Yil", "degisim2Yil", "degisim5Yil",
      "amortisman", "getiri", "ortBinaYasi", "ilanSuresi", "endeks",
      "kiraM2Fiyat", "kiraFiyat", "kiraOrtM2", "kiraIlanSayisi", "kiraYillikDegisim",
      "kiraIlanSuresi", "olcumKaynagi", "kaynak", "toplanmaZamani"],
    trend: ["tip", "seviye", "il", "ilce", "mahalle", "ay", "projeksiyon", "m2Fiyat", "m2FiyatMin", "m2FiyatMax",
      "ortFiyat", "ortM2", "ilanSayisi", "aylikDegisim", "yillikDegisim", "amortisman", "getiri",
      "endeks", "kiraM2Fiyat", "kiraFiyat", "kiraOrtM2", "kiraIlanSayisi", "ilanSuresi", "kaynak"],
    dagilim: ["donem", "tip", "seviye", "il", "ilce", "mahalle", "dagilim", "segment", "oran",
      "m2Fiyat", "ortFiyat", "ortM2", "ilanSayisi", "ortBinaYasi", "amortisman", "getiri",
      "kiraM2Fiyat", "kiraFiyat", "kaynak"],
  };

  const api = { ac, bolgeleriYaz, ozet, ara, csvUret, temizle, KOLONLAR, bolgeAnahtar };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.PiyasaDB = api;
})();
