// Piyasa Toplayıcı — arka plan servisi (MV3 service worker)
//
// Emlakjet "Emlak Piyasası" sayfalarının kendi beslendiği bölge endeksi
// ucundan (sayfadaki tablo ve grafiklerin kaynağı) Türkiye → il → ilçe →
// mahalle hiyerarşisini gezer. Her bölge için:
//   • güncel özet (m² fiyat, ortalama fiyat, yıllık değişim, amortisman…)
//   • aylık trend serisi (2020'den bugüne)
//   • yaş / oda / kat / ısıtma / alan dağılımları
// toplanır ve EKLENTİNİN KENDİ yerel veritabanına (IndexedDB) yazılır.
// Konut ve arsa kayıtları tip alanıyla ayrı tutulur, ayrı dışa aktarılır.
//
// Kuyruk chrome.storage.local'da durur ve her bölgeden sonra kaydedilir.
// Servis askıya alınsa, tarayıcı kapansa ya da bilgisayar yeniden başlasa
// bile chrome.alarms ile uyanıp kaldığı yerden sürer.

importScripts("ej-api.js", "db.js");

const {
  TIPLER, SEVIYE_SIRA, apiUrl, kaynakUrl, slugla,
  guncelOlcum, trendSatirlari, donemDegisimleri, dagilimSatirlari, cocuklar,
} = globalThis.EmlakjetApi;
const DB = globalThis.PiyasaDB;

const ALARM = "ej-tarama-tik";
// Alarm dakikada bir uyandırır; parti bunun hemen altında biter ki
// uyanışlar arasında boş süre kalmasın.
const PARTI_SURESI_MS = 55_000;
const ARDISIK_HATA_SINIR = 6;
const YAZMA_ESIGI = 8; // kaç bölge birikince veritabanına yazılsın
const KUYRUK_SIKISTIRMA = 200; // işlenmiş baş bu kadar büyüyünce kuyruk diske sıkıştırılır
// Emlakjet 429/503 verdiğinde bu bir arıza değil "yavaşla" demektir:
// bekleme kalıcı olarak artırılır, uzun başarı serisinde tabana doğru geri iner.
const YAVASLAMA_KATSAYI = 1.5;
const AZAMI_BEKLEME_MS = 15_000;
const HIZLANMA_ESIGI = 30;     // bu kadar ardışık başarıdan sonra bir kademe hızlan
const LIMIT_HATA_SINIR = 12;   // hız sınırına sıradan hatadan daha toleranslıyız

const VARSAYILAN = {
  aktif: false,
  duraklat: false,
  neden: null,
  tipler: ["konut", "arsa"],
  // ilce → mahalle özetleri de düşer; mahalle → her mahalleye tek tek girilir
  derinlik: "ilce",
  trendKaydet: true,
  beklemeMs: 1500,
  tabanBekleme: 1500,   // kullanıcının seçtiği hız; yavaşlama sonrası buraya döner
  limitHata: 0,         // 429/503 sayacı
  basariDizisi: 0,
  kuyruk: [],
  tampon: [],
  islenen: 0,
  toplamHedef: 0,
  yazilanBolge: 0,
  yazilanSatir: 0,
  ardisikHata: 0,
  sonHata: null,
  sonBolge: null,
  baslama: null,
  sonGuncelleme: null,
};

let kosuyor = false;
// Döngü koşarken durum sahibi döngüdür: duraklat/durdur istekleri buraya
// bırakılır, döngü bölge sınırında uygular. Aksi halde komut ile döngü
// aynı anda duruma yazar ve biri diğerini geri alır.
let istek = null;

// ---------------------------------------------------------------- yardımcılar

const bekle = (ms) => new Promise((r) => setTimeout(r, ms));

// Kuyruk (mahalle derinliğinde on binlerce kayıt) küçük durum nesnesinden
// AYRI anahtarda tutulur: her bölgede tüm kuyruğu diske yazmak taramayı
// boğardı. Küçük durum her adımda, kuyruk yalnızca değiştiğinde yazılır;
// aradaki ilerleme kuyrukIndeks ile takip edilir.
async function durumOku() {
  const o = await chrome.storage.local.get({ ejGorev: null, ejKuyruk: [] });
  const d = o.ejGorev ? { ...VARSAYILAN, ...o.ejGorev } : { ...VARSAYILAN };
  // Eski sürümden devralınan durumda taban hız yoktur: o anki hızı taban say,
  // yoksa kullanıcının seçtiğinden hızlı koşmaya başlarız.
  if (!o.ejGorev?.tabanBekleme) d.tabanBekleme = d.beklemeMs;
  const bas = d.kuyrukIndeks || 0;
  d.kuyruk = Array.isArray(o.ejKuyruk) ? o.ejKuyruk.slice(bas) : [];
  d.kuyrukIndeks = bas;
  return d;
}

