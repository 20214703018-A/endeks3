#!/usr/bin/env node
/**
 * GEOPROP - BİRLEŞİK GAYRİMENKUL TOPLAYICI (Hepsiemlak & Emlakjet)
 * =============================================================
 * Mahalle mahalle en ayrıntılı konum bilgileriyle (İl, İlçe, Mahalle,
 * Mahalle ID, Harita GPS Pini, Ada/Parsel) Ev, Arsa ve Dükkan/İşyeri
 * ilanlarını toplayan, Cloudflare korumalarını şeffaf aşan ve
 * kesintiye dayanıklı SQLite veritabanına kaydeden toplayıcı motor.
 *
 * Kullanım:
 *   node gayrimenkul_toplayici.mjs --il istanbul --ilce kadikoy --kategori konut,arsa,isyeri --max-sayfa 2
 *   node gayrimenkul_toplayici.mjs --il ankara --kategori arsa --max-sayfa 3 --export-csv
 *   node gayrimenkul_toplayici.mjs --il hepsi --kategori hepsi
 */

import fs from 'fs';
import path from 'path';
import http from 'http';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { DatabaseSync } from 'node:sqlite';
import puppeteer from 'puppeteer-core';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_DIR = __dirname;
const DATA_DIR = path.join(BASE_DIR, 'data');
const DB_PATH = path.join(DATA_DIR, 'turkiye_tum_ilanlar.sqlite');
const CSV_DIR = path.join(DATA_DIR, 'csv_ciktilari');

