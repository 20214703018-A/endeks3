(function () {
  'use strict';

  const form = document.getElementById('analysisForm');
  const submitButton = document.getElementById('submitButton');
  const sampleButton = document.getElementById('sampleButton');
  const loadingState = document.getElementById('loadingState');
  const formError = document.getElementById('formError');
  const resultAlert = document.getElementById('resultAlert');
  const money = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 });
  const number = new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 1 });
  const compact = new Intl.NumberFormat('tr-TR', { notation: 'compact', maximumFractionDigits: 1 });

  const map = L.map('map', { center: [40.643806, 29.324681], zoom: 15, zoomControl: false });
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
    attribution: 'Imagery © Esri and contributors'
  }).addTo(map);
  const labels = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', {
    maxZoom: 20,
    attribution: '© OpenStreetMap contributors © CARTO'
  }).addTo(map);
  const parcelLayer = L.geoJSON(null, { style: { color: '#d9f24f', weight: 4, fillColor: '#0f6b4f', fillOpacity: .28 } }).addTo(map);
  const comparableLayer = L.layerGroup().addTo(map);
  let lastBounds = null;
  let lastParcelCenter = null;
  let clickMarker = null;

  // Canlı TKGM MEGSİS parsel sınırları (WMS proxy) — yakınlaşınca görünür.
  const TkgmParcelLayer = L.TileLayer.extend({
    getTileUrl: function (coords) {
      const bounds = this._tileCoordsToBounds(coords);
      const nw = L.CRS.EPSG3857.project(bounds.getNorthWest());
      const se = L.CRS.EPSG3857.project(bounds.getSouthEast());
      const bbox = [nw.x, se.y, se.x, nw.y].join(',');
      return `https://www.kolayimar.com/api/geo-proxy/map?slug=parsel&layers=TKGM:parseller&bbox=${bbox}&width=256&height=256&format=image/png&crs=EPSG:3857`;
    }
  });
  const tkgmParcelWms = new TkgmParcelLayer('', { minZoom: 14, maxZoom: 21, opacity: 0.8, tileSize: 256, zIndex: 350 }).addTo(map);
  L.control.layers(null, { 'TKGM parsel sınırları': tkgmParcelWms }, { position: 'topright', collapsed: true }).addTo(map);

  // Haritaya tıklayınca o noktadaki parseli seç ve analiz et.
  map.on('click', (event) => { selectParcelAt(event.latlng.lat, event.latlng.lng); });

  // ---- Odak mini-haritaları (her konu kendi bölümünde) ----
  const miniMaps = {};
  const densityClusterIcon = (cluster) => {
    let sum = 0;
    cluster.getAllChildMarkers().forEach((m) => { sum += (m.options.sayi || 0); });
    const size = sum >= 500 ? 'lg' : (sum >= 100 ? 'md' : 'sm');
    return L.divIcon({ html: `<div><span>${number.format(sum)}</span></div>`, className: `astim-cluster astim-${size}`, iconSize: L.point(38, 38) });
  };
  function getMini(id) {
    if (!miniMaps[id]) {
      const m = L.map(id, { zoomControl: false, attributionControl: false, scrollWheelZoom: false });
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }).addTo(m);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { maxZoom: 20 }).addTo(m);
      L.control.zoom({ position: 'bottomright' }).addTo(m);
      miniMaps[id] = { map: m, layer: L.layerGroup().addTo(m) };
    }
    return miniMaps[id];
  }
  function miniReset(id) { const mm = getMini(id); mm.layer.clearLayers(); return mm; }
  function fitMini(mm, points) {
    if (points.length) {
      let b = points[0];
      points.slice(1).forEach((p) => { b = b.extend(p); });
      if (b.isValid()) { mm.map.fitBounds(b.pad(0.3), { maxZoom: 16 }); mm.map.invalidateSize(); return; }
    }
    mm.map.setView([39.0, 35.0], 5); mm.map.invalidateSize();
  }
  function parcelPoint(result) {
    const la = Number(result.input.lat), lo = Number(result.input.lon);
    return (Number.isFinite(la) && Number.isFinite(lo)) ? [la, lo] : null;
  }
  function addParcelToMini(mm, result, points) {
    const geom = normalizedGeometry(result.parcel_evidence && result.parcel_evidence.parcel && result.parcel_evidence.parcel.geometry);
    if (geom) {
      const gl = L.geoJSON(geom, { style: { color: '#d9f24f', weight: 3, fillColor: '#0f6b4f', fillOpacity: .3 } });
      mm.layer.addLayer(gl);
      const b = gl.getBounds(); if (b.isValid()) points.push(b);
    }
    const pt = parcelPoint(result);
    if (pt) {
      mm.layer.addLayer(L.circleMarker(pt, { radius: 7, color: '#d9f24f', fillColor: '#0f6b4f', fillOpacity: .9, weight: 3 }));
      points.push(L.latLngBounds([pt]));
    }
  }
  const findingByLayer = (gc, layer) => ((gc && gc.findings) || []).find((f) => f.layer === layer);

  function formPayload() {
    const data = new FormData(form);
    const payload = Object.fromEntries(data.entries());
    payload.canli_parsel_sorgula = data.has('canli_parsel_sorgula');
    return payload;
  }

  function setLoading(active) {
    submitButton.disabled = active;
    submitButton.querySelector('span').textContent = active ? 'Analiz hazırlanıyor…' : 'Arsayı analiz et';
    loadingState.hidden = !active;
    formError.hidden = true;
  }

  function setText(id, value) {
    document.getElementById(id).textContent = value == null || value === '' ? '—' : value;
  }

  function statusLabel(value) {
    const labels = {
      verified_at_source: 'Kaynakta doğrulandı',
      partial_zoning_verified: 'Kısmen doğrulandı',
      plan_coverage_verified: 'Plan kapsamı doğrulandı',
      source_unavailable: 'Kaynağa erişilemedi',
      not_queried: 'Sorgulanmadı',
      not_found: 'Bulunamadı'
    };
    return labels[value] || value || '—';
  }

  function normalizedGeometry(value) {
    let geojson = value;
    if (typeof geojson === 'string') {
      try { geojson = JSON.parse(geojson); } catch (_) { return null; }
    }
    if (!geojson || typeof geojson !== 'object') return null;
    if (geojson.type === 'Feature') return geojson.geometry ? geojson : null;
    if (geojson.type === 'FeatureCollection') return Array.isArray(geojson.features) ? geojson : null;
    if (!['Polygon', 'MultiPolygon'].includes(geojson.type) || !Array.isArray(geojson.coordinates)) return null;
    return { type: 'Feature', properties: {}, geometry: geojson };
  }

  function updateMap(result) {
    parcelLayer.clearLayers();
    comparableLayer.clearLayers();
    lastBounds = null;
    const points = [];
    const parcel = result.parcel_evidence && result.parcel_evidence.parcel;
    const parcelGeometry = normalizedGeometry(parcel && parcel.geometry);
    let hasParcelBoundary = false;
    if (parcelGeometry) {
      parcelLayer.addData(parcelGeometry);
      const bounds = parcelLayer.getBounds();
      if (bounds.isValid()) {
        hasParcelBoundary = true;
        points.push(bounds);
      }
    }
    const inputLat = Number(result.input.lat);
    const inputLon = Number(result.input.lon);
    if (!hasParcelBoundary && Number.isFinite(inputLat) && Number.isFinite(inputLon)) {
      const marker = L.circleMarker([inputLat, inputLon], { radius: 10, color: '#d9f24f', fillColor: '#0f6b4f', fillOpacity: .9, weight: 3 }).addTo(parcelLayer);
      points.push(L.latLngBounds([marker.getLatLng()]));
    }
    // Emsaller, ısı haritası, su/fay/elektrik ayrı bölümlerin mini-haritalarında.
    if (points.length) {
      let bounds = points[0];
      points.slice(1).forEach((item) => { bounds = bounds.extend(item); });
      lastBounds = bounds;
      map.invalidateSize();
      map.fitBounds(bounds.pad(.18), { maxZoom: 16 });
    }
    if (hasParcelBoundary) parcelLayer.bringToFront();
    const mapStatus = document.getElementById('mapStatus');
    mapStatus.innerHTML = `<i></i> ${hasParcelBoundary ? 'Canlı parsel poligonu çizildi' : (parcel ? 'Parsel bulundu; sınır geometrisi alınamadı' : 'Girilen konum ve emsaller')}`;
    window.requestAnimationFrame(() => map.invalidateSize());
  }

  function getComparableCoordinates(item, result) {
    // API bağlantı ve kaynak URL'si döndürmez; koordinat yoksa hedef çevresinde sahte nokta üretmeyiz.
    if (Number.isFinite(item.lat) && Number.isFinite(item.lon)) return [item.lat, item.lon];
    return null;
  }

  function renderComparables(result) {
    const tbody = document.getElementById('comparableRows');
    const selection = result.comparable_selection;
    const rows = selection.comparables;
    if (!rows.length) {
      const empty = selection.scope === 'koordinat_gerekli'
        ? (selection.message || '3 km emsal araması için parsel koordinatı gerekli.')
        : 'Bu parselin 1 km çevresinde arsa emsali bulunamadı.';
      tbody.innerHTML = `<tr><td colspan="6" class="table-empty">${escapeHtml(empty)}</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.neighbourhood || item.district || item.province)}</strong><small>${escapeHtml(item.district || '')}</small></td>
        <td>${number.format(item.area_m2)} m²</td>
        <td>${money.format(item.price_tl)}</td>
        <td>${money.format(item.unit_price_tl)}</td>
        <td>${item.distance_km == null ? '—' : number.format(item.distance_km) + ' km'}</td>
        <td><strong>%${number.format(item.similarity_score)}</strong></td>
      </tr>`).join('');
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  }

  function renderRegionDynamics(result) {
    const card = document.getElementById('regionDynamicsCard');
    const mi = result.market_index;
    const dd = result.district_development;
    const nb = result.neighborhood_building;
    const ip = result.investment_profile;
    if (!mi && !dd && !nb && !ip) { card.hidden = true; return; }
    card.hidden = false;
    const pct = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${number.format(v * 100)}%`);
    setText('regYoy', mi ? pct(mi.yillik_fiyat_degisim) : '—');
    setText('regDom', mi && mi.ilanda_kalma_suresi_gun != null ? `${number.format(mi.ilanda_kalma_suresi_gun)} gün` : '—');
    setText('regStock', mi ? pct(mi.yillik_stok_degisim) : '—');
    setText('regListings', mi && mi.ilan_sayisi != null ? number.format(mi.ilan_sayisi) : '—');
    setText('regSege', dd ? `${dd.tier_label} · sıra ${dd.rank}/${dd.total_districts} · sınıf ${dd.socioeconomic_class}` : 'Veri yok');
    setText('regKonutM2', nb && nb.avg_gross_m2 ? `${number.format(nb.avg_gross_m2)} m² brüt · net ${nb.median_net_m2 ? number.format(nb.median_net_m2) + ' m²' : '—'} (${number.format(nb.listing_count)} konut)` : 'Veri yok');
    setText('regKat', nb && nb.avg_floor != null ? `${number.format(nb.avg_floor)}. kat · maks ${nb.max_floor}. kat` : 'Veri yok');
    setText('regAmortisman', ip && ip.amortization_years != null ? `${number.format(ip.amortization_years)} yıl${ip.scope === 'ilce' ? ' (ilçe)' : ''}` : 'Veri yok');
    setText('regGetiri', ip && ip.rental_yield_pct != null ? `%${number.format(ip.rental_yield_pct)}` : 'Veri yok');
    setText('regBinaYasi', ip && ip.avg_building_age != null ? `${number.format(ip.avg_building_age)} yıl` : (nb ? '—' : 'Veri yok'));
    setText('regKira', ip && ip.rent_price != null ? `${money.format(ip.rent_price)}/ay${ip.rent_m2_price ? ' · ' + money.format(ip.rent_m2_price) + '/m²' : ''}` : 'Veri yok');
    setText('regionBadge', dd ? 'SEGE + endeks' : (mi ? (mi.match_scope || 'bölge') : '—'));
    document.getElementById('regionNote').textContent = nb && nb.note
      ? 'Endeks + SEGE + konut agregatı. ' + nb.note
      : 'Mahalle endeks ambarı + SEGE ilçe gelişmişlik. Yatırım / likidite bağlamı; kesin değer değildir.';
  }


  const SCOPE_LABEL = { mahalle: 'Mahalle', ilce: 'İlçe', il: 'İl' };
  const scopeBadge = (part) => (part && part.scope ? SCOPE_LABEL[part.scope] || part.scope : '—');
  const pctText = (v) => (v == null ? '—' : `%${number.format(v)}`);

  function renderBolgeProfili(result) {
    const b = result.bolge_profili || {};
    const ok = b.status === 'available';
    const d = ok ? b.demografi : null;
    const ys = ok ? b.yillik_satis : null;
    const kf = ok ? b.konut_fiyat_ozet : null;
    const af = ok ? b.arsa_fiyat_ozet : null;
    const kk = ok ? b.konut_kirilim : null;
    const poi = ok ? b.poi_ilce : null;

    // 1) Nüfus ve sosyoekonomi
    const demoCard = document.getElementById('bolgeDemografiCard');
    if (!d) { demoCard.hidden = true; } else {
      demoCard.hidden = false;
      setText('bolgeDemografiBadge', `${scopeBadge(d)} · ${d.bolge || ''}`);
      setText('bdNufus', d.nufus != null ? number.format(d.nufus) : '—');
      setText('bdHane', d.hane_sayisi != null ? number.format(d.hane_sayisi) : '—');
      setText('bdGelir', d.ortalama_hane_geliri != null ? `${money.format(d.ortalama_hane_geliri)}/ay` : '—');
      setText('bdEvSahibi', d.ev_sahibi_orani != null ? `${pctText(d.ev_sahibi_orani)} / ${pctText(d.kiraci_orani)}` : '—');
      setText('bdUni', pctText(d.egitim && d.egitim.universite));
      setText('bdYas', d.yas && d.yas.genc != null ? `${pctText(d.yas.genc)} / ${pctText(d.yas.orta)} / ${pctText(d.yas.yasli)}` : '—');
      const ses = d.ses || {};
      const bar = document.getElementById('bdSes');
      const labels = { a_plus: 'A+', a: 'A', b: 'B', c: 'C', d: 'D' };
      bar.innerHTML = Object.keys(labels).filter((k) => ses[k] != null && ses[k] > 0)
        .map((k) => `<span class="ses-${k}" style="flex:${ses[k]}" title="SES ${labels[k]}: %${number.format(ses[k])}">${labels[k]} %${number.format(ses[k])}</span>`).join('');
      const ekstra = [];
      const ms = b.medeni_stok, et = b.eticaret_harcama, yp = b.yas_piramidi;
      if (ms && ms.toplam_konut != null) ekstra.push(['Toplam konut stoku', number.format(ms.toplam_konut)]);
      if (ms && ms.ticari_mulk != null) ekstra.push(['Ticari mülk', number.format(ms.ticari_mulk)]);
      if (ms && ms.yazlik_konut != null) ekstra.push(['Yazlık konut', number.format(ms.yazlik_konut)]);
      if (et && et.aylik_barinma_kira_harcamasi != null) ekstra.push(['Aylık barınma/kira harcaması', money.format(et.aylik_barinma_kira_harcamasi)]);
      if (et && et.aylik_toplam_harcama != null) ekstra.push(['Aylık toplam harcama', money.format(et.aylik_toplam_harcama)]);
      if (yp && yp.gruplar && yp.gruplar.length) ekstra.push(['Yaş piramidi', `${yp.gruplar.length} yaş grubu (${SCOPE_LABEL[yp.scope] || yp.scope})`]);
      document.getElementById('bdEkstra').innerHTML = ekstra.map(([k, v]) => `<div><span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong></div>`).join('');
    }

    // 2) Yıllık tapu satışları
    const satisCard = document.getElementById('bolgeSatisCard');
    if (!ys || !ys.seri || !ys.seri.length) { satisCard.hidden = true; } else {
      satisCard.hidden = false;
      setText('bolgeSatisBadge', `${scopeBadge(ys)} · ${ys.yil_araligi ? ys.yil_araligi.join('–') : ''}`);
      const fmt = (v) => (v == null ? '—' : number.format(v));
      document.getElementById('bolgeSatisRows').innerHTML = ys.seri.slice().reverse()
        .map((r) => `<tr><td>${r.yil ?? '—'}</td><td>${fmt(r.konut)}</td><td>${fmt(r.ipotekli_konut)}</td><td>${fmt(r.arsa)}</td><td>${fmt(r.ipotekli_arsa)}</td></tr>`).join('');
    }

    // 3) Konut ve arsa piyasası özeti
    const fiyatCard = document.getElementById('bolgeFiyatCard');
    if (!kf && !af) { fiyatCard.hidden = true; } else {
      fiyatCard.hidden = false;
      const src = kf || af;
      setText('bolgeFiyatBadge', `${scopeBadge(src)} · ${src.donem || ''}`);
      setText('bfKonutM2', kf && kf.satilik_m2 != null ? `${money.format(kf.satilik_m2)}/m²` : 'Veri yok');
      setText('bfKiraM2', kf && kf.kiralik_m2 != null ? `${money.format(kf.kiralik_m2)}/m²` : 'Veri yok');
      setText('bfKonutFiyat', kf && kf.ortalama_fiyat != null ? money.format(kf.ortalama_fiyat) : 'Veri yok');
      setText('bfAmortisman', kf && kf.amortisman_yil != null ? `${number.format(kf.amortisman_yil)} yıl${kf.brut_kira_getirisi != null ? ' · %' + number.format(kf.brut_kira_getirisi) : ''}` : 'Veri yok');
      setText('bfBinaYasi', kf && kf.ortalama_bina_yasi != null ? `${number.format(kf.ortalama_bina_yasi)} yıl` : 'Veri yok');
      setText('bfKalma', kf && kf.satilik_kalma_gun != null ? `${number.format(kf.satilik_kalma_gun)} / ${kf.kiralik_kalma_gun != null ? number.format(kf.kiralik_kalma_gun) : '—'} gün` : 'Veri yok');
      setText('bfArsaM2', af && af.satilik_m2 != null ? `${money.format(af.satilik_m2)}/m²` : 'Veri yok');
      setText('bfArsaIlan', af && af.ilan_sayisi != null ? `${number.format(af.ilan_sayisi)} ilan${af.satilik_kalma_gun != null ? ' · ' + number.format(af.satilik_kalma_gun) + ' gün' : ''}` : 'Veri yok');
    }

    // 4) Konut stoku kırılımı
    const kirilimCard = document.getElementById('bolgeKirilimCard');
    if (!kk || !kk.gruplar || !Object.keys(kk.gruplar).length) { kirilimCard.hidden = true; } else {
      kirilimCard.hidden = false;
      setText('bolgeKirilimBadge', scopeBadge(kk));
      const titles = { oda: 'Oda sayısı', yas: 'Bina yaşı', kat: 'Kat', isitma: 'Isıtma', bina_yasi: 'Bina yaşı', kat_sayisi: 'Kat', oda_sayisi: 'Oda sayısı', isitma_tipi: 'Isıtma' };
      document.getElementById('bolgeKirilimGroups').innerHTML = Object.entries(kk.gruplar).map(([tur, items]) => `
        <div><h4>${escapeHtml(titles[tur] || tur)}</h4><ul>${items.slice(0, 6).map((x) => `
          <li><span>${escapeHtml(x.segment)}</span><small>${x.oran != null ? '%' + number.format(x.oran) : ''}${x.satilik_m2 != null ? ' · ' + money.format(x.satilik_m2) + '/m²' : ''}</small></li>`).join('')}</ul></div>`).join('');
    }

    // 5) İlçe donanımı (POI)
    const poiCard = document.getElementById('bolgePoiCard');
    if (!poi || !poi.gruplar || !Object.keys(poi.gruplar).length) { poiCard.hidden = true; } else {
      poiCard.hidden = false;
      setText('bolgePoiBadge', poi.kaynak_siniri ? `İlçe · ≥${number.format(poi.toplam)} nokta (kaynak sınırı)` : `İlçe · ${number.format(poi.toplam)} nokta`);
      const poiNote = poiCard.querySelector('.card-note');
      poiNote.textContent = 'İlçe genelindeki sağlık, eğitim, ulaşım, alışveriş ve sosyal tesis sayıları. Koordinat içermez; parsele uzaklık değil ilçe donanımıdır.'
        + (poi.kaynak_siniri ? ' Kaynak ilçe başına 2.000 kayıtla sınırlı; bu ilçede sayılar alt sınırdır.' : '');
      const titles = { saglik: 'Sağlık', egitim: 'Eğitim', ulasim: 'Ulaşım', alisveris: 'Alışveriş', yeme_icme: 'Yeme-içme', spor_kultur: 'Spor & kültür', konut_stoku: 'Site / kooperatif', ibadet: 'İbadet' };
      document.getElementById('bolgePoiGroups').innerHTML = Object.entries(poi.gruplar).map(([g, v]) => `
        <div><h4>${escapeHtml(titles[g] || g)}</h4><strong class="poi-total">${number.format(v.toplam)}</strong><ul>${Object.entries(v.alt).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, n]) => `
          <li><span>${escapeHtml(k)}</span><small>${number.format(n)}</small></li>`).join('')}</ul></div>`).join('');
    }
  }

  // ---- Yakın önemli noktalar (OSM POI ambarı) ----
  const YAKIN_GRUPLAR = [
    ['Sağlık', ['hastane', 'saglik_ocagi', 'eczane']],
    ['Eğitim', ['anaokulu', 'ilkokul', 'ortaokul', 'lise', 'okul', 'universite']],
    ['Ulaşım', ['otobus_duragi', 'tramvay_duragi', 'metro_istasyonu', 'tren_istasyonu', 'otogar', 'iskele', 'liman', 'havalimani']],
    ['Alışveriş', ['market', 'avm', 'pazar', 'hal', 'tekel']],
    ['Yeme-içme', ['kafe', 'restoran', 'fast_food']],
    ['Sanayi', ['osb', 'sanayi_sitesi', 'sanayi_alani']],
    ['Spor & yeşil', ['stadyum', 'spor_salonu', 'park']],
    ['Kamu & hizmet', ['karakol', 'itfaiye', 'belediye', 'benzin_istasyonu', 'otopark', 'cami', 'ibadethane']],
  ];
  const YAKIN_RENK = { 'Sağlık': '#d64545', 'Eğitim': '#2f6fd6', 'Ulaşım': '#7a3fb8', 'Alışveriş': '#d98f1f', 'Yeme-içme': '#c2497a', 'Sanayi': '#5b5b5b', 'Spor & yeşil': '#2f9e5b', 'Kamu & hizmet': '#3b8f9a' };
  const km = (m) => (m >= 1000 ? `${number.format(m / 1000)} km` : `${number.format(m)} m`);

  function renderYakin(result) {
    const y = result.yakin_noktalar;
    const card = document.getElementById('yakinCard');
    if (!y || y.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const toplam = Object.values(y.sayim || {}).reduce((a, v) => a + (v['3000m'] || 0), 0);
    setText('yakinBadge', `${number.format(toplam)} nokta · 3 km`);
    const mm = miniReset('mapYakin');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const groupsEl = document.getElementById('yakinGroups');
    groupsEl.innerHTML = YAKIN_GRUPLAR.map(([grup, keys]) => {
      const rows = keys.map((k) => {
        const h = (y.hedefler || {})[k]; if (!h || !h.en_yakin.length) return '';
        const it = h.en_yakin[0];
        const ad = it.ad || it.marka || h.etiket;
        const yol = it.yol_mesafe_m != null ? `<em>araçla ${km(it.yol_mesafe_m)} · ${number.format(it.yol_sure_dk)} dk</em>` : '';
        const n1 = (y.sayim[k] || {})['1000m'];
        // haritaya işaret
        const mk = L.circleMarker([it.lat, it.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: YAKIN_RENK[grup], fillOpacity: 0.95 })
          .bindTooltip(`${escapeHtml(h.etiket)}: ${escapeHtml(ad)} · ${km(it.mesafe_m)}`);
        mm.layer.addLayer(mk); if (it.mesafe_m <= 3000) pts.push(L.latLngBounds([[it.lat, it.lon], [it.lat, it.lon]]));
        const r = it.resmi;
        const resmiTxt = r ? [r.tip, r.tur, r.durum, r.faaliyet, r.isletici, r.alan_ha ? number.format(r.alan_ha) + ' ha' : null, r.parsel_sayisi ? r.parsel_sayisi + ' parsel' : null].filter(Boolean).join(' · ') : '';
        const resmiTag = r ? ` <span class="badge" title="${escapeHtml(h.resmi_kaynak || 'resmî liste')}">resmî</span>` : '';
        return `<li><div><span>${escapeHtml(h.etiket)}${n1 ? ` · 1 km'de ${n1}` : ''}${resmiTag}</span><br><b>${escapeHtml(ad)}</b>${resmiTxt ? `<br><span>${escapeHtml(resmiTxt)}</span>` : ''}</div><small>${km(it.mesafe_m)}${it.konum_yaklasik ? '<em>≈ yaklaşık konum</em>' : ''}${yol}</small></li>`;
      }).filter(Boolean).join('');
      return rows ? `<div><h4 style="color:${YAKIN_RENK[grup]}">${grup}</h4><ul>${rows}</ul></div>` : '';
    }).join('');
    fitMini(mm, pts);
    const tt = y.toplu_tasima || {};
    const hatlar = (tt.hatlar || []);
    document.getElementById('yakinTransit').innerHTML = hatlar.length
      ? `<span class="lbl">500 m içinde ${tt.durak_500m} durak · ${hatlar.length} hat:</span>` + hatlar.slice(0, 40).map((h) => `<span class="hat-chip ${h.tur}" title="${escapeHtml(h.guzergah || h.ad || '')}${h.operator ? ' · ' + escapeHtml(h.operator) : ''}">${escapeHtml(h.hat || '?')}</span>`).join('') + (hatlar.length > 40 ? `<span class="lbl">+${hatlar.length - 40}</span>` : '')
      : `<span class="lbl">500 m içinde ${tt.durak_500m || 0} durak · OSM'de hat ilişkisi yok</span>`;
    const z = y.zincirler_1km || {};
    const chips = [];
    const zLabels = { market: 'Market', kafe: 'Kahve', restoran: 'Restoran', fast_food: 'Fast food', tekel: 'Tekel', benzin_istasyonu: 'Akaryakıt', spor_salonu: 'Spor' };
    Object.entries(z).forEach(([k, v]) => Object.entries(v).slice(0, 6).forEach(([marka, n]) => chips.push(`<span class="zincir-chip" title="${zLabels[k] || k}"><b>${escapeHtml(marka)}</b> ×${n}</span>`)));
    document.getElementById('yakinChains').innerHTML = chips.length ? `<span class="lbl">1 km'de zincirler:</span>` + chips.join('') : '';
    const yg = y.yogunluk_1km;
    const YG_ETIKET = { yeme_icme: 'Yeme-içme', perakende: 'Perakende', is_ofis: 'İş / ofis / banka', sanayi: 'Sanayi' };
    document.getElementById('yakinYogunluk').innerHTML = yg ? `<span class="lbl">1 km işletme yoğunluğu (${escapeHtml(yg.ilce)} ort. = 1×):</span>` + Object.entries(yg.gruplar).map(([k, v]) => `<span class="hat-chip" title="1 km'de ${v.yerel_sayi} (${number.format(v.yerel_km2)}/km²) · ilçe ${v.ilce_sayi} (${number.format(v.ilce_km2)}/km²)" style="border-color:${v.oran == null ? 'var(--line)' : v.oran >= 2 ? '#1f6f4a' : v.oran >= 1 ? '#3f9a6a' : '#9aa8c7'}">${YG_ETIKET[k] || k}: ${v.oran == null ? '—' : number.format(v.oran) + '×'}</span>`).join('') : '';
    const gc = y.gecmis_1km;
    const gEl = document.getElementById('yakinGecmis');
    if (gc && gc.status === 'available' && gc.toplam) {
      const yil = (t) => t.slice(0, 4) + (t > '2026-01-02' ? ' (güncel)' : '');
      const ek = Object.entries(gc.yeni_eklenen || {}), ka = Object.entries(gc.kaldirilan || {});
      const max = Math.max(1, ...ek.map(([, v]) => v), ...ka.map(([, v]) => v));
      const bars = (arr, cls) => arr.map(([t, v]) => `<div class="bar ${cls}" style="height:${Math.round(v / max * 100)}%" title="${yil(t)}: ${v}"><b>${t.slice(2, 4)}</b></div>`).join('');
      const li = (arr) => arr.slice(0, 6).map((x) => `<li><b>${escapeHtml(x.ad || x.alt_kategori)}</b><span>${escapeHtml(x.alt_kategori)}${x.eski_ad ? ' · eskiden: ' + escapeHtml(x.eski_ad) : ''} · ${x.durum === 'kaldirildi' ? 'son ' + x.son.slice(0, 4) : 'ilk ' + x.ilk.slice(0, 4)}</span></li>`).join('');
      gEl.innerHTML = `<h4>Son 5 yıl işletme değişimi (1 km, OSM kesitleri): 2021 tabanı ${gc.taban_2021} · eklenen ${Object.values(gc.yeni_eklenen).reduce((a, b) => a + b, 0)} · kaldırılan ${gc.kaldirildi_toplam} · ad değiştiren ${gc.ad_degistiren} · marka değiştiren ${gc.marka_degistiren}</h4>
        <div class="gecmis-grid"><div><h4>OSM'de ilk görülme (yıl)</h4><div class="bar-chart">${bars(ek, 'ek')}</div></div><div><h4>OSM'den kaldırılma (son görüldüğü kesit)</h4><div class="bar-chart">${bars(ka, 'kal')}</div></div></div>
        ${gc.ad_degisenler.length ? `<h4 style="margin-top:22px">El değiştirme / ad değişimi</h4><ul class="gecmis-list">${li(gc.ad_degisenler)}</ul>` : ''}
        ${gc.son_kaldirilanlar.length ? `<h4 style="margin-top:12px">OSM'den kaldırılanlar</h4><ul class="gecmis-list">${li(gc.son_kaldirilanlar)}</ul>` : ''}
        <p class="card-note" style="margin-top:8px">${escapeHtml(gc.not)}</p>`;
    } else { gEl.innerHTML = ''; }
    const dg = y.degisim_1km;
    document.getElementById('yakinDegisim').innerHTML = !dg ? '' : (dg.status === 'baseline'
      ? `<span class="lbl">İşletme değişimi: ilk OSM kesiti ${dg.ilk_anlik || ''} alındı; haftalık fark birikince "son 90 günde açılan/kaldırılan" burada görünecek.</span>`
      : `<span class="lbl">Son ${dg.gun} günde 1 km'de OSM'ye eklenen <b>${dg.eklenen}</b> · kaldırılan <b>${dg.kaldirilan}</b> POI</span>` + Object.entries(dg.ozet.eklendi || {}).map(([k, n]) => `<span class="hat-chip" title="eklendi">+${n} ${escapeHtml(k)}</span>`).join('') + Object.entries(dg.ozet.silindi || {}).map(([k, n]) => `<span class="hat-chip" title="kaldırıldı">−${n} ${escapeHtml(k)}</span>`).join(''));
    setText('yakinNote', `${y.kaynak}${y.veri_tarihi ? ' · veri ' + y.veri_tarihi.slice(0, 10) : ''}. Mesafeler kuş uçuşu; ${y.yol_mesafesi_kaynak ? 'araç mesafe/süresi ' + y.yol_mesafesi_kaynak + '.' : ''} OSM kapsamı bölgeye göre değişir; eksik nokta "yok" anlamına gelmez.`);
  }

  // ---- Okullar (MEB resmî liste + LGS) ----
  const OKUL_RENK = { anaokulu: '#e0a21f', ilkokul: '#2f6fd6', ortaokul: '#7a3fb8', lise: '#d64545', ozel: '#3b8f9a' };
  function lgsPill(lgs) {
    if (!lgs || lgs.puan_verisi !== 'var') return '<span class="lgs-pill muted">LGS puanı yok (adrese dayalı)</span>';
    return `<span class="lgs-pill" title="LGS ${lgs.yil} taban puanı · ulusal sıra ${lgs.ulusal_sira} (%${number.format(lgs.ulusal_yuzdelik)}) · il sırası ${lgs.il_sira}">LGS ${number.format(lgs.taban_puan)} · TR ${lgs.ulusal_sira}. · il ${lgs.il_sira}.</span>`;
  }
  function kapasite(it) {
    const parts = [];
    if (it.ogrenci != null) parts.push(`${number.format(it.ogrenci)} öğr.`);
    if (it.ogretmen != null) parts.push(`${number.format(it.ogretmen)} öğrt.`);
    if (it.derslik != null) parts.push(`${number.format(it.derslik)} derslik`);
    if (it.ogrenci_derslik != null) parts.push(`${number.format(it.ogrenci_derslik)} öğr/derslik`);
    return parts.join(' · ');
  }
  function renderOkullar(result) {
    const o = result.okullar;
    const card = document.getElementById('okulCard');
    if (!o || o.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const kad = o.kademeler || {};
    const toplam = Object.values(kad).reduce((a, v) => a + (v.bulunan || 0), 0);
    setText('okulBadge', `${number.format(toplam)} okul · LGS ${o.lgs_yili || ''}`);
    const mm = miniReset('mapOkul');
    const pts = [];
    addParcelToMini(mm, result, pts);
    document.getElementById('okulGroups').innerHTML = Object.entries(kad).map(([key, v]) => {
      if (!v.en_yakin.length) return '';
      const rows = v.en_yakin.map((it) => {
        const mk = L.circleMarker([it.lat, it.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: OKUL_RENK[key], fillOpacity: 0.95 })
          .bindTooltip(`${escapeHtml(it.ad)} · ${km(it.mesafe_m)}${it.lgs && it.lgs.puan_verisi === 'var' ? ' · LGS ' + number.format(it.lgs.taban_puan) : ''}`);
        mm.layer.addLayer(mk); if (it.mesafe_m <= 4000) pts.push(L.latLngBounds([[it.lat, it.lon], [it.lat, it.lon]]));
        return `<li><div><span>${escapeHtml(it.tur_etiket)}</span><br><b>${escapeHtml(it.ad)}</b>${key === 'lise' ? '<br>' + lgsPill(it.lgs) : ''}<br><span>${escapeHtml(kapasite(it))}</span></div><small>${km(it.mesafe_m)}${it.konum_yaklasik ? '<em>≈ mahalle/köy merkezi</em>' : ''}</small></li>`;
      }).join('');
      return `<div><h4 style="color:${OKUL_RENK[key]}">${escapeHtml(v.etiket)} <small style="font-weight:400">· ${v.arama_km} km'de ${v.bulunan}</small></h4><ul>${rows}</ul></div>`;
    }).join('');
    fitMini(mm, pts);
    const best = o.en_iyi_liseler_5km || {};
    document.getElementById('okulBest').innerHTML = (best.liste || []).length
      ? `<h4>${best.yaricap_km} km'de LGS puanına göre en iyi liseler <small style="font-weight:400">(${best.puanli_lise} puanlı / ${best.toplam_lise} lise)</small></h4><ol>${best.liste.map((it) => `<li><b>${escapeHtml(it.ad)}</b> ${lgsPill(it.lgs)} <small>· ${km(it.mesafe_m)}${it.ogrenci ? ' · ' + number.format(it.ogrenci) + ' öğr.' : ''}</small></li>`).join('')}</ol>`
      : `<h4>${best.yaricap_km || 5} km'de sınavla öğrenci alan lise yok</h4>`;
    setText('okulNote', `${o.not} Kaynak: ${o.kaynak}.`);
  }

  // ---- Öğrenci yurtları (GSB/KYK + özel) ----
  function renderYurtlar(result) {
    const y = result.yurtlar;
    const card = document.getElementById('yurtCard');
    if (!y || y.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const o1 = y.ozet['1000m'], o3 = y.ozet['3000m'];
    setText('yurtBadge', `${y.en_yakin.length ? number.format(y.en_yakin.length) + '+ yurt · ' : ''}${y.arama_km} km`);
    setText('yt1Yurt', `${o1.yurt} (${o1.kyk} / ${o1.ozel})`);
    setText('yt1Kap', o1.kapasite_bilinen ? `${number.format(o1.kapasite_bilinen)} (${number.format(o1.kiz_kapasite)} / ${number.format(o1.erkek_kapasite)})${o1.kapasitesi_bilinmeyen ? ' · +' + o1.kapasitesi_bilinmeyen + ' bilinmiyor' : ''}` : (o1.yurt ? `bilinmiyor (${o1.kapasitesi_bilinmeyen} yurt)` : '—'));
    setText('yt3Yurt', `${o3.yurt} (${o3.kyk} / ${o3.ozel})`);
    setText('yt3Kap', o3.kapasite_bilinen ? `${number.format(o3.kapasite_bilinen)} (${number.format(o3.kiz_kapasite)} / ${number.format(o3.erkek_kapasite)})${o3.kapasitesi_bilinmeyen ? ' · +' + o3.kapasitesi_bilinmeyen + ' bilinmiyor' : ''}` : (o3.yurt ? `bilinmiyor (${o3.kapasitesi_bilinmeyen} yurt)` : '—'));
    const mm = miniReset('mapYurt'); const pts = []; addParcelToMini(mm, result, pts);
    document.getElementById('yurtList').innerHTML = y.en_yakin.length ? y.en_yakin.map((it) => {
      const mk = L.circleMarker([it.lat, it.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: it.kaynak === 'kyk' ? '#1f4d3a' : '#c2497a', fillOpacity: 0.95 }).bindTooltip(`${escapeHtml(it.ad)} · ${km(it.mesafe_m)}`);
      mm.layer.addLayer(mk); pts.push(L.latLngBounds([[it.lat, it.lon], [it.lat, it.lon]]));
      return `<li><div><span>${escapeHtml(it.kaynak_etiket)} · ${escapeHtml(it.tip || '')}</span><br><b>${escapeHtml(it.ad)}</b><br><span>${it.kapasite != null ? number.format(it.kapasite) + ' kişi kapasite' : 'kapasite bilinmiyor'}</span></div><small>${km(it.mesafe_m)}${it.konum_yaklasik ? '<em>≈ mahalle merkezi</em>' : ''}</small></li>`;
    }).join('') : `<li><div><b>${y.arama_km} km içinde kayıtlı yurt yok</b></div></li>`;
    fitMini(mm, pts);
    setText('yurtNote', `${y.not} Kaynak: ${y.kaynak}. Ambar: ${number.format(y.kapsam.koordinatli)}/${number.format(y.kapsam.toplam_yurt)} yurt konumlu.`);
  }

  // ---- Üniversite kampüsleri (OSM poligon + YÖK T102) ----
  function renderUniversiteler(result) {
    const u = result.universiteler;
    const card = document.getElementById('uniCard');
    if (!u || u.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    setText('uniBadge', `${u.bulunan} kampüs · ${u.arama_km} km`);
    const mm = miniReset('mapUni'); const pts = []; addParcelToMini(mm, result, pts);
    document.getElementById('uniList').innerHTML = u.kampusler.length ? u.kampusler.map((k) => {
      if (k.geometri) {
        const gl = L.geoJSON(k.geometri, { style: { color: '#2f6fd6', weight: 2, fillColor: '#8fb3f0', fillOpacity: 0.3 } }).bindTooltip(`${escapeHtml(k.ad || k.universite || 'Kampüs')}`);
        mm.layer.addLayer(gl); const b = gl.getBounds(); if (b.isValid() && k.mesafe_m <= 5000) pts.push(b);
      } else {
        mm.layer.addLayer(L.circleMarker([k.lat, k.lon], { radius: k.konum_yaklasik ? 9 : 6, color: '#fff', weight: 1.5, fillColor: k.konum_yaklasik ? '#9aa8c7' : '#2f6fd6', fillOpacity: 0.9, dashArray: k.konum_yaklasik ? '3 3' : null }).bindTooltip(escapeHtml(k.ad || k.universite || '')));
        if (k.mesafe_m <= 5000) pts.push(L.latLngBounds([[k.lat, k.lon], [k.lat, k.lon]]));
      }
      const ogr = k.ilce_ogrenci_aof_haric != null
        ? `${number.format(k.ilce_ogrenci_aof_haric)} öğrenci (AÖF hariç, ${escapeHtml(k.ilce || 'ilçe')} ilçesi${k.ilce_kampus_sayisi > 1 ? ', ' + k.ilce_kampus_sayisi + ' kampüs ortak' : ''})`
        : (k.universite ? 'ilçe öğrenci sayısı eşleşmedi' : 'üniversite eşleşmedi');
      const urap = k.urap ? `<span class="badge">URAP ${k.urap.sira}. / ${k.urap.siralanan}</span>` : (k.universite ? '<span class="badge muted">URAP sırası yok</span>' : '');
      return `<li><div><span>${escapeHtml(k.universite || 'Üniversite bilinmiyor')}${k.universite_turu ? ' · ' + escapeHtml(k.universite_turu) : ''} ${urap}</span><br><b>${escapeHtml(k.ad || 'Kampüs')}</b><br><span>${ogr}${k.alan_ha ? ' · ' + number.format(k.alan_ha) + ' ha' : ''}${k.universite_toplam_aof_haric ? ' · üni. toplam ' + number.format(k.universite_toplam_aof_haric) : ''}</span></div><small>${k.parsel_icinde ? 'kampüs içinde' : km(k.mesafe_m)}${k.konum_yaklasik ? '<em>≈ ilçe merkezi</em>' : ''}</small></li>`;
    }).join('') : `<li><div><b>${u.arama_km} km içinde kampüs yok</b></div></li>`;
    fitMini(mm, pts);
    setText('uniNote', `${u.not}${u.urap ? ' URAP sırası: ' + u.urap.kaynak + ' (yalnız sıralanan ' + (u.kampusler.find((k) => k.urap) || { urap: { siralanan: '' } }).urap.siralanan + ' üniversite; sıralanmayanlar için "sırası yok").' : ''} Kaynak: ${u.kaynak}.`);
  }

  // ---- TÜİK bölge dinamikleri ----
  function renderTuik(result) {
    const t = result.tuik_bolge;
    const card = document.getElementById('tuikCard');
    if (!t || t.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const n = t.nufus || {}, a = t.konut_arzi || {}, s = t.konut_satis || {}, ses = t.ses;
    setText('tuikBadge', `${escapeHtml(t.ilce || t.il)}${t.ilce_eslesti ? '' : ' · ilçe eşleşmedi'}`);
    const pct = (v) => v == null ? '' : ` <em class="${v >= 0 ? 'up' : 'down'}">${v >= 0 ? '+' : ''}${number.format(v)}%</em>`;
    setText('tkNufus', n.ilce_nufus != null ? `${number.format(n.ilce_nufus)} (${n.yil})` : '—');
    document.getElementById('tkNufus').innerHTML += n.ilce_nufus != null ? `<br><small>5 yıl${pct(n.degisim_5y_pct)} · 10 yıl${pct(n.degisim_10y_pct)}</small>` : '';
    setText('tkMahalle', n.mahalle && n.mahalle.nufus != null ? `${number.format(n.mahalle.nufus)} (${n.mahalle.yil})` : (n.mahalle ? 'eşleşmedi' : '—'));
    const g = (n.il_goc || [])[0];
    document.getElementById('tkGoc').innerHTML = g ? `${g.net_hiz_binde >= 0 ? '+' : ''}${number.format(Math.round(g.net_hiz_binde * 10) / 10)}‰ <small>(${g.donem}, net ${number.format(g.net)})</small>` : '—';
    document.getElementById('tkSes').innerHTML = ses ? `${number.format(ses.skor)} <small>il ${ses.il_sira}/${ses.il_ilce_sayisi} · TR ${ses.turkiye_sira}/${ses.turkiye_ilce_sayisi}</small>` : (t.il_ses_skor ? `il ${number.format(t.il_ses_skor)}` : '—');
    document.getElementById('tkRuhsat').innerHTML = a.ruhsat_daire != null ? `${number.format(a.ruhsat_daire)} <small>(${a.son_yil})</small>${a.ruhsat_5y_ortalamaya_gore_pct != null ? '<br><small>5 yıl ort. göre' + pct(a.ruhsat_5y_ortalamaya_gore_pct) + '</small>' : ''}` : '—';
    setText('tkKullanma', a.kullanma_daire != null ? `${number.format(a.kullanma_daire)} (${a.son_yil})` : '—');
    document.getElementById('tkSatis').innerHTML = s.son_12ay != null ? `${number.format(s.son_12ay)} <small>(→ ${s.son_ay})</small>${s.degisim_12ay_pct != null ? '<br><small>önceki 12 aya göre' + pct(s.degisim_12ay_pct) + '</small>' : ''}` : '—';
    setText('tkOran', (s.satis_1000_kisi_12ay != null || a.ruhsat_daire_1000_kisi != null) ? `${s.satis_1000_kisi_12ay != null ? number.format(s.satis_1000_kisi_12ay) : '—'} / ${a.ruhsat_daire_1000_kisi != null ? number.format(a.ruhsat_daire_1000_kisi) : '—'}` : '—');
    // grafikler
    const bars = (el, items, keyA, keyB, label) => {
      const max = Math.max(1, ...items.flatMap((x) => [x[keyA] || 0, keyB ? (x[keyB] || 0) : 0]));
      el.innerHTML = items.map((x, i) => {
        const h1 = Math.round((x[keyA] || 0) / max * 100);
        const lab = (i % Math.ceil(items.length / 8) === 0 || i === items.length - 1) ? `<b>${escapeHtml(label(x))}</b>` : '';
        if (!keyB) return `<div class="bar alt" style="height:${h1}%" title="${escapeHtml(label(x))}: ${number.format(x[keyA] || 0)}">${lab}</div>`;
        const h2 = Math.round((x[keyB] || 0) / max * 100);
        return `<div class="bar alt" style="height:${h1}%" title="${escapeHtml(label(x))} ruhsat: ${number.format(x[keyA] || 0)}">${lab}</div><div class="bar ikinci" style="height:${h2}%" title="${escapeHtml(label(x))} kullanma izni: ${number.format(x[keyB] || 0)}"></div>`;
      }).join('');
    };
    bars(document.getElementById('tkSatisChart'), s.seri_24ay || [], 'satis', null, (x) => x.donem);
    bars(document.getElementById('tkArzChart'), a.seri || [], 'ruhsat_daire', 'kullanma_daire', (x) => String(x.yil));
    const sb = document.getElementById('tkSesBar');
    sb.innerHTML = ses ? [['s1', ses.ust_pct, 'Üst'], ['s2', ses.ust_alti_pct, 'Üst-altı'], ['s3', ses.orta_pct, 'Orta'], ['s4', ses.alt_pct, 'Alt'], ['s5', ses.en_alt_pct, 'En alt']]
      .map(([c, v, l]) => `<span class="${c}" style="width:${v}%" title="${l}: %${number.format(v)}">${v >= 8 ? l + ' %' + Math.round(v) : ''}</span>`).join('') : '';
    const ka = a.kullanim_amaci;
    const renkA = { '11': '#2f6fd6', '121': '#7a3fb8', '122': '#1f6f4a', '123': '#3f9a6a', '124': '#9aa8c7', '125': '#d98f1f', '126': '#d9c11f', '127': '#c9ded1' };
    setText('tkAmacBaslik', ka ? `Yapı ruhsatı kullanım amacı (m², ${ka.donem}) · konut dışı %${number.format(ka.konut_disi_pay_pct)}` : 'Yapı ruhsatı kullanım amacı: veri yok');
    document.getElementById('tkAmacBar').innerHTML = ka ? ka.paylar.map((p) => `<span style="width:${p.pay_pct}%;background:${renkA[p.kod]}" title="${p.etiket}: %${number.format(p.pay_pct)} (${number.format(p.m2)} m²)">${p.pay_pct >= 7 ? p.etiket + ' %' + Math.round(p.pay_pct) : ''}</span>`).join('') : '';
    document.getElementById('tkAmacLegend').innerHTML = ka ? ka.paylar.map((p) => `<span class="hat-chip" style="border-color:${renkA[p.kod]}">${p.etiket} %${number.format(p.pay_pct)}</span>`).join('') : '';
    setText('tuikNote', `${a.not || ''} ${t.ses_not || ''} Kaynak: ${t.kaynak}.`);
  }

  // ---- Hava kalitesi (ÇŞB SİM) ----
  const KAT_ETIKET = { kentsel_donusum: 'Kentsel dönüşüm / riskli alan', sit_koruma: 'Sit / korunan alan', turizm_bolgesi: 'Turizm bölgesi', sanayi_bolgesi: 'OSB / serbest bölge / TGB', rayli_sistem: 'Raylı sistem', yol: 'Yol / köprü', enerji_hatti: 'Enerji / doğal gaz hattı', universite: 'Üniversite', liman_havalimani: 'Liman / havalimanı', maden_ruhsat: 'Maden / jeotermal / petrol', su_yapisi: 'Baraj / sulama / su', kamulastirma_diger: 'Kamulaştırma' };
  function renderHava(result) {
    const h = result.hava_kalitesi;
    const card = document.getElementById('havaCard');
    if (!h || h.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const en = h.en_yakin;
    setText('havaBadge', `${h.pm10_sinif || '—'} · ${en.mesafe_km} km`);
    const kat = (o) => o.ortalama != null ? `${number.format(o.ortalama)}${o.dso_yillik_kat ? `<br><small>DSÖ sınırının ${number.format(o.dso_yillik_kat)}×</small>` : ''}` : 'ölçüm yok';
    document.getElementById('hvPm10').innerHTML = kat(en.pm10);
    document.getElementById('hvPm25').innerHTML = kat(en.pm25);
    document.getElementById('hvNo2').innerHTML = kat(en.no2);
    document.getElementById('hvAsim').innerHTML = en.pm10.gecerli_gun ? `${en.pm10.ab_gunluk_asim_gun} / ${en.pm10.gecerli_gun} gün` : '—';
    const mm = miniReset('mapHava'); const pts = []; addParcelToMini(mm, result, pts);
    const renk = { 'yüksek': '#2f6fd6', 'orta': '#d98f1f', 'düşük': '#9aa8c7' };
    document.getElementById('havaList').innerHTML = h.istasyonlar.map((s) => {
      mm.layer.addLayer(L.circleMarker([s.lat, s.lon], { radius: 7, color: '#fff', weight: 1.5, fillColor: renk[s.temsil_gucu], fillOpacity: 0.95 }).bindTooltip(`${escapeHtml(s.ad)} · PM10 ${s.pm10.ortalama ?? '—'}`));
      pts.push(L.latLngBounds([[s.lat, s.lon], [s.lat, s.lon]]));
      return `<li><div><span>${escapeHtml(s.tip || '')} · temsil gücü ${s.temsil_gucu}</span><br><b>${escapeHtml(s.ad)}</b><br><span>PM10 ${s.pm10.ortalama ?? '—'} · PM2.5 ${s.pm25.ortalama ?? '—'} · NO₂ ${s.no2.ortalama ?? '—'} µg/m³</span></div><small>${number.format(s.mesafe_km)} km</small></li>`;
    }).join('');
    fitMini(mm, pts);
    const items = h.aylik_pm || []; const max = Math.max(1, ...items.map((x) => x.pm10 || 0));
    document.getElementById('hvChart').innerHTML = items.map((x, i) => {
      const v = x.pm10 || 0; const k = v <= 20 ? 'k-iyi' : v <= 40 ? 'k-orta' : v <= 75 ? 'k-kotu' : 'k-cokkotu';
      return `<div class="bar ${k}" style="height:${Math.round(v / max * 100)}%" title="${x.ay}: PM10 ${x.pm10 ?? '—'} (${x.n} gün)">${i % 3 === 0 ? `<b>${x.ay.slice(2)}</b>` : ''}</div>`;
    }).join('');
    setText('havaNote', `${h.not} Kaynak: ${h.kaynak}.`);
  }

  // ---- Resmî Gazete ----
  function renderRg(result) {
    const r = result.resmi_gazete;
    const card = document.getElementById('rgCard');
    if (!r || r.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    setText('rgBadge', `${r.toplam} karar · ${r.donem_ay} ay`);
    document.getElementById('rgList').innerHTML = r.kararlar.length ? r.kararlar.map((k) => {
      const kirmizi = ['kentsel_donusum', 'sit_koruma', 'maden_ruhsat'].includes(k.kategori) || k.islem;
      const islem = k.islem === 'acele_kamulastirma' ? ' · acele kamulaştırma' : (k.islem === 'kamulastirma' ? ' · kamulaştırma' : '');
      return `<li><span class="tarih">${k.tarih}<br><small>RG ${k.rg_sayi || ''}${k.karar_no ? ' · K.' + k.karar_no : ''}</small></span><div><span class="kat ${kirmizi ? 'kirmizi' : ''}">${escapeHtml(KAT_ETIKET[k.kategori] || k.kategori)}${islem}</span><br>${escapeHtml(k.baslik)}${k.yer_kaynagi === 'harita' ? ' <em>(yer: güzergâh haritası etiketi, düşük güven)</em>' : ''}</div><span class="esl ${k.eslesme === 'ilçe' ? 'ilce' : k.eslesme}">${k.eslesme === 'mahalle' ? escapeHtml(k.mahalle) + ' Mah.' : k.eslesme === 'ilçe' ? escapeHtml(k.ilce) + ' ilçesi' : 'il geneli'}</span></li>`;
    }).join('') : `<li class="bos"><b>Son ${r.donem_ay} ayda bu il/ilçeyi anan gayrimenkul kararı bulunamadı.</b></li>`;
    setText('rgNote', `${r.not} Taranan dönem: ${r.taranan ? r.taranan.ilk + ' → ' + r.taranan.son + ' (' + r.taranan.gun + ' gün)' : '—'}. Kaynak: ${r.kaynak}.`);
  }

  // ---- Uydu değişimi (Sentinel-2, asenkron) ----
  let uyduIstek = 0;
  function renderUydu(result) {
    const card = document.getElementById('uyduCard');
    const lat = result.input && result.input.lat, lon = result.input && result.input.lon;
    if (lat == null || lon == null) { card.hidden = true; return; }
    card.hidden = false;
    setText('uyduBadge', 'hesaplanıyor…'); ['uyYapili', 'uyBitki', 'uy1y', 'uy3y'].forEach((id) => setText(id, '—'));
    document.getElementById('uyduSahneler').innerHTML = '';
    setText('uyduNote', 'Copernicus Sentinel-2 sahneleri okunuyor (ilk sorguda 30–60 sn, sonraki sorgular önbellekten).');
    const istek = ++uyduIstek;
    fetch(`/api/v1/uydu/degisim?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`).then((r) => r.json()).then((u) => {
      if (istek !== uyduIstek) return;
      if (!u || u.status !== 'available') {
        setText('uyduBadge', u && u.status === 'credentials_required' ? 'anahtar yok' : (u && u.status === 'sahne_yok' ? 'bulutsuz sahne yok' : 'kullanılamıyor'));
        setText('uyduNote', (u && u.not) || 'Uydu analizi şu an kullanılamıyor.'); return;
      }
      const pct = (v) => v == null ? '—' : `<em class="${v > 0 ? 'down' : 'up'}">${v > 0 ? '+' : ''}${number.format(v)} puan</em>`;
      setText('uyduBadge', `${u.simdi.tarih} · ${u.pencere_m} m`);
      document.getElementById('uyYapili').innerHTML = `%${number.format(u.simdi.yapili_pay)}`;
      document.getElementById('uyBitki').innerHTML = `%${number.format(u.simdi.bitki_pay)}`;
      document.getElementById('uy1y').innerHTML = u.degisim_1y ? `${pct(u.degisim_1y.yapili_pay_puan)}<br><small>${u.bir_yil_once.tarih}: %${number.format(u.bir_yil_once.yapili_pay)}</small>` : 'sahne yok';
      document.getElementById('uy3y').innerHTML = u.degisim_3y ? `${pct(u.degisim_3y.yapili_pay_puan)}<br><small>${u.uc_yil_once.tarih}: %${number.format(u.uc_yil_once.yapili_pay)}</small>` : 'sahne yok';
      document.getElementById('uyduSahneler').innerHTML = [['simdi', u.simdi], ['1 yıl önce', u.bir_yil_once], ['3 yıl önce', u.uc_yil_once]].filter(([, s]) => s)
        .map(([l, s]) => `<span class="hat-chip" title="${escapeHtml(s.sahne)}">${l}: ${s.tarih} · bulut %${number.format(s.bulut || 0)} · NDVI ${number.format(s.ndvi)}</span>`).join('');
      setText('uyduNote', `${u.not} Kaynak: ${u.kaynak}. Süre ${number.format(Math.round(u.sure_ms / 100) / 10)} sn.`);
    }).catch(() => { if (istek === uyduIstek) { setText('uyduBadge', 'hata'); setText('uyduNote', 'Uydu analizi alınamadı.'); } });
  }

  // ---- İstanbul: deprem senaryosu + gürültü ----
  function renderRisk(result) {
    const d = result.deprem_senaryo, g = result.gurultu;
    const card = document.getElementById('riskCard');
    const dOk = d && d.status === 'available', gOk = g && g.status === 'available';
    if (!dOk && !gOk) { card.hidden = true; return; }
    card.hidden = false;
    setText('riskBadge', dOk ? `${d.mahalle} · İst. sırası ${d.istanbul_sira}/${d.mahalle_sayisi}` : 'mahalle eşleşmedi');
    document.getElementById('rkHasar').innerHTML = dOk && d.agir_hasar_orani != null ? `%${number.format(d.agir_hasar_orani)}<br><small>ilçe ort. %${number.format(d.ilce_ortalama)} · İstanbul %${number.format(d.istanbul_ortalama)}</small>` : (d && d.not ? escapeHtml(d.not) : '—');
    setText('rkBina', dOk ? `${number.format(d.cok_agir)} / ${number.format(d.agir)} / ${number.format(d.orta)}  (stok ${number.format(d.bina_toplam_2017 || 0)})` : '—');
    setText('rkYas', dOk && d.bina_yasi ? `<1980: ${number.format(d.bina_yasi['1980_oncesi'] || 0)} · 1980–2000: ${number.format(d.bina_yasi['1980_2000'] || 0)} · >2000: ${number.format(d.bina_yasi['2000_sonrasi'] || 0)}` : '—');
    setText('rkCan', dOk ? `${number.format(d.can_kaybi)} / ${number.format(d.gecici_barinma)}` : '—');
    const mes = (dm) => dm && Object.keys(dm).length ? Object.entries(dm).map(([s, m]) => `${s} dB: ${m} m`).join(' · ') : '';
    document.getElementById('rkGunduz').innerHTML = gOk ? `${escapeHtml(g.gunduz_sinif || '—')}<br><small>${mes((g.mesafe_m || {}).gunduz)}</small>` : (g && g.not ? escapeHtml(g.not) : '—');
    document.getElementById('rkGece').innerHTML = gOk ? `${escapeHtml(g.gece_sinif || '—')}<br><small>${mes((g.mesafe_m || {}).gece)}</small>` : '—';
    setText('riskNote', `${dOk ? d.not + ' Kaynak: ' + d.kaynak + '. ' : ''}${gOk ? g.not + ' Kaynak: ' + g.kaynak + '.' : ''}`);
  }

  // ---- İBB saatlik trafik yoğunluğu ----
  function renderTrafik(result) {
    const t = result.trafik_saatlik;
    const card = document.getElementById('trafikCard');
    if (!t || t.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    setText('trafikBadge', `hücre ${t.hucre} · ${number.format(t.hucre_mesafe_km)} km`);
    setText('trOrt', t.haftaici_ortalama_arac != null ? number.format(t.haftaici_ortalama_arac) : '—');
    document.getElementById('trGoreli').innerHTML = t.goreli_yogunluk != null ? `${number.format(t.goreli_yogunluk)}× <small>İst. ort. ${number.format(t.istanbul_ortalama_arac)} araç/saat</small>` : '—';
    setText('trZirve', t.zirve_saat != null ? `${String(t.zirve_saat).padStart(2, '0')}:00` : '—');
    setText('trYavas', t.en_yavas_saat != null ? `${String(t.en_yavas_saat).padStart(2, '0')}:00 · ${number.format(t.en_yavas_hiz_kmh)} km/sa` : '—');
    const max = Math.max(1, ...t.profil_arac.haftaici.map((v) => v || 0), ...t.profil_arac.haftasonu.map((v) => v || 0));
    const bars = (el, arr, hizArr) => { el.innerHTML = arr.map((v, h) => `<div class="bar ${h >= 7 && h <= 9 || h >= 17 && h <= 19 ? 'alt' : ''}" style="height:${Math.round((v || 0) / max * 100)}%" title="${String(h).padStart(2, '0')}:00 · ${v != null ? number.format(v) + ' araç' : 'veri yok'}${hizArr[h] != null ? ' · ' + number.format(hizArr[h]) + ' km/sa' : ''}">${h % 6 === 0 ? `<b>${String(h).padStart(2, '0')}</b>` : ''}</div>`).join(''); };
    bars(document.getElementById('trChartHi'), t.profil_arac.haftaici, t.profil_hiz_kmh.haftaici);
    bars(document.getElementById('trChartHs'), t.profil_arac.haftasonu, t.profil_hiz_kmh.haftasonu);
    setText('trafikNote', `${t.not} Hücre ${t.hucre_boyut}. Kaynak: ${t.kaynak}.`);
  }

  // ---- Taşkın / heyelan vekil göstergeleri ----
  function renderAfet(result) {
    const a = result.afet_vekili;
    const card = document.getElementById('afetCard');
    if (!a || a.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    const renk = { 'yüksek': '#d64545', 'orta': '#d98f1f', 'düşük': '#3f9a6a', 'uzak': '#3f9a6a' };
    const t = a.taskin || {}, h = a.heyelan || {};
    document.getElementById('afTaskin').innerHTML = `<span style="color:${renk[t.sinif] || 'inherit'}">${escapeHtml(t.sinif || '—')}</span>${t.gerekce ? `<br><small>${escapeHtml(t.gerekce)}</small>` : ''}`;
    setText('afDere', `${t.dere_m != null ? t.dere_m + ' m' : '2 km+'} / ${t.gol_baraj_m != null ? t.gol_baraj_m + ' m' : '—'}${t.kot_farki_m != null ? ' · tabana göre +' + number.format(t.kot_farki_m) + ' m' : ''}`);
    document.getElementById('afHeyelan').innerHTML = `<span style="color:${renk[h.sinif] || 'inherit'}">${escapeHtml(h.sinif || '—')}</span>${h.gerekce ? `<br><small>${escapeHtml(h.gerekce)}</small>` : ''}`;
    setText('afEgim', h.cevre_egim_ort_pct != null ? `%${number.format(h.cevre_egim_ort_pct)} / %${number.format(h.cevre_egim_max_pct)}` : '—');
    setText('afetNote', a.not);
  }

  // ---- Semt pazarları ----
  function renderPazarlar(result) {
    const p = result.pazarlar;
    const card = document.getElementById('pazarCard');
    if (!p || p.status !== 'available') { card.hidden = true; return; }
    card.hidden = false;
    setText('pazarBadge', `${p.sayim['3000m']} pazar · ${p.arama_km} km`);
    setText('pzSayi', `${p.sayim['1000m']} / ${p.sayim['3000m']}`);
    setText('pzKapali', `${p.sayim.kapali_3000m}`);
    const gd = Object.entries(p.gun_dagilimi || {}).sort((a, b) => b[1] - a[1]);
    setText('pzGun', gd.length ? gd.map(([g, n]) => `${g} (${n})`).join(', ') : (p.sayim['3000m'] ? 'gün bilgisi yok' : '—'));
    const mm = miniReset('mapPazar'); const pts = []; addParcelToMini(mm, result, pts);
    document.getElementById('pazarList').innerHTML = p.en_yakin.length ? p.en_yakin.map((it) => {
      mm.layer.addLayer(L.circleMarker([it.lat, it.lon], { radius: it.konum_yaklasik ? 8 : 6, color: '#fff', weight: 1.5, fillColor: it.kapali === 1 ? '#7a3fb8' : '#d98f1f', fillOpacity: it.konum_yaklasik ? 0.6 : 0.95, dashArray: it.konum_yaklasik ? '3 3' : null }).bindTooltip(`${escapeHtml(it.ad || 'Pazar')}${it.gunler.length ? ' · ' + it.gunler.join('/') : ''}`));
      pts.push(L.latLngBounds([[it.lat, it.lon], [it.lat, it.lon]]));
      return `<li><div><span>${it.kapali === 1 ? 'Kapalı pazar' : (it.kapali === 0 ? 'Açık pazar' : 'Pazar')}${it.tip ? ' · ' + escapeHtml(it.tip) : ''}${it.mahalle ? ' · ' + escapeHtml(it.mahalle) + ' Mah.' : ''}</span><br><b>${escapeHtml(it.ad || 'Pazar yeri')}</b><br><span>${it.gunler.length ? it.gunler.join(', ') : 'gün bilgisi yok'}</span></div><small>${km(it.mesafe_m)}${it.konum_yaklasik ? '<em>≈ mahalle/sokak merkezi</em>' : ''}</small></li>`;
    }).join('') : `<li><div><b>${p.arama_km} km içinde kayıtlı pazar yok</b></div></li>`;
    fitMini(mm, pts);
    setText('pazarNote', `${p.kapsam_notu} Kaynak: ${p.kaynak}.`);
  }

  // ---- 3D arazi (MapLibre GL + Terrarium terrain) ----
  let map3d = null;
  let rotateTimer = null;
  function ensureMap3d() {
    if (map3d || typeof maplibregl === 'undefined') return map3d;
    map3d = new maplibregl.Map({
      container: 'map3d',
      style: {
        version: 8,
        sources: {
          uydu: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, maxzoom: 19, attribution: 'Esri World Imagery' },
          etiket: { type: 'raster', tiles: ['https://a.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 20 },
          dem: { type: 'raster-dem', tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'], tileSize: 256, encoding: 'terrarium', maxzoom: 15, attribution: 'Terrain: Mapzen/AWS (Copernicus, SRTM)' },
          parsel: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          nokta: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
        },
        layers: [
          { id: 'uydu', type: 'raster', source: 'uydu' },
          { id: 'golge', type: 'hillshade', source: 'dem', paint: { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#1b2a1f' } },
          { id: 'etiket', type: 'raster', source: 'etiket', paint: { 'raster-opacity': 0.9 } },
          { id: 'parsel-dolgu', type: 'fill', source: 'parsel', paint: { 'fill-color': '#b6ff2e', 'fill-opacity': 0.35 } },
          { id: 'parsel-cizgi', type: 'line', source: 'parsel', paint: { 'line-color': '#e9ff5a', 'line-width': 3 } },
          { id: 'nokta', type: 'circle', source: 'nokta', paint: { 'circle-radius': 7, 'circle-color': '#e9ff5a', 'circle-stroke-color': '#0f1d14', 'circle-stroke-width': 2 } },
        ],
        terrain: { source: 'dem', exaggeration: 1.4 },
      },
      center: [35, 39], zoom: 5, pitch: 60, bearing: -20, maxPitch: 80, attributionControl: true,
    });
    map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    const setMode = (pitch) => { map3d.easeTo({ pitch, duration: 600 }); document.getElementById('btn3d').classList.toggle('active', pitch > 0); document.getElementById('btn2d').classList.toggle('active', pitch === 0); };
    document.getElementById('btn3d').addEventListener('click', () => setMode(60));
    document.getElementById('btn2d').addEventListener('click', () => setMode(0));
    document.getElementById('btnRotate').addEventListener('click', (ev) => {
      if (rotateTimer) { clearInterval(rotateTimer); rotateTimer = null; ev.currentTarget.classList.remove('active'); return; }
      ev.currentTarget.classList.add('active');
      rotateTimer = setInterval(() => map3d.rotateTo(map3d.getBearing() + 0.6, { duration: 60 }), 60);
    });
    document.getElementById('btn3d').classList.add('active');
    return map3d;
  }

  function renderArazi3d(result) {
    const a = result.arazi || {};
    const card = document.getElementById('araziCard');
    const pt = parcelPoint(result);
    if (!pt || a.status === 'disabled') { card.hidden = true; return; }
    card.hidden = false;
    const p = a.parsel_90m, c = a.cevre_300m;
    setText('araziBadge', a.status === 'available' ? `${a.kapsam === 'bolgesel' ? 'Bölgesel · ' : ''}${a.cozunurluk_m} m DEM` : (a.status === 'kaynak_erisilemedi' ? 'DEM erişilemedi' : '—'));
    setText('arRakim', a.merkez_rakim_m != null ? `${number.format(a.merkez_rakim_m)} m` : '—');
    setText('arEgim', p ? `%${number.format(p.egim_pct.ort)} (${number.format(p.egim_derece)}°) · ${p.sinif_etiket}` : '—');
    setText('arBaki', p ? `${p.baki_yon} (${p.baki_derece}°)` : '—');
    setText('arCevre', c ? `%${number.format(c.egim_pct.ort)} · ${number.format(c.rakim_fark_m)} m` : '—');
    setText('araziNote', a.status === 'available'
      ? `${a.not} Kaynak: ${a.kaynak}. 3D arazi aynı yükseklik modelinden (1.4× abartı).`
      : 'Yükseklik modeli şu an alınamadı; 3D harita yine gösterilir.');
    const m = ensureMap3d();
    if (!m) return;
    const geom = normalizedGeometry(result.parcel_evidence && result.parcel_evidence.parcel && result.parcel_evidence.parcel.geometry);
    const apply = () => {
      m.getSource('parsel').setData(geom || { type: 'FeatureCollection', features: [] });  // geom zaten Feature/FeatureCollection
      // Poligon varsa nokta gizlenir ve parsel görünür olacak kadar yaklaşılır.
      m.getSource('nokta').setData(geom ? { type: 'FeatureCollection', features: [] } : { type: 'Feature', geometry: { type: 'Point', coordinates: [pt[1], pt[0]] }, properties: {} });
      m.resize();
      m.jumpTo({ center: [pt[1], pt[0]], zoom: geom ? 17.2 : 15.5, pitch: 60, bearing: -20 });
    };
    if (m.loaded()) apply(); else m.once('load', apply);
  }

  function renderJeoloji(result) {
    const z = result.zemin_jeoloji || {};
    const card = document.getElementById('jeolojiCard');
    if (!z.status || z.status === 'disabled') { card.hidden = true; return; }
    card.hidden = false;
    const fay = ((result.geographic_context || {}).findings || []).find((f) => f.layer === 'DİRİ_FAY_HATTI');
    setText('jeoFay', fay ? `${number.format(fay.distance_m)} m${fay.ad ? ' · ' + fay.ad : ''}` : '—');
    const labels = { available: 'MTA 1/500.000', bulunamadi: 'Birim bulunamadı', kaynak_erisilemedi: 'Kaynağa erişilemedi', coordinate_required: 'Koordinat gerekli' };
    setText('jeolojiBadge', labels[z.status] || z.status);
    const mm = miniReset('mapJeoloji');
    const pts = [];
    addParcelToMini(mm, result, pts);
    if (z.status === 'available' && z.geometry) {
      // Birim poligonu (parsel çevresi penceresi) + fay hattı aynı haritada.
      const gl = L.geoJSON(z.geometry, { style: { color: z.kuvaterner ? '#d98f1f' : '#5a3d9a', weight: 2, fillColor: z.kuvaterner ? '#f2c66d' : '#a58be0', fillOpacity: 0.28 } });
      gl.bindTooltip(`${z.simge || ''} · ${z.aciklama || ''}`, { sticky: true });
      mm.layer.addLayer(gl);
      const gb = gl.getBounds(); if (gb.isValid()) pts.push(gb);
    }
    if (fay && fay.geometry) mm.layer.addLayer(L.geoJSON(fay.geometry, { style: { color: '#e03c3c', weight: 3, dashArray: '6 4' } }));
    fitMini(mm, pts);
    if (z.status === 'available') {
      setText('jeoBirim', z.aciklama || '—');
      setText('jeoYas', z.yas || '—');
      setText('jeoSimge', `${z.simge || '—'}${z.kod ? ' / ' + z.kod : ''}`);
      setText('jeolojiNote', `${z.kuvaterner ? 'Kuvaterner (genç/gevşek çökel) birim: zemin etüdü ve sıvılaşma değerlendirmesi özellikle önemlidir. ' : ''}${z.not} Kaynak: ${z.kaynak}. Sorgu anında canlı alınır; ${z.geometry_note || 'poligon saklanmaz.'}`);
    } else {
      setText('jeoBirim', '—'); setText('jeoYas', '—'); setText('jeoSimge', '—');
      setText('jeolojiNote', z.status === 'kaynak_erisilemedi'
        ? 'MTA Yerbilimleri Portalı yanıt vermedi; sonraki sorguda yeniden denenir. Fay mesafesi kendi ambarımızdan gelir.'
        : (z.status === 'bulunamadi' ? 'Bu noktada 1/500.000 haritada jeolojik birim kaydı yok (deniz/göl veya harita boşluğu olabilir).' : 'Jeolojik birim için parsel koordinatı gerekli.'));
    }
  }

  function renderFactors(result) {
    const gc = result.geographic_context || {};
    const find = (layer) => (gc.findings || []).find((f) => f.layer === layer);
    const ps = result.parcel_shape;
    const dz = result.declared_zoning;
    const ce = result.city_expansion;
    const su = find('SU');
    const fay = find('DİRİ_FAY_HATTI');
    const fields = (result.parcel_evidence && result.parcel_evidence.zoning && result.parcel_evidence.zoning.fields) || {};
    const zoneLabel = (v) => String(v == null ? '' : v).replace(/\\u0026/g, '&');
    const rows = [];
    rows.push({ label: 'Parsel şekli', value: ps ? ps.label : 'Geometri gerekli', note: ps ? `dikdörtgensellik %${number.format(ps.rectangularity * 100)} · ${ps.vertices} köşe · ${number.format(ps.area_m2)} m²` : 'Canlı sorgu ile hesaplanır' });
    if (fields.plan_fonksiyon || fields.imar_durumu) rows.push({ label: 'İmar durumu', value: fields.plan_fonksiyon || fields.imar_durumu, note: 'Canlı E-Plan' });
    else if (dz && dz.dominant) rows.push({ label: 'İmar durumu', value: zoneLabel(dz.dominant), note: 'İlan beyanı' });
    else rows.push({ label: 'İmar durumu', value: '—', note: 'Veri yok' });
    rows.push({ label: 'Suya yakınlık', value: su ? `${number.format(su.count)} kaynak (2 km)` : 'Yakın kaynak yok', note: su ? `en yakın ~${number.format(su.distance_m)} m` : '2 km içinde yok' });
    rows.push({ label: 'Fay hattı', value: fay ? `~${number.format(fay.distance_m)} m` : 'Yok', note: fay ? (fay.relation === 'over' ? 'parsel üzerinde' : 'en yakın diri fay') : 'yakında diri fay yok' });
    if (ce && ce.status === 'available') {
      const up = (ce.upper_scale_plans || [])[0];
      rows.push({ label: 'Şehir genişleme', value: ce.is_expansion_area ? 'Gelişme işareti var' : (up ? up.plan_turu : 'İşaret yok'), note: 'Canlı E-Plan üst ölçek plan' });
    }
    rows.push({ label: 'Yol', value: 'Değerlendiriliyor', note: 'Daha sağlam kaynak + uydu çıkarımı ile eklenecek' });
    rows.push({ label: 'Zemin durumu', value: '—', note: 'Zemin etüdü kaynağı henüz bağlı değil' });
    document.getElementById('factorList').innerHTML = rows.map((r) => `
      <div class="factor"><span>${escapeHtml(r.label)}<small>${escapeHtml(r.note)}</small></span><strong>${escapeHtml(String(r.value))}</strong></div>`).join('');
    setText('factorTotal', 'Gerçek veriden');
  }

  function renderZoning(result) {
    const evidence = result.parcel_evidence;
    const parcel = evidence && evidence.parcel;
    const zoning = evidence && evidence.zoning;
    setText('cadastreStatus', statusLabel(evidence && evidence.cadastre && evidence.cadastre.status));
    setText('parcelIdentity', parcel ? `${parcel.block || '—'} / ${parcel.parcel || '—'}` : '—');
    setText('parcelArea', parcel && parcel.area_m2 ? `${number.format(parcel.area_m2)} m²` : `${number.format(result.input.alan_m2)} m² (girdi)`);
    setText('parcelQuality', parcel && parcel.quality);
    setText('groundTitleStatus', parcel && parcel.ground_title_status);
    // Parsel şekli: geometriden hesaplanır.
    const ps = result.parcel_shape;
    setText('parcelShape', ps
      ? `${ps.label} · ${number.format(ps.area_m2)} m² · ${ps.vertices} köşe (hesaplandı)`
      : 'Parsel geometrisi gerekli (canlı sorgu)');
    // Şehir genişleme / üst ölçek plan.
    const ce = result.city_expansion;
    const ceEl = document.getElementById('cityExpansion');
    if (ce && ce.status === 'available') {
      if (ce.is_expansion_area) {
        ceEl.textContent = `Şehir genişleme: gelişme/genişleme işareti bulundu — ${ce.expansion_signals.slice(0, 2).join(' · ')}`;
      } else {
        const ups = (ce.upper_scale_plans || []).map((p) => `${p.plan_turu}${p.olcek ? ' (1/' + p.olcek + ')' : ''}`);
        ceEl.textContent = ups.length ? `Üst ölçek plan: ${ups.join(' · ')}` : 'Bu noktada üst ölçek gelişme planı işareti yok.';
      }
    } else if (ce) {
      ceEl.textContent = 'Şehir genişleme / üst ölçek plan: TKGM + E-Plan canlı sorgusu ile gelir.';
    } else {
      ceEl.textContent = '';
    }
    const fields = (zoning && zoning.fields) || {};
    const declared = result.declared_zoning;
    const zoneLabel = (value) => String(value == null ? '' : value).replace(/\\u0026/g, '&').replace(/&amp;/g, '&');
    const hasLivePlan = Boolean(fields.plan_fonksiyon || fields.imar_durumu);
    // İmar durumu: canlı E-Plan doğruladıysa onu, yoksa ilan beyanını göster.
    if (hasLivePlan) {
      setText('planFunction', fields.plan_fonksiyon || fields.imar_durumu);
    } else if (declared && declared.dominant) {
      setText('planFunction', `${zoneLabel(declared.dominant)} (ilan beyanı)`);
    } else {
      setText('planFunction', '—');
    }
    // KAKS/TAKS ve kat izni sayısal değerleri yalnızca resmi kaynaktan gelir; uydurulmaz.
    setText('zoningRatios', (fields.kaks_emsal || fields.taks) ? `${fields.kaks_emsal || '—'} / ${fields.taks || '—'}` : 'Resmi plandan sorgulanmalı');
    setText('heightAndFloors', (fields.kat_adedi || fields.gabari) ? `${fields.kat_adedi ? number.format(fields.kat_adedi) + ' kat' : '—'} / ${fields.gabari ? number.format(fields.gabari) + ' m' : '—'}` : 'Resmi plandan sorgulanmalı');
    setText('setbacks', fields.on_bahce || fields.yan_bahce ? `${fields.on_bahce ? number.format(fields.on_bahce) + ' m' : '—'} / ${fields.yan_bahce ? number.format(fields.yan_bahce) + ' m' : '—'}` : '—');
    setText('planTypeScale', fields.plan_turu || fields.plan_olcegi ? `${fields.plan_turu || '—'} / ${fields.plan_olcegi ? '1/' + fields.plan_olcegi : '—'}` : '—');
    setText('planPinStatus', fields.pin_tucbs_no || fields.plan_sureci ? `${fields.pin_tucbs_no || '—'} / ${fields.plan_sureci || '—'}` : '—');
    setText('planDate', fields.plan_adi || fields.plan_kayit_tarihi ? `${fields.plan_adi || '—'} · ${fields.plan_kayit_tarihi ? String(fields.plan_kayit_tarihi).slice(0, 10) : '—'}` : '—');
    const badge = document.getElementById('zoningBadge');
    const liveVerified = zoning && ['verified_at_source', 'partial_zoning_verified', 'plan_coverage_verified'].includes(zoning.status);
    if (liveVerified) {
      badge.textContent = statusLabel(zoning.status); badge.className = 'badge good';
    } else if (declared && declared.dominant) {
      badge.textContent = 'İlan beyanı'; badge.className = 'badge muted';
    } else {
      badge.textContent = statusLabel(zoning && zoning.status); badge.className = 'badge muted';
    }
    let warnText = result.warnings.find((warning) => /İmar|E-Plan|KAKS|TAKS/i.test(warning));
    if (!warnText && !hasLivePlan && declared && declared.distribution && declared.distribution.length) {
      const dist = declared.distribution.slice(0, 3).map((d) => `${zoneLabel(d.label)} %${Math.round(d.share * 100)}`).join(' · ');
      const scopeLabel = declared.scope === 'mahalle' ? 'mahallede' : (declared.scope === 'ilce' ? 'ilçede' : 'ilde');
      warnText = `İmar durumu ilan beyanıdır (resmi plan doğrulanmadı). Bu ${scopeLabel} beyan dağılımı: ${dist} · ${number.format(declared.sample_size)} ilan. KAKS/TAKS ve kat izni TKGM/E-Plan'dan doğrulanmalıdır.`;
    }
    setText('zoningWarning', warnText || 'Kaynakta gelen alanlar değiştirilmeden gösteriliyor.');

    const planList = document.getElementById('eplanPlans');
    const plans = (zoning && zoning.plans) || [];
    planList.hidden = !plans.length;
    planList.innerHTML = plans.slice(0, 6).map((plan) => `
      <div class="eplan-plan">
        <strong>${escapeHtml(plan.plan_adi || 'Adsız plan')}</strong>
        <small>${escapeHtml([plan.plan_turu, plan.olcek ? '1/' + plan.olcek : null, plan.pin_tucbs_no, plan.onay_durumu].filter(Boolean).join(' · '))}</small>
      </div>`).join('');

    const project = result.example_project;
    document.getElementById('projectEmpty').hidden = Boolean(project);
    document.getElementById('projectValues').hidden = !project;
    if (project) {
      setText('grossConstruction', `${number.format(project.total_gross_construction_m2)} m²`);
      setText('footprint', `${number.format(project.max_footprint_m2)} m²`);
      setText('openArea', `${number.format(project.open_area_m2)} m²`);
      setText('floorCount', `${project.indicative_floor_count} kat`);
    } else {
      const ctx = (!hasLivePlan && declared && declared.dominant)
        ? `İmar durumu (ilan beyanı): <strong>${escapeHtml(zoneLabel(declared.dominant))}</strong>. `
        : '';
      document.getElementById('projectEmpty').innerHTML =
        `<strong>Yapı kapasitesi doğrulanmış KAKS/TAKS ister</strong><p>${ctx}Kesin KAKS/TAKS ve kat izni TKGM/E-Plan'dan doğrulanınca örnek proje burada hesaplanır; uydurma değer üretilmez.</p>`;
    }
  }

  function renderResult(result) {
    const index = result.market_index;
    const comparables = result.comparable_statistics || {};
    setText('resultTitle', `${result.input.il}${result.input.ilce ? ' / ' + result.input.ilce : ''} arsa analizi`);
    setText('analysisMeta', `${result.model_version} · ${result.elapsed_ms || 0} ms · ${result.analysis_id}`);
    setText('priceRange', index && index.min_m2_fiyat != null ? `${money.format(index.min_m2_fiyat)} – ${money.format(index.max_m2_fiyat)}` : 'Endeks bulunamadı');
    setText('priceRangeNote', index ? `${index.donem || 'Dönem yok'} · ${number.format(index.ilan_sayisi || 0)} kayıt` : 'Bu bölgede mahalle endeksi yok');
    setText('priceEstimate', index && index.satilik_m2_fiyat != null ? `${money.format(index.satilik_m2_fiyat)} / m²` : '—');
    setText('unitEstimate', index ? `Bağımsız ${index.match_scope || 'bölge'} endeksi` : 'Endeks verisi yok');
    setText('confidence', comparables.median_unit_price == null ? '—' : `${money.format(comparables.median_unit_price)} / m²`);
    setText('confidenceLabel', comparables.low_unit_price == null ? 'Emsal bulunamadı' : `${money.format(comparables.low_unit_price)} – ${money.format(comparables.high_unit_price)}`);
    const selection = result.comparable_selection;
    setText('comparableCount', selection.selected_count);
    setText('comparableScope', selection.scope === '1_km_yaricap' ? '1 km yarıçap' : selection.scope.replaceAll('_', ' '));
    setText('methodBadge', selection.scope === 'koordinat_gerekli'
      ? 'Koordinat gerekli'
      : `${selection.selected_count} ilan · 1 km`);
    resultAlert.className = 'alert';
    resultAlert.textContent = result.warnings.join(' ');
    const inLat = Number(result.input.lat), inLon = Number(result.input.lon);
    lastParcelCenter = (Number.isFinite(inLat) && Number.isFinite(inLon)) ? { lat: inLat, lon: inLon } : null;
    renderComparables(result);
    renderIndexHistory(result);
    renderRegionDynamics(result);
    renderBolgeProfili(result);
    renderAreaSegments(result);
    renderEmsalMap(result);
    renderDensity(result);
    renderSuMap(result);
    renderFayMap(result);
    renderElektrikMap(result);
    renderGeographic(result);
    renderJeoloji(result);
    renderArazi3d(result);
    renderYakin(result);
    renderOkullar(result);
    renderYurtlar(result);
    renderUniversiteler(result);
    renderPazarlar(result);
    renderTuik(result);
    renderHava(result);
    renderRg(result);
    renderUydu(result);
    renderRisk(result);
    renderTrafik(result);
    renderAfet(result);
    renderFactors(result);
    renderZoning(result);
    updateMap(result);
  }

  function renderIndexHistory(result) {
    const card = document.getElementById('indexHistoryCard');
    const mi = result.market_index;
    const history = (mi && mi.history) || [];
    const points = history.filter((p) => Number.isFinite(Number(p.unit_price)));
    if (!mi || points.length < 2) { card.hidden = true; return; }
    card.hidden = false;

    const W = 720, H = 240, padL = 58, padR = 16, padT = 16, padB = 30;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const values = points.map((p) => Number(p.unit_price));
    const minV = Math.min(...values), maxV = Math.max(...values);
    const span = (maxV - minV) || 1;
    const x = (i) => padL + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
    const y = (v) => padT + innerH - ((v - minV) / span) * innerH;

    const firstProjIdx = points.findIndex((p) => p.is_projection);
    const splitIdx = firstProjIdx === -1 ? points.length : firstProjIdx;
    const toPath = (offset, slice) => slice.map((p, k) => `${k === 0 ? 'M' : 'L'} ${x(offset + k).toFixed(1)} ${y(Number(p.unit_price)).toFixed(1)}`).join(' ');
    const observedPts = points.slice(0, splitIdx);
    const projOffset = splitIdx > 0 ? splitIdx - 1 : 0;
    const projectionPts = points.slice(projOffset);

    let grid = '';
    const gridCount = 4;
    for (let g = 0; g <= gridCount; g++) {
      const val = minV + (span * g) / gridCount;
      const gy = y(val);
      grid += `<line x1="${padL}" y1="${gy.toFixed(1)}" x2="${W - padR}" y2="${gy.toFixed(1)}" class="hc-grid"/>`;
      grid += `<text x="${padL - 8}" y="${(gy + 3.5).toFixed(1)}" class="hc-ylabel" text-anchor="end">${compact.format(Math.round(val))}</text>`;
    }

    const labelSet = new Set([0]);
    if (splitIdx > 0 && splitIdx < points.length) labelSet.add(splitIdx - 1);
    labelSet.add(points.length - 1);
    const xLabels = [...labelSet].map((i) => {
      const anchor = i === 0 ? 'start' : (i === points.length - 1 ? 'end' : 'middle');
      return `<text x="${x(i).toFixed(1)}" y="${H - 8}" class="hc-xlabel" text-anchor="${anchor}">${escapeHtml(points[i].period)}</text>`;
    }).join('');

    let divider = '';
    if (splitIdx > 0 && splitIdx < points.length) {
      const dx = x(splitIdx - 0.5).toFixed(1);
      divider = `<line x1="${dx}" y1="${padT}" x2="${dx}" y2="${padT + innerH}" class="hc-divider"/>`;
    }

    const dots = points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(Number(p.unit_price)).toFixed(1)}" r="2.4" class="hc-dot ${p.is_projection ? 'proj' : 'obs'}"><title>${escapeHtml(p.period)}: ${money.format(Math.round(Number(p.unit_price)))} / m²${p.is_projection ? ' (tahmin)' : ''}</title></circle>`).join('');

    document.getElementById('indexHistoryChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="hc-svg" role="img">
      ${grid}${divider}
      <path d="${toPath(0, observedPts)}" class="hc-line obs"/>
      ${projectionPts.length > 1 ? `<path d="${toPath(projOffset, projectionPts)}" class="hc-line proj"/>` : ''}
      ${dots}${xLabels}
    </svg>`;

    const s = mi.history_summary || {};
    const scopeLabel = mi.match_scope === 'mahalle' ? 'mahalle endeksi' : 'bölge temsilci endeksi';
    setText('historyBadge', `${result.input.mahalle || mi.mahalle || '—'} · ${scopeLabel}`);
    document.getElementById('historyRange').textContent =
      `${s.first_period || points[0].period} → ${s.last_period || points[points.length - 1].period} · ${s.observed_count || observedPts.length} gözlenen + ${s.projection_count || 0} projeksiyon`;
  }

  function setMode(mode) {
    document.querySelectorAll('.mode-switch button').forEach((item) => item.classList.toggle('active', item.dataset.mode === mode));
    document.querySelectorAll('.parcel-fields').forEach((item) => { item.hidden = mode === 'coordinate'; });
  }

  function setFieldValue(name, value) {
    const el = form.elements[name];
    if (el && value != null && value !== '') el.value = value;
  }

  function fillFormFromParcel(p, fallbackLat, fallbackLon) {
    setFieldValue('il', p.il);
    setFieldValue('ilce', p.ilce);
    setFieldValue('mahalle', p.mahalle);
    setFieldValue('ada', p.ada_no);
    setFieldValue('parsel', p.parsel_no);
    setFieldValue('mahalleId', p.mahalle_id);
    if (Number.isFinite(Number(p.alan_m2))) setFieldValue('alan_m2', Math.round(Number(p.alan_m2)));
    setFieldValue('lat', p.enlem != null ? p.enlem : fallbackLat);
    setFieldValue('lon', p.boylam != null ? p.boylam : fallbackLon);
    form.elements.canli_parsel_sorgula.checked = true;
    setMode('parcel');
  }

  async function selectParcelAt(lat, lon) {
    if (clickMarker) map.removeLayer(clickMarker);
    clickMarker = L.circleMarker([lat, lon], { radius: 8, color: '#d9f24f', fillColor: '#0f6b4f', fillOpacity: .9, weight: 3 }).addTo(map);
    const mapStatus = document.getElementById('mapStatus');
    mapStatus.innerHTML = '<i></i> Parsel sorgulanıyor…';
    try {
      const resp = await fetch(`/api/parsel-sorgu?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
      const data = await resp.json();
      if (resp.ok && data.status === 'success' && data.data && data.data.parsel) {
        const p = data.data.parsel;
        mapStatus.innerHTML = `<i></i> Seçildi: Ada ${p.ada_no || '—'} / Parsel ${p.parsel_no || '—'}`;
        fillFormFromParcel(p, lat, lon);
      } else {
        // Tescilli parsel yoksa yine de tıklanan koordinatla analiz sürer.
        mapStatus.innerHTML = '<i></i> Parsel bulunamadı; koordinatla analiz ediliyor';
        setFieldValue('lat', lat);
        setFieldValue('lon', lon);
        setMode('coordinate');
      }
      runAnalysis(formPayload());
    } catch (error) {
      mapStatus.innerHTML = '<i></i> Parsel sorgusunda bağlantı hatası';
    }
  }

  function renderAreaSegments(result) {
    const card = document.getElementById('areaSegmentCard');
    const data = result.area_price_segments || {};
    const segments = data.segments || [];
    if (!segments.length) { card.hidden = true; return; }
    card.hidden = false;
    setText('segmentBadge', `${data.region || '—'}${data.target_area_m2 ? ' · ' + number.format(data.target_area_m2) + ' m²' : ''}`);
    document.getElementById('segmentRows').innerHTML = segments.map((s) => `
      <tr${s.matches_input ? ' class="is-match"' : ''}>
        <td><strong>${escapeHtml(s.band)}</strong>${s.matches_input ? ' <span class="match-tag">bu arsa</span>' : ''}</td>
        <td>${s.unit_price == null ? '—' : money.format(s.unit_price) + ' / m²'}</td>
        <td>${s.min_unit_price == null ? '—' : money.format(s.min_unit_price) + ' – ' + money.format(s.max_unit_price)}</td>
        <td>${s.listing_share == null ? '—' : '%' + number.format(s.listing_share * 100)}${s.listing_count != null ? ` <small>(${number.format(s.listing_count)})</small>` : ''}</td>
        <td>${s.avg_area_m2 == null ? '—' : number.format(s.avg_area_m2) + ' m²'}</td>
        <td>${s.days_on_market == null ? '—' : number.format(s.days_on_market) + ' gün'}</td>
      </tr>`).join('');
  }

  function renderEmsalMap(result) {
    const mm = miniReset('mapEmsal');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const sel = result.comparable_selection;
    (sel.comparables || []).forEach((c) => {
      if (Number.isFinite(c.lat) && Number.isFinite(c.lon)) {
        mm.layer.addLayer(L.circleMarker([c.lat, c.lon], { radius: 6, color: '#fffef9', fillColor: '#d58b25', fillOpacity: .95, weight: 2 })
          .bindTooltip(`${money.format(c.unit_price_tl)} / m²`));
        pts.push(L.latLngBounds([[c.lat, c.lon]]));
      }
    });
    fitMini(mm, pts);
    setText('emsalSummary', sel.scope === 'koordinat_gerekli' ? 'Koordinat gerekli' : `${sel.selected_count} ilan · 1 km yarıçap`);
  }

  function renderDensity(result) {
    const d = result.transaction_density;
    const mm = miniReset('mapDensity');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const bars = document.getElementById('densityYearBars');
    if (!d || d.status !== 'available' || !d.points || !d.points.length) {
      setText('densitySummary', d && d.status === 'coordinate_required' ? 'Koordinat gerekli' : 'Bu bölgede satış kaydı yok');
      bars.innerHTML = '';
      fitMini(mm, pts);
      return;
    }
    const cluster = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 48, iconCreateFunction: densityClusterIcon });
    const capped = d.points.slice(0, 2000);
    cluster.addLayers(capped.map((p) => L.circleMarker([p.lat, p.lon], { radius: 4, sayi: p.count, color: '#6b1f8a', weight: 1, fillColor: '#b24dd6', fillOpacity: .85 })));
    mm.layer.addLayer(cluster);
    capped.slice(0, 60).forEach((p) => pts.push(L.latLngBounds([[p.lat, p.lon]])));
    fitMini(mm, pts);
    setText('densitySummary', `${number.format(d.total_transactions)} işlem · ${number.format(d.unique_parcels)} parsel · ${d.year_start}-${d.year_end}`);
    const maxY = Math.max(...d.by_year.map((y) => y.transactions), 1);
    bars.innerHTML = d.by_year.map((y) => `<div class="dbar"><span class="dbar-year">${y.year}</span><span class="dbar-track"><i style="width:${Math.round(y.transactions / maxY * 100)}%"></i></span><span class="dbar-val">${number.format(y.transactions)}</span></div>`).join('');
  }

  function renderSuMap(result) {
    const gc = result.geographic_context || {};
    const mm = miniReset('mapSu');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const su = findingByLayer(gc, 'SU');
    if (su && su.items) su.items.forEach((w) => {
      if (Number.isFinite(w.lat) && Number.isFinite(w.lon)) {
        mm.layer.addLayer(L.circleMarker([w.lat, w.lon], { radius: 5, color: '#0b5cad', fillColor: '#4aa3e6', fillOpacity: .9, weight: 1.5 })
          .bindTooltip(`${w.label} ~${number.format(w.distance_m)} m`));
        pts.push(L.latLngBounds([[w.lat, w.lon]]));
      }
    });
    const dere = findingByLayer(gc, 'SU_YOLU_DERE');
    if (dere && dere.geometry) mm.layer.addLayer(L.geoJSON(dere.geometry, { style: { color: '#2f8fd6', weight: 3 } }));
    fitMini(mm, pts);
    setText('suSummary', su ? su.message : (gc.status === 'available' ? '2 km içinde su kaynağı bulunmadı' : 'Koordinat gerekli'));
  }

  function renderFayMap(result) {
    const gc = result.geographic_context || {};
    const mm = miniReset('mapFay');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const fay = findingByLayer(gc, 'DİRİ_FAY_HATTI');
    if (fay && fay.geometry) {
      const gl = L.geoJSON(fay.geometry, { style: { color: '#e03c3c', weight: 4 } });
      mm.layer.addLayer(gl);
      const b = gl.getBounds(); if (b.isValid()) pts.push(b);
    }
    fitMini(mm, pts);
    setText('fauSummary', fay ? fay.message : (gc.status === 'available' ? 'Yakında diri fay hattı yok' : 'Koordinat gerekli'));
  }

  function renderElektrikMap(result) {
    const gc = result.geographic_context || {};
    const mm = miniReset('mapElektrik');
    const pts = [];
    addParcelToMini(mm, result, pts);
    const el = findingByLayer(gc, 'ELEKTRIK_HATTI');
    const card = document.getElementById('focusElektrik');
    if (el && el.geometry) {
      const gl = L.geoJSON(el.geometry, { style: { color: '#f0a500', weight: 4 } });
      mm.layer.addLayer(gl);
      const b = gl.getBounds(); if (b.isValid()) pts.push(b);
      card.classList.add('risk-focus');
      setText('elektrikSummary', '⚠️ Parsel üzerinden gerilim hattı geçiyor');
    } else {
      card.classList.remove('risk-focus');
      setText('elektrikSummary', gc.status === 'available' ? '✓ Parsel üzerinden hat geçmiyor' : 'Koordinat gerekli');
    }
    fitMini(mm, pts);
  }

  function renderGeographic(result) {
    const card = document.getElementById('geoFindingsCard');
    const gc = result.geographic_context;
    const exclude = new Set(['SU', 'DİRİ_FAY_HATTI', 'ELEKTRIK_HATTI', 'SU_YOLU_DERE']);
    const findings = ((gc && gc.findings) || []).filter((f) => !exclude.has(f.layer));
    if (!gc || gc.status !== 'available' || !findings.length) { card.hidden = true; return; }
    card.hidden = false;
    const sev = { risk: { cls: 'risk', ic: '⚠️' }, warn: { cls: 'warn', ic: '🟡' }, info: { cls: 'info', ic: 'ℹ️' } };
    setText('geoBadge', gc.parcel_geometry_used ? 'Parsel geometrisiyle' : 'Koordinatla (yaklaşık)');
    document.getElementById('geoFindings').innerHTML = findings.map((f) => {
      const m = sev[f.severity] || sev.info;
      const rel = f.relation === 'over' ? 'Üzerinde' : (f.relation === 'adjacent' ? 'Bitişik' : (f.distance_m != null ? '~' + number.format(f.distance_m) + ' m' : '—'));
      return `<div class="geo-item ${m.cls}"><span class="geo-ic">${m.ic}</span><div class="geo-txt"><strong>${escapeHtml(f.label)}</strong><small>${escapeHtml(f.message)}</small></div><span class="geo-rel">${rel}</span></div>`;
    }).join('');
    document.getElementById('geoNote').textContent = (gc.notes || []).join(' ');
  }

  async function runAnalysis(payload) {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/arsa/analiz', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok || result.status !== 'success') throw new Error(result.message || 'Analiz tamamlanamadı.');
      renderResult(result);
      document.getElementById('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      formError.hidden = false;
      formError.textContent = error.message || 'Analiz sırasında hata oluştu.';
    } finally {
      setLoading(false);
    }
  }

  form.addEventListener('submit', (event) => { event.preventDefault(); runAnalysis(formPayload()); });
  sampleButton.addEventListener('click', () => {
    form.elements.canli_parsel_sorgula.checked = true;
    form.elements.sekil.value = 'regular';
    form.elements.yol.value = 'cadastral_frontage';
    form.elements.su.value = 'nearby';
    form.elements.gelisim.value = 'toward_growth';
    form.elements.imar.value = 'unknown';
    runAnalysis(formPayload());
  });
  document.querySelectorAll('.mode-switch button').forEach((button) => {
    button.addEventListener('click', () => setMode(button.dataset.mode));
  });
  document.getElementById('fitMap').addEventListener('click', () => { if (lastBounds) map.fitBounds(lastBounds.pad(.18), { maxZoom: 16 }); });

  // ---- Uydu geçmişi galerisi: Esri World Imagery Wayback (yıllara göre foto) ----
  // Ayrı pencere; harita değil, parsel merkezli statik yüksek çözünürlük fotoğraflar.
  const WAYBACK = {
    2014: 5844, 2015: 28163, 2016: 18966, 2017: 25521, 2018: 23448, 2019: 4756,
    2020: 29260, 2021: 26120, 2022: 45134, 2023: 56102, 2024: 16453, 2025: 13192, 2026: 26334,
  };
  const SAT_YEARS = Object.keys(WAYBACK).map(Number);
  const SAT_ZOOM = 17;
  const waybackUrl = (rel, z, x, y) => `https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/${rel}/${z}/${y}/${x}`;
  const satGallery = document.getElementById('satGallery');
  const satPhoto = document.getElementById('satPhoto');
  const satGallerySlider = document.getElementById('satGallerySlider');
  let satGalleryTimer = null;

  function lonLatToTile(lon, lat, z) {
    const n = Math.pow(2, z);
    const latRad = lat * Math.PI / 180;
    return {
      x: (lon + 180) / 360 * n,
      y: (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n,
    };
  }

  function composePhoto(canvas, release, lat, lon) {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0b1512';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const { x, y } = lonLatToTile(lon, lat, SAT_ZOOM);
    const cx = canvas.width / 2, cy = canvas.height / 2;
    const fx = x - Math.floor(x), fy = y - Math.floor(y);
    const spanX = Math.ceil(canvas.width / 512) + 1;
    const spanY = Math.ceil(canvas.height / 512) + 1;
    for (let dx = -spanX; dx <= spanX; dx++) {
      for (let dy = -spanY; dy <= spanY; dy++) {
        const px = Math.round(cx + (dx - fx) * 256);
        const py = Math.round(cy + (dy - fy) * 256);
        const img = new Image();
        img.onload = () => { if (canvas.dataset.release === String(release)) ctx.drawImage(img, px, py); };
        img.src = waybackUrl(release, SAT_ZOOM, Math.floor(x) + dx, Math.floor(y) + dy);
      }
    }
  }

  function renderGalleryPhoto() {
    const idx = Math.min(Number(satGallerySlider.value), SAT_YEARS.length - 1);
    const year = SAT_YEARS[idx];
    satPhoto.dataset.release = String(WAYBACK[year]);
    setText('satGalleryYear', year);
    setText('satPhotoYear', year);
    if (lastParcelCenter) composePhoto(satPhoto, WAYBACK[year], lastParcelCenter.lat, lastParcelCenter.lon);
    document.querySelectorAll('#satStrip .sat-thumb').forEach((t, i) => t.classList.toggle('active', i === idx));
  }

  function buildStrip() {
    const strip = document.getElementById('satStrip');
    strip.innerHTML = SAT_YEARS.map((y, i) => `<button type="button" class="sat-thumb" data-idx="${i}">${y}</button>`).join('');
    strip.querySelectorAll('.sat-thumb').forEach((b) => b.addEventListener('click', () => {
      stopGalleryPlay(); satGallerySlider.value = b.dataset.idx; renderGalleryPhoto();
    }));
  }

  function stopGalleryPlay() {
    if (satGalleryTimer) { clearInterval(satGalleryTimer); satGalleryTimer = null; setText('satGalleryPlay', '▶ Oynat'); }
  }
  function toggleGalleryPlay() {
    if (satGalleryTimer) { stopGalleryPlay(); return; }
    setText('satGalleryPlay', '⏸ Durdur');
    satGalleryTimer = setInterval(() => {
      let v = Number(satGallerySlider.value) + 1;
      if (v > SAT_YEARS.length - 1) v = 0;
      satGallerySlider.value = v; renderGalleryPhoto();
    }, 1100);
  }
  function stepGallery(delta) {
    stopGalleryPlay();
    const v = Number(satGallerySlider.value) + delta;
    satGallerySlider.value = Math.max(0, Math.min(SAT_YEARS.length - 1, v));
    renderGalleryPhoto();
  }
  function openSatGallery() {
    if (!lastParcelCenter) { alert('Önce bir arsa analizi yapın (parsel konumu gerekli).'); return; }
    satGallery.hidden = false;
    setText('satGalleryLoc', `${lastParcelCenter.lat.toFixed(5)}, ${lastParcelCenter.lon.toFixed(5)}`);
    satGallerySlider.max = SAT_YEARS.length - 1;
    satGallerySlider.value = SAT_YEARS.length - 1;
    buildStrip();
    renderGalleryPhoto();
  }
  function closeSatGallery() { satGallery.hidden = true; stopGalleryPlay(); }

  document.getElementById('satToggle').addEventListener('click', openSatGallery);
  document.getElementById('satOpen').addEventListener('click', openSatGallery);
  document.getElementById('satGalleryClose').addEventListener('click', closeSatGallery);
  satGallery.addEventListener('click', (e) => { if (e.target === satGallery) closeSatGallery(); });
  satGallerySlider.addEventListener('input', () => { stopGalleryPlay(); renderGalleryPhoto(); });
  document.getElementById('satGalleryPlay').addEventListener('click', toggleGalleryPlay);
  document.getElementById('satPrev').addEventListener('click', () => stepGallery(-1));
  document.getElementById('satNext').addEventListener('click', () => stepGallery(1));
  document.addEventListener('keydown', (e) => {
    if (satGallery.hidden) return;
    if (e.key === 'Escape') closeSatGallery();
    else if (e.key === 'ArrowLeft') stepGallery(-1);
    else if (e.key === 'ArrowRight') stepGallery(1);
  });
  window.addEventListener('resize', () => map.invalidateSize());
})();