// Kuyruksuz özet (popup yoklaması on binlik diziyi kopyalamasın)
async function durumOzeti() {
  const o = await chrome.storage.local.get({ ejGorev: null });
  const d = o.ejGorev ? { ...VARSAYILAN, ...o.ejGorev } : { ...VARSAYILAN };
  delete d.kuyruk;
  return d;
}

async function durumYaz(d, kuyrukDa = false) {
  const { kuyruk, ...kucuk } = d;
  if (kuyrukDa) { d.kuyrukIndeks = 0; kucuk.kuyrukIndeks = 0; }
  kucuk.kuyrukAdet = (kuyruk || []).length;
  kucuk.sonGuncelleme = new Date().toISOString();
  d.sonGuncelleme = kucuk.sonGuncelleme;
  const yazilacak = { ejGorev: kucuk };
  if (kuyrukDa) yazilacak.ejKuyruk = kuyruk || [];
  await chrome.storage.local.set(yazilacak);
  rozetYaz(d);
  return d;
}

function rozetYaz(d) {
  const bekleyen = d.kuyruk?.length ?? d.kuyrukAdet ?? 0;
  const metin = d.aktif && !d.duraklat
    ? (bekleyen > 999 ? "999+" : String(bekleyen))
    : (d.duraklat ? "⏸" : "");
  // Rozet kozmetiktir: senkron da asenkron da hata taramayı durdurmasın
  try {
    Promise.resolve(chrome.action?.setBadgeText?.({ text: metin })).catch(() => {});
    Promise.resolve(chrome.action?.setBadgeBackgroundColor?.({
      color: d.duraklat ? "#8b2b2b" : "#0b3d2e",
    })).catch(() => {});
  } catch { /* rozet yoksa önemsiz */ }
}

// ---------------------------------------------------------------- Emlakjet ucu

async function bolgeGetir(g) {
  const c = new AbortController();
  const zamanAsimi = setTimeout(() => c.abort(), 25_000);
  try {
    const r = await fetch(apiUrl(g), {
      signal: c.signal,
      credentials: "omit",
      headers: { Accept: "application/json" },
    });
    if (!r.ok) {
      const e = new Error(`Emlakjet ${r.status}`);
      e.httpDurum = r.status;
      const ra = Number(r.headers.get("Retry-After"));
      e.tekrarSaniye = Number.isFinite(ra) && ra > 0 ? Math.min(600, ra) : null;
      throw e;
    }
    return await r.json();
  } finally {
    clearTimeout(zamanAsimi);
  }
}

// ---------------------------------------------------------------- tarama

function kokGorevler(tipler) {
  return tipler.filter((t) => TIPLER[t]).map((tip) => ({
    tip, seviye: "ulke", cityId: null, countyId: null, districtId: null,
    il: null, ilce: null, mahalle: null,
  }));
}

function gorevAnahtar(g) {
  return [g.tip, g.seviye, g.cityId || 0, g.countyId || 0, g.districtId || 0].join("|");
}