function resolveFilePath(filename) {
  const candidates = [
    path.join(DATA_DIR, filename),
    path.join(BASE_DIR, 'collector', filename),
    path.join(BASE_DIR, 'collector', 'data', filename),
    path.join(BASE_DIR, filename)
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return path.join(DATA_DIR, filename);
}

const GUIDE_PATH = resolveFilePath('turkiye_il_ilce_rehberi.json');
const CENTROIDS_PATH = resolveFilePath('mahalle_koordinatlari.json');

// Terminal Renkleri
const c = {
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  red: '\x1b[31m',
  magenta: '\x1b[35m',
  bold: '\x1b[1m',
  reset: '\x1b[0m'
};

function log(msg, level = 'INFO') {
  const t = new Date().toLocaleTimeString('tr-TR', { hour12: false });
  const map = {
    INFO: `${c.cyan}[INFO]${c.reset}`,
    SUCCESS: `${c.green}[BAŞARILI]${c.reset}`,
    WARN: `${c.yellow}[UYARI]${c.reset}`,
    ERROR: `${c.red}[HATA]${c.reset}`,
    LOCATION: `${c.magenta}[KONUM]${c.reset}`
  };
  console.log(`[${t}] ${map[level] || `[${level}]`} ${msg}`);
}

// Türkçe Karakter Temizleme (Slug)
export function trSlug(str) {
  if (!str) return '';
  return str.toString()
    .replace(/İ/g, 'i').replace(/I/g, 'i').replace(/ı/g, 'i')
    .replace(/Ğ/g, 'g').replace(/ğ/g, 'g')
    .replace(/Ü/g, 'u').replace(/ü/g, 'u')
    .replace(/Ş/g, 's').replace(/ş/g, 's')
    .replace(/Ö/g, 'o').replace(/ö/g, 'o')
    .replace(/Ç/g, 'c').replace(/ç/g, 'c')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

// Ada, Parsel ve İmar Ayrıştırma Motoru
export function extractAdaParsel(text) {
  if (!text) return { ada: null, parsel: null, imar: null, tapu: null, kaks: null };
  const str = text.toLowerCase();
  let ada = null;
  let parsel = null;
  let imar = null;
  let tapu = null;
  let kaks = null;

  // 1. Örüntü: 123/45 parsel
  const slashMatch = str.match(/\b(\d{1,6})\s*\/\s*(\d{1,6})\s*(?:ada[\s\/]*)?parsel\b/);
  if (slashMatch) {
    ada = slashMatch[1];
    parsel = slashMatch[2];
  } else {
    // 2. Örüntü: 123 ada 45 parsel
    const comboMatch = str.match(/\b(\d{1,6})\s*ada\s*[,/-]?\s*(\d{1,6})\s*parsel\b/);
    if (comboMatch) {
      ada = comboMatch[1];
      parsel = comboMatch[2];
    } else {
      // 3. Ayrı Ada: ada: 123 veya ada no 123
      const adaMatch = str.match(/\bada\s*(?:no)?[:#\s]+(\d{1,6})\b/);
      if (adaMatch) ada = adaMatch[1];

      // 4. Ayrı Parsel: parsel: 45 veya parsel no 45
      const parselMatch = str.match(/\bparsel\s*(?:no)?[:#\s]+(\d{1,6})\b/);
      if (parselMatch) parsel = parselMatch[1];
    }
  }

  // İmar Durumu Tahmini
  if (str.includes('konut imar') || str.includes('villa imar')) imar = 'Konut İmarlı';
  else if (str.includes('ticari imar') || str.includes('ticaret imar')) imar = 'Ticari İmarlı';
  else if (str.includes('sanayi imar')) imar = 'Sanayi İmarlı';
  else if (str.includes('turizm imar')) imar = 'Turizm İmarlı';
  else if (str.includes('tarla')) imar = 'Tarla';
  else if (str.includes('zeytinlik')) imar = 'Zeytinlik';
  else if (str.includes('bağ') || str.includes('bahçe') || str.includes('bag') || str.includes('bahce')) imar = 'Bağ & Bahçe';
  else if (str.includes('imarli') || str.includes('imarlı')) imar = 'İmarlı';

  // Tapu Durumu
  if (str.includes('mustakil tapu') || str.includes('müstakil tapu') || str.includes('tek tapu')) tapu = 'Müstakil Parsel';
  else if (str.includes('hisseli tapu') || str.includes('hisseli')) tapu = 'Hisseli Tapu';
  else if (str.includes('tahsisli')) tapu = 'Tahsisli';

  // KAKS / Emsal
  const kaksMatch = str.match(/\b(?:kaks|emsal)\s*[:=]?\s*([0-9.,]+)\b/);
  if (kaksMatch) kaks = kaksMatch[1];

  return { ada, parsel, imar, tapu, kaks };
}

// 51.171 Resmi Mahalle Rehberini Yükle
let centroidsCache = null;
function loadCentroids() {
  if (centroidsCache) return centroidsCache;
  if (!fs.existsSync(CENTROIDS_PATH)) {
    log(`Mahalle koordinat dosyası bulunamadı: ${CENTROIDS_PATH}`, 'WARN');
    return {};
  }
  try {
    const raw = JSON.parse(fs.readFileSync(CENTROIDS_PATH, 'utf8'));
    centroidsCache = raw;
    log(`🗺️  51.171 Resmi Mahalle Koordinat Veritabanı Belleğe Alındı`, 'SUCCESS');
    return centroidsCache;
  } catch (err) {
    log(`Koordinat rehberi okuma hatası: ${err.message}`, 'ERROR');
    return {};
  }
}

// İlan Konumunu Resmi Mahalle Verisiyle Eşleştir
function matchNeighborhood(il, ilce, mahalle, centroids) {
  if (!mahalle || !centroids) return null;
  const mClean = mahalle.replace(/\s+(mahallesi|mah\.|mah)$/i, '').trim();
  const ilSlug = trSlug(il);
  const ilceSlug = trSlug(ilce);
  const mahSlug = trSlug(mClean);

  let key = `${ilSlug}_${ilceSlug}_${mahSlug}`;
  let match = centroids[key];
  if (!match) {
    key = `${ilSlug}_${ilceSlug}_${mahSlug}koyu`;
    match = centroids[key];
  }
  return match || null;
}

// SQLite Veritabanını Başlat
function initDatabase() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const db = new DatabaseSync(DB_PATH);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA synchronous = NORMAL;');

  db.exec(`
    CREATE TABLE IF NOT EXISTS ilanlar (
      ilan_id TEXT PRIMARY KEY,
      kaynak TEXT,
      kategori TEXT,
      alt_tip TEXT,
      baslik TEXT,
      il TEXT,
      ilce TEXT,
      mahalle TEXT,
      mahalle_id INTEGER,
      resmi_mahalle_id TEXT,
      ada TEXT,
      parsel TEXT,
      imar_durumu TEXT,
      tapu_durumu TEXT,
      kaks_emsal TEXT,
      fiyat_tl INTEGER,
      para_birimi TEXT DEFAULT 'TL',
      m2_brut REAL,
      m2_net REAL,
      birim_fiyat REAL,
      oda_sayisi TEXT,
      bina_yasi TEXT,
      kat TEXT,
      lat REAL,
      lon REAL,
      koordinat_tipi TEXT,
      satici_turu TEXT,
      satici_adi TEXT,
      satici_telefon TEXT,
      tarih TEXT,
      gorsel_url TEXT,
      url TEXT,
      eklenme_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tarama_kuyrugu (
      anahtar TEXT PRIMARY KEY,
      kaynak TEXT,
      il_adi TEXT,
      ilce_adi TEXT,
      mahalle_adi TEXT,
      kategori TEXT,
      son_sayfa INTEGER DEFAULT 0,
      toplam_ilan INTEGER DEFAULT 0,
      durum TEXT DEFAULT 'bekliyor',
      guncellenme TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_ilan_il_ilce ON ilanlar(il, ilce);
    CREATE INDEX IF NOT EXISTS idx_ilan_mahalle ON ilanlar(mahalle);
    CREATE INDEX IF NOT EXISTS idx_ilan_kat ON ilanlar(kategori);
    CREATE INDEX IF NOT EXISTS idx_ilan_kaynak ON ilanlar(kaynak);
    CREATE INDEX IF NOT EXISTS idx_ilan_resmi_mah ON ilanlar(resmi_mahalle_id);
    CREATE INDEX IF NOT EXISTS idx_ilan_ada_parsel ON ilanlar(ada, parsel);
  `);

  // Kolon göç kontrolü (migration)
  const cols = db.prepare("PRAGMA table_info(ilanlar)").all().map(r => r.name);
  const extraCols = {
    resmi_mahalle_id: 'TEXT',
    ada: 'TEXT',
    parsel: 'TEXT',
    imar_durumu: 'TEXT',
    tapu_durumu: 'TEXT',
    kaks_emsal: 'TEXT',
    koordinat_tipi: 'TEXT'
  };
  for (const [col, colType] of Object.entries(extraCols)) {
    if (!cols.includes(col)) {
      db.exec(`ALTER TABLE ilanlar ADD COLUMN ${col} ${colType};`);
    }
  }

  return db;
}

// İlanları Veritabanına Toplu Yaz
function saveListingsToDb(db, items, centroids) {
  if (!items || items.length === 0) return 0;

  const insertStmt = db.prepare(`
    INSERT INTO ilanlar (
      ilan_id, kaynak, kategori, alt_tip, baslik, il, ilce, mahalle, mahalle_id,
      resmi_mahalle_id, ada, parsel, imar_durumu, tapu_durumu, kaks_emsal,
      fiyat_tl, para_birimi, m2_brut, m2_net, birim_fiyat,
      oda_sayisi, bina_yasi, kat, lat, lon, koordinat_tipi, satici_turu, satici_adi, satici_telefon,
      tarih, gorsel_url, url, eklenme_tarihi
    ) VALUES (
      ?, ?, ?, ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?
    )
    ON CONFLICT(ilan_id) DO UPDATE SET
      fiyat_tl = excluded.fiyat_tl,
      birim_fiyat = excluded.birim_fiyat,
      tarih = excluded.tarih,
      lat = COALESCE(excluded.lat, ilanlar.lat),
      lon = COALESCE(excluded.lon, ilanlar.lon),
      koordinat_tipi = COALESCE(excluded.koordinat_tipi, ilanlar.koordinat_tipi),
      resmi_mahalle_id = COALESCE(excluded.resmi_mahalle_id, ilanlar.resmi_mahalle_id),
      ada = COALESCE(excluded.ada, ilanlar.ada),
      parsel = COALESCE(excluded.parsel, ilanlar.parsel),
      imar_durumu = COALESCE(excluded.imar_durumu, ilanlar.imar_durumu),
      tapu_durumu = COALESCE(excluded.tapu_durumu, ilanlar.tapu_durumu),
      kaks_emsal = COALESCE(excluded.kaks_emsal, ilanlar.kaks_emsal);
  `);

  let savedCount = 0;
  const now = new Date().toISOString().replace('T', ' ').slice(0, 19);

  for (const it of items) {
    let lat = it.lat;
    let lon = it.lon;
    let resmiMahId = null;
    let coordType = (lat && lon) ? 'KESIN_PIN' : null;

    // Koordinat ve Mahalle ID Zenginleştirme
    const geo = matchNeighborhood(it.il, it.ilce, it.mahalle, centroids);
    if (geo) {
      resmiMahId = geo.id ? String(geo.id) : null;
      if (!lat || !lon) {
        lat = geo.lat;
        lon = geo.lon;
        coordType = 'MAHALLE_MERKEZI';
      }
    }

    try {
      insertStmt.run(
        String(it.ilan_id),
        String(it.kaynak || 'hepsiemlak'),
        String(it.kategori || ''),
        String(it.alt_tip || ''),
        String(it.baslik || ''),
        String(it.il || ''),
        String(it.ilce || ''),
        String(it.mahalle || ''),
        it.mahalle_id ? parseInt(it.mahalle_id) : null,
        resmiMahId ? String(resmiMahId) : null,
        it.ada ? String(it.ada) : null,
        it.parsel ? String(it.parsel) : null,
        it.imar_durumu ? String(it.imar_durumu) : null,
        it.tapu_durumu ? String(it.tapu_durumu) : null,
        it.kaks_emsal ? String(it.kaks_emsal) : null,
        parseInt(it.fiyat_tl) || 0,
        String(it.para_birimi || 'TL'),
        parseFloat(it.m2_brut) || 0,
        parseFloat(it.m2_net) || 0,
        parseFloat(it.birim_fiyat) || 0,
        String(it.oda_sayisi || ''),
        String(it.bina_yasi || ''),
        String(it.kat || ''),
        (lat !== null && lat !== undefined && !isNaN(lat)) ? parseFloat(lat) : null,
        (lon !== null && lon !== undefined && !isNaN(lon)) ? parseFloat(lon) : null,
        coordType,
        String(it.satici_turu || ''),
        String(it.satici_adi || ''),
        String(it.satici_telefon || ''),
        String(it.tarih || ''),
        String(it.gorsel_url || ''),
        String(it.url || ''),
        now
      );
      savedCount++;
    } catch (e) {
      log(`İlan kayıt uyarısı (${it.ilan_id}): ${e.message}`, 'WARN');
    }
  }

  return savedCount;
}

// Chrome Bağlantısı / Otomatik Başlatma (CDP & Headless CI Desteği)
let isCdpConnected = false;
async function getBrowser() {
  const checkUrl = 'http://localhost:9222/json/version';
  const isAvailable = await new Promise((resolve) => {
    http.get(checkUrl, (res) => {
      resolve(res.statusCode === 200);
    }).on('error', () => resolve(false));
  });

  if (isAvailable) {
    log(`🔗 Chrome CDP Oturumu Aktif (Port: 9222)`, 'SUCCESS');
    isCdpConnected = true;
    return await puppeteer.connect({ browserURL: 'http://localhost:9222' });
  }

  log(`🚀 Chrome Başlatılıyor (CI / Headless Mod)...`, 'INFO');
  const chromePaths = [
    process.env.CHROME_BIN,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  ].filter(Boolean);

  const bin = chromePaths.find(p => fs.existsSync(p));
  if (!bin) {
    throw new Error('Google Chrome binary bulunamadı!');
  }

  isCdpConnected = false;
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: bin,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      '--window-size=1920,1080',
      '--lang=tr-TR,tr'
    ]
  });
  log(`✅ Chrome başarıyla başlatıldı ve bağlandı!`, 'SUCCESS');
  return browser;
}

// Hepsiemlak Sayfasını Çekme ve Ayrıştırma (Nuxt.js Madencisi)
async function scrapeHepsiemlakPage(page, url, targetCategory, defaultCity = '', defaultCounty = '') {
  try {
    const res = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 35000 });
    
    // Cloudflare WAF veya JS challenge varsa tamamlanmasını bekle
    let title = await page.title();
    if (title.includes('Just a moment') || title.includes('Bir dakika') || (res && res.status() === 429)) {
      log(`Cloudflare doğrulaması bekleniyor...`, 'WARN');
      await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => {});
      await new Promise(r => setTimeout(r, 2000));
      title = await page.title();
    }

    // Nuxt veri nesnesinden listings çıkart
    const pageData = await page.evaluate(() => {
      const nuxt = window.__NUXT__?.data?.[0];
      if (!nuxt) return null;

      const totalAds = nuxt.totalAdvertisement || 0;
      const totalPages = nuxt.totalPage || 1;
      const rawList = nuxt.list || [];

      const parsedList = rawList.map(item => {
        const floorStr = typeof item.floor === 'object' ? (item.floor?.name || '') : (item.floor ? String(item.floor) : '');
        const ageStr = typeof item.age === 'object' ? (item.age?.name || '') : (item.age !== null && item.age !== undefined ? `${item.age} Yaşında` : '');
        const roomStr = Array.isArray(item.roomAndLivingRoom) ? (item.roomAndLivingRoom[0] || '') : (item.roomAndLivingRoom ? String(item.roomAndLivingRoom) : '');
        const subCatStr = typeof item.subCategory === 'object' ? (item.subCategory?.typeName || item.subCategory?.name || '') : (item.subCategory ? String(item.subCategory) : '');
        const sellerTypeStr = typeof item.sellerType === 'object' ? (item.sellerType?.name || '') : (item.sellerType ? String(item.sellerType) : (item.advertiseOwner || ''));

        return {
          id: item.listingId || item.id,
          title: item.title || '',
          desc: item.detailDescription || '',
          price: item.price || 0,
          currency: item.currency || 'TL',
          city: item.city?.name || '',
          county: item.county?.name || '',
          district: item.district?.name || '',
          districtId: item.district?.id || null,
          mainCategory: item.mainCategory?.name || '',
          subCategory: subCatStr,
          grossSqm: item.sqm?.grossSqm?.[0] || 0,
          netSqm: item.sqm?.netSqm || 0,
          sqmPrice: item.sqm?.price || 0,
          room: roomStr,
          age: ageStr,
          floor: floorStr,
          lat: item.mapLocation?.lat || null,
          lon: item.mapLocation?.lon || null,
          sellerType: sellerTypeStr,
          firmName: item.firm?.name || item.owner?.name || '',
          phone: item.whatsAppNumber?.phoneNumber || item.owner?.phones?.[0]?.phoneNumber || item.firm?.firmUser?.phones?.[0]?.phoneNumber || '',
          date: item.createDate || item.listingUpdatedDate || '',
          detailUrl: item.detailUrl || '',
          imageUrl: item.imageUrl || ''
        };
      });

      return { totalAds, totalPages, parsedList };
    });

    if (!pageData || !pageData.parsedList) {
      return { totalAds: 0, totalPages: 0, items: [] };
    }

    const items = [];
    for (const raw of pageData.parsedList) {
      const { ada, parsel, imar, tapu, kaks } = extractAdaParsel(`${raw.title} ${raw.desc}`);
      const m2Brut = parseFloat(raw.grossSqm) || 0;
      const m2Net = parseFloat(raw.netSqm) || 0;
      const fiyat = parseInt(raw.price) || 0;
      let birimFiyat = parseFloat(raw.sqmPrice) || 0;
      if (!birimFiyat && m2Brut > 0 && fiyat > 0) {
        birimFiyat = Math.round((fiyat / m2Brut) * 100) / 100;
      }

      items.push({
        ilan_id: `he_${raw.id}`,
        kaynak: 'hepsiemlak',
        kategori: targetCategory,
        alt_tip: raw.subCategory || raw.mainCategory || targetCategory,
        baslik: raw.title || '',
        il: raw.city || defaultCity,
        ilce: raw.county || defaultCounty,
        mahalle: raw.district || '',
        mahalle_id: raw.districtId,
        ada,
        parsel,
        imar_durumu: imar || (targetCategory === 'arsa' ? raw.subCategory : null),
        tapu_durumu: tapu,
        kaks_emsal: kaks,
        fiyat_tl: fiyat,
        para_birimi: raw.currency || 'TL',
        m2_brut: m2Brut,
        m2_net: m2Net,
        birim_fiyat: birimFiyat,
        oda_sayisi: raw.room || '',
        bina_yasi: raw.age || '',
        kat: raw.floor || '',
        lat: raw.lat,
        lon: raw.lon,
        satici_turu: raw.sellerType || '',
        satici_adi: raw.firmName || '',
        satici_telefon: raw.phone || '',
        tarih: raw.date ? raw.date.slice(0, 10) : '',
        gorsel_url: raw.imageUrl ? (raw.imageUrl.startsWith('http') ? raw.imageUrl : `https://hepsiemlak.com/${raw.imageUrl}`) : '',
        url: raw.detailUrl ? (raw.detailUrl.startsWith('http') ? raw.detailUrl : `https://www.hepsiemlak.com/${raw.detailUrl}`) : ''
      });
    }

    return {
      totalAds: pageData.totalAds,
      totalPages: pageData.totalPages,
      items
    };
  } catch (err) {
    log(`Sayfa çekme hatası (${url}): ${err.message}`, 'WARN');
    return { totalAds: 0, totalPages: 0, items: [] };
  }
}

