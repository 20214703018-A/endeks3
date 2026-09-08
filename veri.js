// Yerel veri sayfası: eklentinin IndexedDB havuzunu görüntüler, arar, CSV verir.
(() => {
  "use strict";
  const DB = globalThis.PiyasaDB;
  const $ = (id) => document.getElementById(id);

  // Kümeye göre tabloda gösterilecek kolonlar (CSV'de tüm kolonlar çıkar)
  const GORUNEN = {
    bolge: ["tip", "seviye", "il", "ilce", "mahalle", "donem", "m2Fiyat", "ortFiyat", "ortM2",
      "yillikDegisim", "degisim1Yil", "degisim2Yil", "degisim5Yil",
      "amortisman", "getiri", "ortBinaYasi", "ilanSayisi", "kiraM2Fiyat", "olcumKaynagi"],
    trend: ["tip", "seviye", "il", "ilce", "mahalle", "ay", "projeksiyon", "m2Fiyat", "ortFiyat", "ortM2",
      "ilanSayisi", "aylikDegisim", "yillikDegisim", "endeks", "kiraM2Fiyat"],
    dagilim: ["tip", "seviye", "il", "ilce", "mahalle", "dagilim", "segment", "oran",
      "m2Fiyat", "ortFiyat", "ortM2", "ilanSayisi", "ortBinaYasi"],
  };
  const BASLIK = {
    tip: "Tip", seviye: "Seviye", il: "İl", ilce: "İlçe", mahalle: "Mahalle", donem: "Dönem",
    ay: "Ay", m2Fiyat: "m² fiyat ₺", ortFiyat: "Ort. fiyat ₺", ortM2: "Ort. m²",
    yillikDegisim: "Yıllık %", aylikDegisim: "Aylık %", amortisman: "Amortisman yıl",
    getiri: "Getiri %", ortBinaYasi: "Ort. bina yaşı", ilanSayisi: "İlan", endeks: "Endeks",
    kiraM2Fiyat: "Kira ₺/m²", dagilim: "Kırılım", segment: "Segment", oran: "Pay %",
    olcumKaynagi: "Ölçüm alanı", projeksiyon: "Projeksiyon",
    degisim1Yil: "1 yıl %", degisim2Yil: "2 yıl %", degisim5Yil: "5 yıl %",
  };
  const METIN_KOLON = new Set(["tip", "seviye", "il", "ilce", "mahalle", "donem", "ay",
    "dagilim", "segment", "olcumKaynagi", "projeksiyon"]);
  const KIRILIM_AD = { yas: "Bina yaşı", oda: "Oda", kat: "Kat", isitma: "Isıtma", alan: "Alan (m²)" };

  const bicim = (k, v) => {
    if (v === null || v === undefined || v === "") return "—";
    if (k === "dagilim") return KIRILIM_AD[v] || v;
    if (k === "projeksiyon") return v ? "tahmin" : "gerçekleşen";
    if (METIN_KOLON.has(k)) return String(v);
    if (typeof v !== "number") return String(v);
    if (/Degisim|degisim|getiri|oran/i.test(k)) return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
    return v.toLocaleString("tr-TR");
  };

  async function kartlariCiz() {
    const o = await DB.ozet();
    $("kartlar").innerHTML = [
      ["Bölge özeti", o.toplam.bolge, `konut ${o.bolge.konut.toLocaleString("tr")} · arsa ${o.bolge.arsa.toLocaleString("tr")}`],
      ["Aylık trend satırı", o.toplam.trend, `konut ${o.trend.konut.toLocaleString("tr")} · arsa ${o.trend.arsa.toLocaleString("tr")}`],
      ["Dağılım satırı", o.toplam.dagilim, `yaş / oda / kat / ısıtma / alan`],
    ].map(([ad, n, alt]) =>
      `<div class="kart"><span>${ad}</span><b>${(n || 0).toLocaleString("tr")}</b><span>${alt}</span></div>`,
    ).join("");
    return o;
  }

  function tabloCiz(depo, satirlar) {
    const kolonlar = GORUNEN[depo];
    const thead = $("tablo").tHead;
    const tbody = $("tablo").tBodies[0];
    thead.innerHTML = `<tr>${kolonlar.map((k) =>
      `<th class="${METIN_KOLON.has(k) ? "sol" : ""}">${BASLIK[k] || k}</th>`).join("")}</tr>`;
    if (!satirlar.length) {
      tbody.innerHTML = `<tr><td class="bos sol" colspan="${kolonlar.length}">Kayıt yok. Popup'tan “Emlakjet bölge endeksi” taramasını başlatın.</td></tr>`;
      return;
    }
    tbody.innerHTML = satirlar.map((s) => `<tr>${kolonlar.map((k) => {
      if (k === "tip") return `<td class="sol"><span class="rozet ${s.tip === "arsa" ? "arsa" : ""}">${s.tip}</span></td>`;
      return `<td class="${METIN_KOLON.has(k) ? "sol" : ""}">${bicim(k, s[k])}</td>`;
    }).join("")}</tr>`).join("");
  }

  async function ara() {
    const depo = $("depo").value;
    $("bilgi").textContent = "Aranıyor…";
    $("araBtn").disabled = true;
    try {
      const s = await DB.ara({
        depo,
        metin: $("q").value,
        tip: $("tip").value,
        seviye: $("seviye").value,
        limit: Number($("limit").value) || 300,
        sirala: depo === "trend" ? "" : "m2Fiyat",
      });
      tabloCiz(depo, s.satirlar);
      $("bilgi").innerHTML = `<b>${s.eslesen.toLocaleString("tr")}</b> eşleşme ` +
        `(${s.taranan.toLocaleString("tr")} kayıt tarandı) — ilk ${s.satirlar.length} satır gösteriliyor.`;
    } catch (e) {
      $("bilgi").textContent = "Hata: " + String(e.message || e);
    } finally {
      $("araBtn").disabled = false;
    }
  }

  function indir(ad, parcalar) {
    const url = URL.createObjectURL(new Blob(parcalar, { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = ad;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  async function disaAktar(depo, tip) {
    const { parcalar, adet } = await DB.csvUret(depo, tip, DB.KOLONLAR[depo]);
    if (!adet) return 0;
    indir(`emlakjet-${depo}-${tip || "tumu"}.csv`, parcalar);
    return adet;
  }

  $("araBtn").onclick = ara;
  $("q").addEventListener("keydown", (e) => { if (e.key === "Enter") ara(); });
  for (const id of ["depo", "tip", "seviye", "limit"]) $(id).onchange = ara;

  $("disaAktar").onclick = async () => {
    const depo = $("depo").value;
    const tip = $("tip").value;
    $("bilgi").textContent = "CSV hazırlanıyor…";
    const n = await disaAktar(depo, tip);
    $("bilgi").textContent = n ? `${n.toLocaleString("tr")} satır indirildi.` : "Bu kümede kayıt yok.";
  };

  $("hepsiniAktar").onclick = async () => {
    $("bilgi").textContent = "Tüm kümeler hazırlanıyor…";
    let toplam = 0;
    for (const depo of ["bolge", "trend", "dagilim"]) {
      for (const tip of ["konut", "arsa"]) {
        toplam += await disaAktar(depo, tip);
        await new Promise((r) => setTimeout(r, 350)); // indirmeler üst üste binmesin
      }
    }
    $("bilgi").textContent = `${toplam.toLocaleString("tr")} satır, konut/arsa ayrı dosyalar halinde indirildi.`;
  };

  $("sil").onclick = async () => {
    if (!confirm("Yerel havuzdaki tüm bölge, trend ve dağılım kayıtları silinecek. Emin misiniz?")) return;
    await DB.temizle();
    await kartlariCiz();
    await ara();
  };

  kartlariCiz().then(ara);
  setInterval(kartlariCiz, 5000); // tarama sürerken sayaçlar canlı kalsın
})();