// Bir bölgeyi işler: kaydını üretir, alt bölgeleri kuyruğa ekler
async function bolgeIsle(g, d) {
  const j = await bolgeGetir(g);
  const geo = j.GeoInfo || {};
  const il = g.il || geo.City || null;
  const ilce = g.ilce || geo.County || null;
  const mahalle = g.mahalle || geo.District || geo.Quarter || null;
  // General bazı tip/seviye bileşimlerinde (ör. arsa-mahalle) hiç gelmez;
  // o durumda güncel ay aylık seriden okunur.
  const { donem: guncelAy, ...olcum } = guncelOlcum(j);
  const donem = guncelAy || new Date().toISOString().slice(0, 7);
  // 1/2/5 yıllık değişim yanıtta yok; sayfanın yaptığı gibi seriden hesaplanır.
  // (Seri her zaman çekilir; trendKaydet yalnızca SAKLANIP saklanmayacağını belirler.)
  const seri = trendSatirlari(j);
  const ozet = { ...olcum, ...donemDegisimleri(seri, donem) };
  const kimlik = {
    tip: g.tip, seviye: g.seviye, il, ilce, mahalle,
    ilSlug: slugla(il), ilceSlug: slugla(ilce), mahalleSlug: slugla(mahalle),
    cityId: g.cityId || null, countyId: g.countyId || null, districtId: g.districtId || null,
  };
  const cocuk = cocuklar(j, { ...g, il, ilce });

  const kayit = {
    ...kimlik,
    kaynak: kaynakUrl(kimlik),
    donem,
    toplanmaZamani: new Date().toISOString(),
    ozet,
    dagilim: dagilimSatirlari(j),
    trend: d.trendKaydet ? seri : [],
    // Alt bölgelerin güncel özetleri: ilçeye girmeden tüm mahalle
    // fiyatlarını da yakalar (Emlakjet tabloyu bu veriden çizer).
    cocukOzetleri: cocuk.map((c) => ({
      seviye: c.seviye, il: c.il, ilce: c.ilce, mahalle: c.mahalle,
      ilSlug: slugla(c.il), ilceSlug: slugla(c.ilce), mahalleSlug: slugla(c.mahalle),
      cityId: c.cityId, countyId: c.countyId, districtId: c.districtId,
      donem: c.ay || donem,
      olcumKaynagi: "Static",
      ...c.ozet,
    })),
  };

  // Derinlik sınırına kadar in
  const hedefDerinlik = SEVIYE_SIRA.indexOf(d.derinlik);
  const yeniGorevler = cocuk
    .filter((c) => SEVIYE_SIRA.indexOf(c.seviye) <= hedefDerinlik)
    .map((c) => ({
      tip: c.tip, seviye: c.seviye, cityId: c.cityId, countyId: c.countyId,
      districtId: c.districtId, il: c.il, ilce: c.ilce, mahalle: c.mahalle,
    }));

  return { kayit, yeniGorevler };
}

async function tamponuBosalt(d, zorla = false) {
  if (!d.tampon.length) return d;
  if (!zorla && d.tampon.length < YAZMA_ESIGI) return d;
  const sonuc = await DB.bolgeleriYaz(d.tampon);
  d.yazilanBolge += sonuc.bolge || 0;
  d.yazilanSatir += sonuc.satir || 0;
  d.tampon = [];
  return d;
}

// Bekleyen duraklat/durdur isteğini uygular; uyguladıysa true döner.
async function istegiUygula(d) {
  if (!istek) return false;
  const i = istek;
  istek = null;
  if (i.tur === "durdur") {
    try { await tamponuBosalt(d, true); } catch { /* tampon durumda saklı kalır */ }
    d.aktif = false;
    d.duraklat = false;
    d.neden = "Kullanıcı durdurdu";
  } else {
    d.duraklat = true;
    d.neden = i.neden || "Kullanıcı duraklattı";
  }
  await durumYaz(d, true);
  return true;
}