// Emlakjet Arsa Detay Madencisi (Harita GPS Pini, Ada, Parsel, İmar Durumu)
async function fetchEmlakjetArsaDetail(url) {
  if (!url) return {};
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
      },
      signal: AbortSignal.timeout(6000)
    });
    if (!res.ok) return {};
    const html = await res.text();

    const coordM = html.match(/coordinates[^\d]+([0-9]{2}\.[0-9]+)[^\d]+([0-9]{2}\.[0-9]+)/);
    const lat = coordM ? parseFloat(coordM[1]) : null;
    const lon = coordM ? parseFloat(coordM[2]) : null;

    const descM = html.match(/\"description\":\s*\"([^\"]+)\"/);
    const desc = descM ? descM[1] : '';

    const { ada, parsel, imar, tapu, kaks } = extractAdaParsel(`${desc}`);

    const imarDetailM = html.match(/\"İmar Durumu\"[^\"]*\"value\":\s*\"([^\"]+)\"/i);
    const tapuDetailM = html.match(/\"Tapu Durumu\"[^\"]*\"value\":\s*\"([^\"]+)\"/i);

    return {
      lat,
      lon,
      ada,
      parsel,
      imar_durumu: (imarDetailM ? imarDetailM[1] : null) || imar,
      tapu_durumu: (tapuDetailM ? tapuDetailM[1] : null) || tapu,
      kaks_emsal: kaks
    };
  } catch (e) {
    return {};
  }
}

// Emlakjet Sayfasını Çekme ve Ayrıştırma (JSON-LD Madencisi)
async function scrapeEmlakjetPage(page, url, targetCategory, defaultCity = '', defaultCounty = '') {
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 35000 });

    const rawList = await page.evaluate(() => {
      const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
      const found = [];
      for (const s of scripts) {
        try {
          const j = JSON.parse(s.innerText);
          const graph = j['@graph'] || [j];
          for (const item of graph) {
            if (item['@type'] === 'RealEstateListing') {
              found.push(item);
            }
          }
        } catch(e) {}
      }
      return found;
    });

    const items = [];
    for (const raw of rawList) {
      const urlStr = raw.url || '';
      const idMatch = urlStr.match(/-(\d+)$/);
      if (!idMatch) continue;
      const rawId = idMatch[1];

      const offers = raw.offers || {};
      const fiyat = parseInt(offers.price) || 0;
      const props = raw.additionalProperty || [];

      let oda = '';
      let m2Val = 0;
      let kat = '';
      let binaYasi = '';
      let mahalle = '';
      let ilce = defaultCounty;
      let il = defaultCity;
      for (const p of props) {
        const n = (p.name || '').toLowerCase();
        const v = String(p.value || '').trim();
        if (n === 'konum' || n === 'location') {
          const parts = v.split(',');
          if (parts.length >= 1) {
            mahalle = parts[0].trim();
          }
        } else if (n === 'roomcount' || n.includes('oda')) oda = v;
        else if (n.includes('metrekare') || n.includes('area') || n === 'grosssquaremeters') {
          const m = v.replace(/[^\d.]/g, '');
          if (m) m2Val = parseFloat(m);
        } else if (n === 'floor' || n.includes('kat')) kat = v;
        else if (n.includes('yaş') || n.includes('age')) binaYasi = v;
        else if (n === 'district') mahalle = v;
        else if (n === 'county' && !ilce) ilce = v;
        else if (n === 'city' && !il) il = v;
      }

      if (!mahalle && urlStr) {
        const mMah = urlStr.match(/\/([a-z0-9\-]+)-mahallesi/);
        if (mMah) mahalle = mMah[1].replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
      }

      if (!mahalle && raw.name) {
        const mTitle = raw.name.match(/([A-ZÇĞİÖŞÜa-zçğıöşü]+)\s*(?:Mahallesi|Mah\.|'de|'da)/);
        if (mTitle) mahalle = mTitle[1];
      }

      const { ada, parsel } = extractAdaParsel(`${raw.name || ''}`);
      let birimFiyat = (fiyat > 0 && m2Val > 0) ? Math.round((fiyat / m2Val) * 100) / 100 : 0;

      items.push({
        ilan_id: `ej_${rawId}`,
        kaynak: 'emlakjet',
        kategori: targetCategory,
        alt_tip: targetCategory,
        baslik: raw.name || '',
        il,
        ilce,
        mahalle,
        mahalle_id: null,
        ada,
        parsel,
        fiyat_tl: fiyat,
        para_birimi: 'TL',
        m2_brut: m2Val,
        m2_net: m2Val,
        birim_fiyat: birimFiyat,
        oda_sayisi: oda,
        bina_yasi: binaYasi,
        kat,
        lat: raw.geo?.latitude || null,
        lon: raw.geo?.longitude || null,
        satici_turu: '',
        satici_adi: '',
        satici_telefon: '',
        tarih: raw.datePosted ? raw.datePosted.slice(0, 10) : '',
        gorsel_url: raw.image || '',
        url: urlStr
      });
    }

    return { totalAds: items.length, totalPages: 1, items };
  } catch (err) {
    log(`Emlakjet sayfa hatası (${url}): ${err.message}`, 'WARN');
    return { totalAds: 0, totalPages: 0, items: [] };
  }
}

