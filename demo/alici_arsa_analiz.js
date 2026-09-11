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
      points.push(marker.getBounds());
    }
    result.comparable_selection.comparables.forEach((item) => {
      const source = getComparableCoordinates(item, result);
      if (!source) return;
      const marker = L.circleMarker(source, { radius: 6, color: '#fffef9', fillColor: '#d58b25', fillOpacity: .95, weight: 2 })
        .bindTooltip(`${item.neighbourhood || item.district}<br>${money.format(item.unit_price_tl)} / m²`)
        .addTo(comparableLayer);
      points.push(marker.getBounds());
    });
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
    const rows = result.comparable_selection.comparables;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Bu konum için yeterli arsa emsali bulunamadı.</td></tr>';
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

  function renderFactors(result) {
    document.getElementById('factorList').innerHTML = result.factors.map((item) => `
      <div class="factor"><span>${escapeHtml(item.label)}<small>${item.evidence === 'missing' ? 'Bilinmediği için nötr' : (item.evidence === 'verified_live_source' ? 'Canlı kaynak kanıtı' : 'Kullanıcı girdisi')}</small></span><strong>×${number.format(item.coefficient)}</strong></div>`).join('');
    const multiplier = result.valuation.factor_multiplier || result.factors.reduce((total, item) => total * item.coefficient, 1);
    setText('factorTotal', `×${number.format(multiplier)}`);
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
    const fields = (zoning && zoning.fields) || {};
    setText('planFunction', fields.plan_fonksiyon || fields.imar_durumu);
    setText('zoningRatios', fields.kaks_emsal || fields.taks ? `${fields.kaks_emsal || '—'} / ${fields.taks || '—'}` : '—');
    setText('heightAndFloors', fields.kat_adedi || fields.gabari ? `${fields.kat_adedi ? number.format(fields.kat_adedi) + ' kat' : '—'} / ${fields.gabari ? number.format(fields.gabari) + ' m' : '—'}` : '—');
    setText('setbacks', fields.on_bahce || fields.yan_bahce ? `${fields.on_bahce ? number.format(fields.on_bahce) + ' m' : '—'} / ${fields.yan_bahce ? number.format(fields.yan_bahce) + ' m' : '—'}` : '—');
    setText('planTypeScale', fields.plan_turu || fields.plan_olcegi ? `${fields.plan_turu || '—'} / ${fields.plan_olcegi ? '1/' + fields.plan_olcegi : '—'}` : '—');
    setText('planPinStatus', fields.pin_tucbs_no || fields.plan_sureci ? `${fields.pin_tucbs_no || '—'} / ${fields.plan_sureci || '—'}` : '—');
    setText('planDate', fields.plan_adi || fields.plan_kayit_tarihi ? `${fields.plan_adi || '—'} · ${fields.plan_kayit_tarihi ? String(fields.plan_kayit_tarihi).slice(0, 10) : '—'}` : '—');
    const badge = document.getElementById('zoningBadge');
    badge.textContent = statusLabel(zoning && zoning.status);
    badge.className = `badge ${zoning && ['verified_at_source', 'partial_zoning_verified'].includes(zoning.status) ? 'good' : 'muted'}`;
    setText('zoningWarning', result.warnings.find((warning) => /İmar|E-Plan|KAKS|TAKS/i.test(warning)) || 'Kaynakta gelen alanlar değiştirilmeden gösteriliyor.');

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
    }
  }

  function renderResult(result) {
    const valuation = result.valuation;
    setText('resultTitle', `${result.input.il}${result.input.ilce ? ' / ' + result.input.ilce : ''} arsa analizi`);
    setText('analysisMeta', `${result.model_version} · ${result.elapsed_ms || 0} ms · ${result.analysis_id}`);
    setText('priceRange', valuation.low_total_price == null ? 'Emsal bulunamadı' : `${money.format(valuation.low_total_price)} – ${money.format(valuation.high_total_price)}`);
    setText('priceRangeNote', valuation.status === 'statistical_pre_valuation' ? 'İlan emsallerinden istatistiksel aralık' : 'Daha fazla doğrulanmış emsal gerekli');
    setText('priceEstimate', valuation.estimated_total_price == null ? '—' : money.format(valuation.estimated_total_price));
    setText('unitEstimate', valuation.estimated_unit_price == null ? '— / m²' : `${money.format(valuation.estimated_unit_price)} / m²`);
    setText('confidence', `${valuation.confidence_score}/100`);
    setText('confidenceLabel', valuation.confidence_label.replaceAll('_', ' '));
    setText('comparableCount', result.comparable_selection.selected_count);
    setText('comparableScope', result.comparable_selection.scope.replaceAll('_', ' '));
    setText('methodBadge', `${result.comparable_selection.removed_outlier_count} aykırı dışarıda`);
    resultAlert.className = 'alert';
    resultAlert.textContent = result.warnings.join(' ');
    renderComparables(result);
    renderFactors(result);
    renderZoning(result);
    updateMap(result);
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
    button.addEventListener('click', () => {
      document.querySelectorAll('.mode-switch button').forEach((item) => item.classList.toggle('active', item === button));
      document.querySelectorAll('.parcel-fields').forEach((item) => { item.hidden = button.dataset.mode === 'coordinate'; });
    });
  });
  document.getElementById('fitMap').addEventListener('click', () => { if (lastBounds) map.fitBounds(lastBounds.pad(.18), { maxZoom: 16 }); });
  window.addEventListener('resize', () => map.invalidateSize());
})();