async function surdur() {
  if (kosuyor) return;
  kosuyor = true;
  const bitis = Date.now() + PARTI_SURESI_MS;
  try {
    let d = await durumOku();
    while (d.aktif && !d.duraklat && Date.now() < bitis) {
      if (!d.kuyruk.length) {
        d = await tamponuBosalt(d, true);
        d.aktif = false;
        d.neden = "Tarama tamamlandı";
        await durumYaz(d);
        break;
      }
      const g = d.kuyruk[0];
      try {
        const { kayit, yeniGorevler } = await bolgeIsle(g, d);
        d.kuyruk.shift();
        d.kuyrukIndeks += 1;
        d.tampon.push(kayit);
        d.islenen += 1;
        d.ardisikHata = 0;
        d.sonHata = null;
        d.basariDizisi = (d.basariDizisi || 0) + 1;
        const taban = d.tabanBekleme || d.beklemeMs;
        if (d.basariDizisi >= HIZLANMA_ESIGI && d.beklemeMs > taban) {
          d.beklemeMs = Math.max(taban, Math.round(d.beklemeMs / YAVASLAMA_KATSAYI));
          d.basariDizisi = 0;
          d.limitHata = 0;
          d.neden = `Sunucu toparladı; bekleme ${(d.beklemeMs / 1000).toFixed(1)} sn'ye indirildi.`;
        }
        const yer = [kayit.il, kayit.ilce, kayit.mahalle].filter(Boolean).join(" / ") || "Türkiye";
        d.sonBolge = `${yer} (${g.tip})`;

        let kuyrukDegisti = false;
        if (yeniGorevler.length) {
          const bilinen = new Set(d.kuyruk.map(gorevAnahtar));
          for (const y of yeniGorevler) {
            const a = gorevAnahtar(y);
            if (!bilinen.has(a)) { d.kuyruk.push(y); bilinen.add(a); kuyrukDegisti = true; }
          }
          d.toplamHedef = d.islenen + d.kuyruk.length;
        }

        d = await tamponuBosalt(d);
        // Kuyruk büyüdüyse ya da işlenmiş baş çok uzadıysa diske yaz
        await durumYaz(d, kuyrukDegisti || d.kuyrukIndeks >= KUYRUK_SIKISTIRMA);
      } catch (e) {
        const mesaj = String(e?.message || e).slice(0, 200);
        d.sonHata = mesaj;
        d.basariDizisi = 0;

        // 429/503: kalıcı arıza değil, "yavaşla" sinyali.
        // Bölge kuyrukta kalır, bekleme artırılır, aynı bölge yeniden denenir.
        if (e?.httpDurum === 429 || e?.httpDurum === 503) {
          d.limitHata = (d.limitHata || 0) + 1;
          d.ardisikHata = 0;
          const oncekiBekleme = d.beklemeMs;
          d.beklemeMs = Math.min(AZAMI_BEKLEME_MS, Math.round(d.beklemeMs * YAVASLAMA_KATSAYI));
          if (d.limitHata >= LIMIT_HATA_SINIR) {
            d.duraklat = true;
            d.neden = `Emlakjet ${e.httpDurum} vermeyi sürdürüyor (${d.limitHata} kez). `
              + "Bir süre bekleyip “Devam et” deyin; kuyruk duruyor.";
            await durumYaz(d);
            break;
          }
          d.neden = `Emlakjet ${e.httpDurum} (hız sınırı) — bekleme `
            + `${(oncekiBekleme / 1000).toFixed(1)} → ${(d.beklemeMs / 1000).toFixed(1)} sn. Tarama sürüyor.`;
          await durumYaz(d);
          const duraklama = Math.max(
            (e.tekrarSaniye || 0) * 1000,
            Math.min(60_000, d.beklemeMs * 2 ** Math.min(4, d.limitHata)),
          );
          await bekle(duraklama);
          continue;
        }

        d.ardisikHata += 1;
        if (d.ardisikHata >= ARDISIK_HATA_SINIR) {
          // Kuyruk ve tampon yerinde kalır; "Devam et" ile kaldığı yerden sürer.
          d.duraklat = true;
          d.neden = `Üst üste ${d.ardisikHata} hata: ${mesaj}. Bağlantı ya da erişim engeli olabilir.`;
          await durumYaz(d);
          break;
        }
        await durumYaz(d);
        await bekle(Math.min(30_000, d.beklemeMs * (2 ** d.ardisikHata)));
        continue;
      }
      if (await istegiUygula(d)) break;
      await bekle(d.beklemeMs);
      if (await istegiUygula(d)) break;
    }
  } catch (e) {
    const d = await durumOku();
    d.duraklat = true;
    d.sonHata = String(e?.message || e).slice(0, 200);
    d.neden = "Beklenmeyen hata; kuyruk korundu, 'Devam et' ile sürdürebilirsiniz.";
    await durumYaz(d);
  } finally {
    kosuyor = false;
  }
}

async function alarmKur() {
  const mevcut = await chrome.alarms.get(ALARM);
  if (!mevcut) await chrome.alarms.create(ALARM, { periodInMinutes: 1, delayInMinutes: 0.1 });
}

// ---------------------------------------------------------------- komutlar