// CSV Dışa Aktarım Modülü (UTF-8 BOM ile Excel Uyumlu)
export function exportToCsv(db, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  log(`💾 CSV Dışa Aktarma Başlatılıyor...`, 'INFO');

  const categories = ['konut', 'arsa', 'isyeri'];
  const bom = '\uFEFF';

  for (const kat of categories) {
    const query = `
      SELECT 
        ilan_id, kaynak, kategori, alt_tip, baslik,
        il, ilce, mahalle, mahalle_id, resmi_mahalle_id,
        ada, parsel, imar_durumu, tapu_durumu, kaks_emsal,
        fiyat_tl, para_birimi, m2_brut, m2_net, birim_fiyat,
        oda_sayisi, bina_yasi, kat, lat, lon, koordinat_tipi,
        satici_turu, satici_adi, satici_telefon,
        tarih, url, gorsel_url, eklenme_tarihi
      FROM ilanlar
      WHERE kategori = ?
      ORDER BY il, ilce, mahalle, fiyat_tl DESC;
    `;

    const stmt = db.prepare(query);
    const rows = stmt.all(kat);

    if (rows.length === 0) continue;

    const headers = [
      'İlan ID', 'Portal', 'Kategori', 'Alt Tip', 'Başlık',
      'İl', 'İlçe', 'Mahalle', 'Portal Mahalle ID', 'Resmi TKGM Mahalle ID',
      'Ada', 'Parsel', 'İmar Durumu', 'Tapu Durumu', 'KAKS / Emsal',
      'Fiyat (TL)', 'Para Birimi', 'Brüt m²', 'Net m²', 'Birim Fiyat (TL/m²)',
      'Oda Sayısı', 'Bina Yaşı', 'Kat', 'Enlem (Lat)', 'Boylam (Lon)', 'Koordinat Hassasiyeti',
      'Satıcı Türü', 'Satıcı Adı', 'Satıcı Telefon',
      'İlan Tarihi', 'İlan URL', 'Görsel URL', 'Kayıt Tarihi'
    ];

    const escapeCsv = (val) => {
      if (val === null || val === undefined) return '""';
      const s = String(val).replace(/"/g, '""');
      return `"${s}"`;
    };

    const csvLines = [headers.join(';')];
    for (const r of rows) {
      const rowVals = [
        r.ilan_id, r.kaynak, r.kategori, r.alt_tip, r.baslik,
        r.il, r.ilce, r.mahalle, r.mahalle_id, r.resmi_mahalle_id,
        r.ada, r.parsel, r.imar_durumu, r.tapu_durumu, r.kaks_emsal,
        r.fiyat_tl, r.para_birimi, r.m2_brut, r.m2_net, r.birim_fiyat,
        r.oda_sayisi, r.bina_yasi, r.kat, r.lat, r.lon, r.koordinat_tipi,
        r.satici_turu, r.satici_adi, r.satici_telefon,
        r.tarih, r.url, r.gorsel_url, r.eklenme_tarihi
      ];
      csvLines.push(rowVals.map(escapeCsv).join(';'));
    }

    const outPath = path.join(outDir, `turkiye_${kat}_ayrintili.csv`);
    fs.writeFileSync(outPath, bom + csvLines.join('\r\n'), 'utf8');
    log(`✅ CSV Hazır: ${path.basename(outPath)} (${rows.length.toLocaleString('tr-TR')} satır, UTF-8 BOM)`, 'SUCCESS');
  }
}

// Ana Yürütücü Fonksiyon
async function main() {
  const args = process.argv.slice(2);
  const getArg = (name, def = null) => {
    const idx = args.indexOf(`--${name}`);
    if (idx !== -1 && args[idx + 1]) return args[idx + 1];
    return def;
  };
  const hasFlag = (name) => args.includes(`--${name}`);

  const targetIllerRaw = getArg('iller') || getArg('il', 'hepsi');
  const targetIlce = getArg('ilce', 'hepsi');
  const targetKat = getArg('kategori', 'hepsi');
  const targetKaynak = getArg('kaynak', 'hepsi'); // default: hepsi (hepsiemlak & emlakjet)
  const maxSayfa = getArg('max-sayfa') ? parseInt(getArg('max-sayfa')) : null;
  const hizBase = parseFloat(getArg('hiz', '1.5'));
  const exportCsvFlag = hasFlag('export-csv');
  const sadeceExport = hasFlag('sadece-export');

  const db = initDatabase();

  if (sadeceExport) {
    exportToCsv(db, CSV_DIR);
    process.exit(0);
  }

  const centroids = loadCentroids();

  if (!fs.existsSync(GUIDE_PATH)) {
    log(`İl-ilçe rehberi bulunamadı: ${GUIDE_PATH}`, 'ERROR');
    process.exit(1);
  }
  const guide = JSON.parse(fs.readFileSync(GUIDE_PATH, 'utf8'));

  // Hedef Kategori Listesi
  const validKats = ['konut', 'arsa', 'isyeri'];
  let kats = [];
  if (targetKat.toLowerCase() === 'hepsi') {
    kats = validKats;
  } else {
    kats = targetKat.split(',').map(s => s.trim().toLowerCase()).filter(s => validKats.includes(s));
  }
  if (kats.length === 0) kats = ['konut', 'arsa', 'isyeri'];

  // Hedef İller (Plaka veya İsim Eşleme - Örn: "1,2", "34", "istanbul")
  let targetCities = [];
  if (targetIllerRaw.toLowerCase() === 'hepsi') {
    targetCities = Object.keys(guide);
  } else {
    const tokens = targetIllerRaw.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    for (const token of tokens) {
      for (const [cid, cinfo] of Object.entries(guide)) {
        if (
          cid === token ||
          parseInt(cid) === parseInt(token) ||
          trSlug(cinfo.city_name) === trSlug(token) ||
          trSlug(cinfo.city_slug) === trSlug(token)
        ) {
          if (!targetCities.includes(cid)) {
            targetCities.push(cid);
          }
        }
      }
    }
  }
  if (targetCities.length === 0) {
    log(`Belirtilen il(ler) bulunamadı: ${targetIllerRaw}`, 'ERROR');
    process.exit(1);
  }

  // İş Listesi Oluştur
  const tasks = [];
  for (const cid of targetCities) {
    const cinfo = guide[cid];
    const cName = cinfo.city_name;
    const cSlug = cinfo.city_slug;
    const ilceler = cinfo.ilceler || [];

    for (const item of ilceler) {
      if (targetIlce.toLowerCase() !== 'hepsi' && trSlug(item.county_name) !== trSlug(targetIlce) && trSlug(item.county_slug) !== trSlug(targetIlce)) {
        continue;
      }
      for (const kat of kats) {
        tasks.push({
          cityId: cid,
          cityName: cName,
          citySlug: cSlug,
          countyId: item.county_id,
          countyName: item.county_name,
          countySlug: item.county_slug,
          category: kat
        });
      }
    }
  }

  log('='.repeat(70), 'INFO');
  log(`🚀 GEOPROP BİRLEŞİK GAYRİMENKUL TOPLAYICI (40 MAKİNE CI & YEREL)`, 'INFO');
  log(`🎯 Hedef İller: ${targetCities.length} (${targetCities.join(', ')}) | Toplam Görev: ${tasks.length}`, 'INFO');
  log(`📌 Kategoriler: ${kats.join(', ')} | Kaynaklar: ${targetKaynak.toUpperCase()}`, 'INFO');
  log(`📄 Sayfa Limiti: ${maxSayfa ? `${maxSayfa} Sayfa / İlçe` : 'Sınırsız (Tüm Sayfalar)'}`, 'INFO');
  log(`💾 Veritabanı: ${path.basename(DB_PATH)} (SQLite WAL Modu)`, 'INFO');
  log('='.repeat(70), 'INFO');

  // Mevcut İlan Adedi
  const countStmt = db.prepare('SELECT count(*) as cnt FROM ilanlar;');
  const prevCount = countStmt.get().cnt;
  log(`📊 Veritabanındaki Önceden Kayıtlı İlan Sayısı: ${prevCount.toLocaleString('tr-TR')}`, 'INFO');

  // Chrome'a Bağlan veya Başlat (CDP / Headless)
  const browser = await getBrowser();
  const page = await browser.newPage();
  await page.setViewport({ width: 1366, height: 768 });

  let totalNew = 0;
  const startTime = Date.now();

  try {
    for (let i = 0; i < tasks.length; i++) {
      const task = tasks[i];
      const progressStr = `[${i + 1}/${tasks.length}]`;
      const locStr = `${task.cityName} > ${task.countyName} (${task.category.toUpperCase()})`;

      let taskSaved = 0;
      let pageNum = 1;

      // 1. HEPSİEMLAK TARAMASI
      if (targetKaynak === 'hepsiemlak' || targetKaynak === 'hepsi') {
        while (true) {
          if (maxSayfa && pageNum > maxSayfa) break;

          // Hepsiemlak URL formatı: {ilce}-satilik (konut) veya {ilce}-satilik/{kategori}
          const heSlug = task.category === 'konut' 
            ? `${task.countySlug}-satilik` 
            : `${task.countySlug}-satilik/${task.category}`;
          const url = pageNum === 1 
            ? `https://www.hepsiemlak.com/${heSlug}`
            : `https://www.hepsiemlak.com/${heSlug}?page=${pageNum}`;

          // İnsansı gecikme
          const delay = hizBase * 1000 + Math.random() * 800;
          await new Promise(r => setTimeout(r, delay));

          const { totalAds, totalPages, items } = await scrapeHepsiemlakPage(page, url, task.category, task.cityName, task.countyName);

          if (!items || items.length === 0) break;

          const saved = saveListingsToDb(db, items, centroids);
          taskSaved += saved;
          totalNew += saved;

          const mahalles = [...new Set(items.map(x => x.mahalle).filter(Boolean))];
          const coordCount = items.filter(x => x.lat && x.lon).length;
          const adaCount = items.filter(x => x.ada || x.parsel).length;

          log(
            `${progressStr} [Hepsiemlak] ${locStr} (Sayfa ${pageNum}/${totalPages}): ` +
            `${items.length} ilan (${saved} yeni/güncel) | ` +
            `📍 ${mahalles.length} Mahalle | 🛰️ ${coordCount}/${items.length} Koordinatlı` +
            (adaCount > 0 ? ` | 📐 ${adaCount} Ada/Parsel` : ''),
            'SUCCESS'
          );

          if (pageNum >= totalPages) break;
          pageNum++;
        }
      }

      // 2. EMLAKJET TARAMASI
      if (targetKaynak === 'emlakjet' || targetKaynak === 'hepsi') {
        const ejCatMap = {
          konut: 'satilik-konut',
          arsa: 'satilik-arsa',
          isyeri: 'satilik-isyeri'
        };
        const ejCat = ejCatMap[task.category] || 'satilik-konut';
        const ejSlug = `${task.citySlug}-${task.countySlug}`;

        let ejPage = 1;
        while (true) {
          if (maxSayfa && ejPage > maxSayfa) break;

          const ejUrl = ejPage === 1
            ? `https://www.emlakjet.com/${ejCat}/${ejSlug}`
            : `https://www.emlakjet.com/${ejCat}/${ejSlug}?page=${ejPage}`;

          await new Promise(r => setTimeout(r, hizBase * 800 + Math.random() * 400));
          const { items } = await scrapeEmlakjetPage(page, ejUrl, task.category, task.cityName, task.countyName);

          if (!items || items.length === 0) break;

          // Arsa kategorisinde ilan detay zenginleştirmesi (Harita GPS Pini, Ada, Parsel, İmar)
          if (task.category === 'arsa') {
            for (const itm of items) {
              if (itm.url && (!itm.lat || !itm.ada)) {
                const det = await fetchEmlakjetArsaDetail(itm.url);
                if (det.lat && det.lon) {
                  itm.lat = det.lat;
                  itm.lon = det.lon;
                }
                if (det.ada) itm.ada = det.ada;
                if (det.parsel) itm.parsel = det.parsel;
                if (det.imar_durumu) itm.imar_durumu = det.imar_durumu;
                if (det.tapu_durumu) itm.tapu_durumu = det.tapu_durumu;
                if (det.kaks_emsal) itm.kaks_emsal = det.kaks_emsal;
              }
            }
          }

          const saved = saveListingsToDb(db, items, centroids);
          taskSaved += saved;
          totalNew += saved;

          const mahalles = [...new Set(items.map(x => x.mahalle).filter(Boolean))];
          const coordCount = items.filter(x => x.lat && x.lon).length;
          const adaCount = items.filter(x => x.ada || x.parsel).length;

          log(
            `${progressStr} [Emlakjet] ${locStr} (Sayfa ${ejPage}): ` +
            `${items.length} ilan (${saved} yeni/güncel) | ` +
            `📍 ${mahalles.length} Mahalle | 🛰️ ${coordCount}/${items.length} Koordinatlı` +
            (adaCount > 0 ? ` | 📐 ${adaCount} Ada/Parsel` : ''),
            'SUCCESS'
          );

          if (items.length < 20) break; // Son sayfaya ulaşıldı
          ejPage++;
        }
      }
    }
  } catch (err) {
    log(`Kritik tarama hatası: ${err.message}`, 'ERROR');
  } finally {
    if (page) await page.close().catch(() => {});
    if (browser) {
      if (isCdpConnected) {
        await browser.disconnect().catch(() => {});
      } else {
        await browser.close().catch(() => {});
      }
    }

    const elapsed = Math.round((Date.now() - startTime) / 1000 / 60 * 10) / 10;
    const finalCount = countStmt.get().cnt;

    log('='.repeat(70), 'INFO');
    log(`🏁 TARAMA TAMAMLANDI (${elapsed} dakikada)`, 'SUCCESS');
    log(`💎 Bu Oturumda Kaydedilen/Güncellenen İlan: ${totalNew.toLocaleString('tr-TR')}`, 'SUCCESS');
    log(`📦 Veritabanındaki Güncel Toplam İlan: ${finalCount.toLocaleString('tr-TR')}`, 'SUCCESS');
    log('='.repeat(70), 'INFO');

    if (exportCsvFlag) {
      exportToCsv(db, CSV_DIR);
    }
  }
}

// Doğrudan çalıştırıldığında başlat
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(err => {
    console.error('Fatal:', err);
    process.exit(1);
  });
}
