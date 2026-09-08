// Popup: backend ayarı + tara/kaydet
document.addEventListener("DOMContentLoaded", async () => {
  const backendEl = document.getElementById("backend");
  const durum = document.getElementById("durum");
  const taraBtn = document.getElementById("tara");
  const kaydetBtn = document.getElementById("kaydet");

  const o = await chrome.storage.sync.get({ backend: "http://localhost:3001", otomatik: false, yaziliIzin: false });
  backendEl.value = o.backend || "http://localhost:3001";
  const backendAdres = () => (backendEl.value.trim() || "http://localhost:3001").replace(/\/$/, "");
  backendEl.onchange = () => { chrome.storage.sync.set({ backend: backendAdres() }); yenile(); };
  const otoEl = document.getElementById("otomatik");
  const izinEl = document.getElementById("izinOnayi");
  izinEl.checked = o.yaziliIzin === true;
  otoEl.checked = o.otomatik === true && izinEl.checked;
  izinEl.onchange = async () => {
    await chrome.storage.sync.set({ yaziliIzin: izinEl.checked });
    if (!izinEl.checked) {
      otoEl.checked = false;
      await chrome.storage.sync.set({ otomatik: false });
    }
  };
  otoEl.onchange = async () => {
    if (otoEl.checked && !izinEl.checked) {
      otoEl.checked = false;
      durum.textContent = "Otomatik kayıt için önce yazılı izin onayını işaretleyin.";
      return;
    }
    await chrome.storage.sync.set({ otomatik: otoEl.checked });
  };

  async function taramaGoster() {
    const el = document.getElementById("taramaDurum");
    try {
      const { taramaDurumu: d } = await chrome.storage.local.get({ taramaDurumu: null });
      if (!d) { el.textContent = "Etkin tarama yok."; return; }
      const mod = d.mod === "emlakjet_piyasa" ? "Emlakjet piyasa hiyerarşisi" : "İlan sayfalaması";
      el.textContent = `${d.aktif ? "🟢" : "⚪"} ${mod}\n${d.islenenSayfa || 0}/${d.maxSayfa || "?"} sayfa • ${d.eklenen || 0} yeni • ${d.guncellenen || 0} güncel\nKuyruk: ${(d.kuyruk || []).length}${d.bitisNedeni ? "\n" + d.bitisNedeni : ""}${d.hata ? "\nHata: " + d.hata : ""}`;
    } catch { el.textContent = "Tarama durumu okunamadı."; }
  }

  // Canlı sayaç: 2 sn'de bir güncellenir (popup açıkken)
  async function yenile() {
    try {
      const st = await chrome.storage.local.get({ gunluk: null, son: null });
      const bugun = new Date().toISOString().slice(0, 10);
      const adet = st.gunluk && st.gunluk.tarih === bugun ? st.gunluk.adet : 0;
      document.getElementById("bBugun").textContent = adet;
      if (st.son?.zaman) {
        const d = new Date(st.son.zaman);
        document.getElementById("bSon").textContent =
          `son: ${st.son.adet} satır (${st.son.tur || "?"}) • ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
      } else {
        document.getElementById("bSon").textContent = "henüz kayıt yok — bir ilan sayfası açman yeterli";
      }
    } catch { /* yoksay */ }
    try {
      const [ilan, piyasa] = await Promise.all([
        fetch(backendAdres() + "/api/ilanlar").then((r) => r.json()),
        fetch(backendAdres() + "/api/piyasa").then((r) => r.json()),
      ]);
      document.getElementById("bDurum").textContent = "🟢 bağlı";
      const pAdet = Array.isArray(piyasa) ? piyasa.length : 0;
      document.getElementById("bHavuz").textContent = `havuz: ${ilan.adet || 0} ilan + ${pAdet} piyasa satırı`;
    } catch {
      document.getElementById("bDurum").textContent = "🔴 backend kapalı (npm start?)";
      document.getElementById("bHavuz").textContent = "havuz: —";
    }
    taramaGoster();
    ejDurumYenile();
    ejHavuzYaz();
  }
  yenile();
  const canli = setInterval(yenile, 2000);
  window.addEventListener("unload", () => clearInterval(canli));

  async function sekme() {
    const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
    return t;
  }

  // Destek: piyasa/analiz sayfaları + tüm ilan arama/sonuç sayfaları
  // (tespit sayfa içeriğinden yapılır, URL desenine bağımlı değildir)
  function destekleniyorMu(url) {
    return /https:\/\/([^/]*\.)?(endeksa\.com|emlakjet\.com|sahibinden\.com|hepsiemlak\.com|zingat\.com)\//.test(url || "");
  }

  function kisaUrl(url) {
    try {
      const u = new URL(url);
      return u.host + u.pathname.slice(0, 80);
    } catch { return String(url || "?").slice(0, 100); }
  }

  // İçerik betiği yoksa (sayfa eklentiden önce açılmışsa) sonradan enjekte et
  async function gonder(tabId, mesaj) {
    try {
      return await chrome.tabs.sendMessage(tabId, mesaj);
    } catch (e) {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["parsers.js", "content.js"] });
      await new Promise((r) => setTimeout(r, 500));
      return await chrome.tabs.sendMessage(tabId, mesaj);
    }
  }

  function geoOverride() {
    return {
      il: document.getElementById("fIl").value.trim(),
      ilce: document.getElementById("fIlce").value.trim(),
    };
  }

  function sonucYaz(s) {
    if (!s.ok) { durum.textContent = "Hata: " + s.hata; return; }
    if (s.tur === "ilan") {
      durum.textContent =
        `🏠 ${s.ilanlar.length} ilan bulundu (${s.geo.il || "?"}${s.geo.ilce ? "/" + s.geo.ilce : ""})\n` +
        `Örnek: ${s.ilanlar[0].baslik.slice(0, 50)} → ${(s.ilanlar[0].fiyat ?? "-").toLocaleString?.("tr") || s.ilanlar[0].fiyat} ₺` +
        (s.ilanlar[0].m2 ? ` (${s.ilanlar[0].m2} m²)` : "") +
        `\nİl/ilçe yanlışsa aşağıdan düzeltip tekrar tara.`;
    } else {
      durum.textContent =
        `${s.geo.site} | ${s.geo.il}${s.geo.ilce ? "/" + s.geo.ilce : ""} (${s.geo.tip})\n` +
        `${s.satirlar.length} satır bulundu. Örnek: ${s.satirlar[0].ad} → ${s.satirlar[0].m2 ?? "-"} ₺/m²`;
    }
  }

  taraBtn.onclick = async () => {
    durum.textContent = "Taranıyor…";
    try {
      const t = await sekme();
      if (!destekleniyorMu(t.url)) {
        durum.textContent = "Bu sayfa desteklenmiyor:\n" + kisaUrl(t.url) + "\nDestek: Endeksa, Emlakjet, Sahibinden, Hepsiemlak, Zingat.";
        return;
      }
      const s = await gonder(t.id, { type: "TARA", geo: geoOverride() });
      sonucYaz(s);
      yenile();
    } catch (e) {
      durum.textContent = "Hata: sayfayı bir kez yenileyip tekrar deneyin. (" + String(e.message || e).slice(0, 80) + ")";
    }
  };

  document.getElementById("teshis").onclick = async () => {
    const pre = document.getElementById("teshisCikti");
    pre.style.display = "block";
    pre.textContent = "Çalışıyor…";
    try {
      const t = await sekme();
      const s = await gonder(t.id, { type: "TARA", geo: geoOverride() });
      const ozet = {
        url: kisaUrl(t.url),
        ok: s.ok, tur: s.tur, hata: s.hata || null,
        geo: s.geo || null, tani: s.tani || null,
        satirlar: (s.ilanlar || s.satirlar || []).slice(0, 8).map((x) => ({
          baslik: (x.baslik || x.ad || "").slice(0, 45),
          il: x.il || null, ilce: x.ilce || null, mahalle: x.mahalle || null,
          m2: x.m2 ?? null, oda: x.oda || null, fiyat: x.fiyat ?? x.ortFiyat ?? null,
          konum: (x.konum || "").slice(0, 50) || null,
        })),
      };
      pre.textContent = JSON.stringify(ozet, null, 1);
      durum.textContent = "Teşhis hazır — çıktıyı kopyalayıp gönderin, noktayı bulayım.";
    } catch (e) {
      pre.textContent = "Hata: " + String(e.message || e);
    }
  };

  // ---------------- Emlakjet bölge endeksi (arka plan servisi) ----------------
  const ejAyar = () => ({
    tipler: [
      document.getElementById("ejKonut").checked ? "konut" : null,
      document.getElementById("ejArsa").checked ? "arsa" : null,
    ].filter(Boolean),
    derinlik: document.getElementById("ejDerinlik").value,
    trendKaydet: document.getElementById("ejTrend").checked,
    beklemeMs: Math.round((Number(document.getElementById("ejBekleme").value) || 1.5) * 1000),
  });

  // MV3 servis worker'ı uykudayken ilk mesaj bazen uyandırma yarışına
  // takılır; bir kez daha denemek bunu çözer. Hâlâ ulaşılamıyorsa servis
  // hiç kaydolmamıştır — kullanıcıya ne yapacağını söyle.
  async function ejGonder(type, ayar) {
    const mesaj = ayar ? { type, ayar } : { type };
    for (let deneme = 0; deneme < 2; deneme++) {
      try {
        const y = await chrome.runtime.sendMessage(mesaj);
        if (y !== undefined) return y;
      } catch { /* servis uyanıyor olabilir */ }
      if (deneme === 0) await new Promise((r) => setTimeout(r, 400));
    }
    return {
      ok: false,
      hata: "Arka plan servisi çalışmıyor. chrome://extensions → Piyasa Toplayıcı → "
        + "yenile (⟳) düğmesine basın. Sorun sürerse aynı karttaki "
        + "“Service worker” bağlantısını açıp konsoldaki hatayı gönderin.",
    };
  }

  function ejYaz(d) {
    const el = document.getElementById("ejDurum");
    const kuyruk = d?.kuyrukAdet ?? (d?.kuyruk || []).length;
    if (!d || (!d.aktif && !d.islenen && !kuyruk)) {
      el.textContent = "Tarama başlatılmadı.";
      return;
    }
    const isaret = d.duraklat ? "⏸ duraklatıldı" : d.aktif ? "🟢 çalışıyor" : "⚪ durdu";
    const yuzde = d.toplamHedef ? Math.min(99, Math.round((d.islenen / d.toplamHedef) * 100)) : 0;
    const hiz = `${((d.beklemeMs || 0) / 1000).toFixed(1)} sn`
      + (d.tabanBekleme && d.beklemeMs > d.tabanBekleme ? " ⚠ yavaşlatıldı" : "");
    el.textContent =
      `${isaret} • ${(d.tipler || []).join(" + ")} • derinlik: ${d.derinlik} • ${hiz}\n` +
      `${d.islenen || 0} bölge işlendi (~%${yuzde}) • kuyrukta ${kuyruk}\n` +
      `yazılan: ${d.yazilanBolge || 0} bölge / ${d.yazilanSatir || 0} satır` +
      (d.tampon?.length ? ` • gönderilmeyi bekleyen ${d.tampon.length}` : "") +
      (d.sonBolge ? `\nson: ${d.sonBolge}` : "") +
      (d.neden ? `\n${d.neden}` : "") +
      (d.sonHata ? `\nhata: ${d.sonHata}` : "");
  }

  async function ejHavuzYaz() {
    const el = document.getElementById("ejHavuz");
    const s = await ejGonder("EJ_OZET");
    if (!s?.ok) { el.textContent = ""; return; }
    const o = s.ozet;
    const n = (v) => (v || 0).toLocaleString("tr");
    el.textContent =
      `yerel havuz — bölge: ${n(o.toplam.bolge)} (konut ${n(o.bolge.konut)} / arsa ${n(o.bolge.arsa)})\n` +
      `aylık seri: ${n(o.toplam.trend)} • dağılım: ${n(o.toplam.dagilim)} satır`;
  }

  async function ejDurumYenile() {
    const s = await ejGonder("EJ_DURUM");
    if (s?.ok) ejYaz(s.durum);
    else document.getElementById("ejDurum").textContent = s?.hata || "Durum okunamadı.";
  }

  document.getElementById("ejBaslat").onclick = async () => {
    if (!izinEl.checked) { durum.textContent = "Önce yazılı izin onayını işaretleyin."; return; }
    const s = await ejGonder("EJ_BASLAT", ejAyar());
    durum.textContent = s.ok ? "✅ Bölge endeksi taraması başladı." : "Hata: " + s.hata;
    ejDurumYenile();
  };
  document.getElementById("ejDuraklat").onclick = async () => { await ejGonder("EJ_DURAKLAT"); ejDurumYenile(); };
  document.getElementById("ejDevam").onclick = async () => {
    const s = await ejGonder("EJ_DEVAM");
    if (!s.ok) durum.textContent = "Hata: " + s.hata;
    ejDurumYenile();
  };
  document.getElementById("ejDurdur").onclick = async () => { await ejGonder("EJ_DURDUR"); ejDurumYenile(); };
  document.getElementById("ejSifirla").onclick = async () => { await ejGonder("EJ_SIFIRLA"); ejDurumYenile(); };
  document.getElementById("ejVeri").onclick = () => chrome.tabs.create({ url: chrome.runtime.getURL("veri.html") });

  function taramaAyari() {
    return {
      maxSayfa: Number(document.getElementById("maxSayfa").value) || 1200,
      beklemeSaniye: Number(document.getElementById("bekleme").value) || 8,
    };
  }

  async function taramaBaslat(mod) {
    if (!izinEl.checked) {
      durum.textContent = "Tarama başlatılmadı: önce yazılı izin onayını işaretleyin.";
      return;
    }
    durum.textContent = "Tarama hazırlanıyor…";
    try {
      const t = await sekme();
      if (!destekleniyorMu(t.url)) throw new Error("Bu site desteklenmiyor.");
      const s = await gonder(t.id, { type: "TARAMA_BASLAT", mod, ayar: taramaAyari() });
      durum.textContent = s.ok ? "✅ Tarama başladı. Bu sekme sayfaları otomatik gezecek." : "Hata: " + s.hata;
      taramaGoster();
    } catch (e) { durum.textContent = "Hata: " + String(e.message || e).slice(0, 120); }
  }

  document.getElementById("ilanTarama").onclick = () => taramaBaslat("ilan_sayfalama");
  document.getElementById("piyasaTarama").onclick = () => taramaBaslat("emlakjet_piyasa");
  document.getElementById("taramaDurdur").onclick = async () => {
    try {
      const { taramaDurumu: d } = await chrome.storage.local.get({ taramaDurumu: null });
      if (d) {
        d.aktif = false; d.bitisNedeni = "Kullanıcı durdurdu"; d.sonGuncelleme = new Date().toISOString();
        await chrome.storage.local.set({ taramaDurumu: d });
      }
      durum.textContent = "⏹️ Tarama durduruldu.";
      taramaGoster();
    } catch (e) { durum.textContent = "Hata: " + String(e.message || e).slice(0, 120); }
  };

  kaydetBtn.onclick = async () => {
    kaydetBtn.disabled = true;
    durum.textContent = "Kaydediliyor…";
    try {
      const t = await sekme();
      if (!destekleniyorMu(t.url)) {
        durum.textContent = "Bu sayfa desteklenmiyor:\n" + kisaUrl(t.url) + "\nDestek: Endeksa, Emlakjet, Sahibinden, Hepsiemlak, Zingat.";
        kaydetBtn.disabled = false;
        return;
      }
      const s = await gonder(t.id, { type: "KAYDET", geo: geoOverride() });
      if (s.ok && s.tur === "ilan") {
        durum.textContent = `✅ ${s.toplam} ilan havuza yazıldı (${s.eklenen} yeni)`;
      } else {
        durum.textContent = s.ok
          ? `✅ Kaydedildi: ${s.eklenen} yeni + ${s.guncellenen} güncelleme (${s.toplam} satır)`
          : "Hata: " + s.hata;
      }
    } catch (e) {
      durum.textContent = "Hata: " + String(e.message || e).slice(0, 100);
    }
    kaydetBtn.disabled = false;
    yenile();
  };
});