async function baslat(ayar = {}) {
  const onceki = await durumOku();
  if (onceki.aktif && !onceki.duraklat && onceki.kuyruk.length) {
    return { ok: false, hata: "Zaten çalışan bir tarama var. Önce durdurun." };
  }
  const izin = await chrome.storage.sync.get({ yaziliIzin: false });
  if (izin.yaziliIzin !== true) {
    return { ok: false, hata: "Otomatik tarama için popup'taki yazılı izin onayı gerekli." };
  }
  const tipler = (Array.isArray(ayar.tipler) && ayar.tipler.length ? ayar.tipler : ["konut", "arsa"])
    .filter((t) => TIPLER[t]);
  if (!tipler.length) return { ok: false, hata: "En az bir tip (konut/arsa) seçin." };
  const derinlik = SEVIYE_SIRA.includes(ayar.derinlik) ? ayar.derinlik : "ilce";
  const kuyruk = kokGorevler(tipler);
  const d = {
    ...VARSAYILAN,
    aktif: true,
    tipler,
    derinlik,
    trendKaydet: ayar.trendKaydet !== false,
    beklemeMs: 0, // hemen altında taban ile birlikte atanır
    kuyruk,
    toplamHedef: kuyruk.length,
    baslama: new Date().toISOString(),
  };
  d.tabanBekleme = Math.min(20_000, Math.max(800, Number(ayar.beklemeMs) || 1500));
  d.beklemeMs = d.tabanBekleme;
  await durumYaz(d, true);
  await alarmKur();
  surdur();
  return { ok: true, durum: d };
}

async function duraklat(neden = "Kullanıcı duraklattı") {
  if (kosuyor) { istek = { tur: "duraklat", neden }; return { ok: true, durum: await durumOzeti() }; }
  const d = await durumOku();
  d.duraklat = true;
  d.neden = neden;
  await durumYaz(d);
  return { ok: true, durum: d };
}

async function devam() {
  istek = null;
  const d = await durumOku();
  if (!d.kuyruk.length && !d.tampon.length) return { ok: false, hata: "Devam edecek kuyruk yok." };
  d.duraklat = false;
  d.aktif = true;
  d.neden = null;
  d.ardisikHata = 0;
  // Sayaç sıfırlanır ama artırılmış bekleme korunur: aynı duvara
  // aynı hızla koşmanın anlamı yok.
  d.limitHata = 0;
  d.basariDizisi = 0;
  await durumYaz(d, true);
  await alarmKur();
  surdur();
  return { ok: true, durum: d };
}

async function durdur() {
  if (kosuyor) { istek = { tur: "durdur" }; return { ok: true, durum: await durumOzeti() }; }
  const d = await durumOku();
  try { await tamponuBosalt(d, true); } catch { /* elde kalan tampon durumda saklı */ }
  d.aktif = false;
  d.duraklat = false;
  d.neden = "Kullanıcı durdurdu";
  await durumYaz(d, true);
  return { ok: true, durum: d };
}

async function sifirla() {
  await chrome.storage.local.remove(["ejGorev", "ejKuyruk"]);
  rozetYaz({ ...VARSAYILAN, kuyruk: [] });
  return { ok: true, durum: { ...VARSAYILAN } };
}

// ---------------------------------------------------------------- olaylar

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM) surdur();
});

chrome.runtime.onStartup.addListener(async () => {
  await alarmKur();
  surdur(); // tarayıcı yeniden açıldı: kaldığı yerden
});

chrome.runtime.onInstalled.addListener(async () => {
  await alarmKur();
  surdur();
});

chrome.runtime.onMessage.addListener((msg, _s, yanit) => {
  const komutlar = {
    EJ_BASLAT: () => baslat(msg.ayar),
    EJ_DURAKLAT: () => duraklat(),
    EJ_DEVAM: () => devam(),
    EJ_DURDUR: () => durdur(),
    EJ_SIFIRLA: () => sifirla(),
    EJ_PING: async () => ({ ok: true, surum: chrome.runtime.getManifest().version }),
    EJ_DURUM: async () => ({ ok: true, durum: await durumOzeti() }),
    EJ_OZET: async () => ({ ok: true, ozet: await DB.ozet() }),
    EJ_ARA: async () => ({ ok: true, ...(await DB.ara(msg.sorgu || {})) }),
    EJ_VERI_SIL: async () => { await DB.temizle(); return { ok: true }; },
  };
  const calistir = komutlar[msg?.type];
  if (!calistir) return;
  Promise.resolve()
    .then(calistir)
    .then(yanit)
    .catch((e) => yanit({ ok: false, hata: String(e?.message || e) }));
  return true;
});

// Servis her uyandığında (mesaj, alarm, tarayıcı açılışı) kuyruğa bak
alarmKur().then(() => surdur());
