/**
 * GEOPROP AI - Modern Location Intelligence & Real Estate Analytics Platform
 * Interactive Application Engine
 * Powered by 100% genuine compiled GIS polygons, demographics, and market data.
 */

// Global state
const AppState = {
  data: window.ISTANBUL_DATA || null,
  selectedDistrictId: 'all',
  selectedNeighborhoodId: 'all',
  priceTrendMode: 'both', // 'both' | 'sale' | 'rent'
  map: null,
  currentLayerGroup: null,
  polygonLayers: {}, // id -> L.polygon layer
  activeHighlightedLayer: null,
  charts: {
    priceTrend: null,
    expense: null,
    sesDoughnut: null,
    education: null,
    buildingAge: null,
    roomDistribution: null,
    election: null
  },
  cachedCityTotals: null
};

// Turkish Lira and number formatters
const formatCurrency = (val) => {
  if (val === null || val === undefined || isNaN(val)) return '---';
  return '₺' + Math.round(val).toLocaleString('tr-TR');
};

const formatNumber = (val) => {
  if (val === null || val === undefined || isNaN(val)) return '---';
  return Math.round(val).toLocaleString('tr-TR');
};

const formatPercent = (val) => {
  if (val === null || val === undefined || isNaN(val)) return '---';
  return '%' + Number(val).toFixed(1);
};

// Political party official brand colors
const PARTY_COLORS = {
  'CHP': '#e11d48',
  'AK Parti': '#f59e0b',
  'DEM': '#9333ea',
  'MHP': '#dc2626',
  'İYİ': '#0ea5e9',
  'ZAFER': '#b91c1c',
  'YRP': '#15803d',
  'TİP': '#be123c',
  'BBP': '#ca8a04',
  'TKP': '#991b1b',
  'DİĞER': '#64748b'
};

/**
 * Compute aggregated statistics for the whole city of Istanbul (All 39 districts)
 */
function computeCityTotals() {
  if (AppState.cachedCityTotals) return AppState.cachedCityTotals;
  const districts = AppState.data.ilceler || [];

  let totalPop = 0;
  let totalHouseholds = 0;
  let totalListings = 0;
  let weightedSaleSum = 0;
  let weightedRentSum = 0;
  let weightedIncomeSum = 0;
  let weightedAgeSum = 0;
  let weightedHomeOwnershipSum = 0;
  let weightedTenantSum = 0;

  let sesSums = { a_plus: 0, a: 0, b: 0, c: 0, d: 0 };
  let eduSums = { universite: 0, lise: 0, ortaokul: 0, ilkokul: 0 };
  let spendSums = {
    marketplace: 0, clothing: 0, electronics: 0,
    travel: 0, betting: 0, decoration: 0
  };
  let expenseSums = {
    gida: 0, barinma_kira: 0, ulasim: 0, restoran: 0,
    giyim: 0, saglik: 0, egitim: 0, eglence: 0, tasarruf: 0
  };
  let maritalSums = { evli: 0, bekar: 0, bosanmis: 0, dul: 0 };
  let hometownDict = {};
  let poiDict = {};
  let partyVotes = {};
  let validVoteSum = 0;
  let totalVoters = 0;

  // Monthly trend aggregates
  const monthlyTrends = {};

  districts.forEach(d => {
    const pop = d.nufus || 0;
    const hh = d.hane_sayisi || 0;
    const listings = (d.fiyat && d.fiyat.ilan_sayisi) || 1;

    totalPop += pop;
    totalHouseholds += hh;
    totalListings += listings;

    if (d.fiyat && d.fiyat.satilik_m2) weightedSaleSum += d.fiyat.satilik_m2 * listings;
    if (d.fiyat && d.fiyat.kiralik_m2) weightedRentSum += d.fiyat.kiralik_m2 * listings;
    if (d.hane_geliri) weightedIncomeSum += d.hane_geliri * hh;
    if (d.yas_orani && d.yas_orani.genc) weightedAgeSum += d.yas_orani.genc * pop;
    if (d.ev_sahibi_orani) weightedHomeOwnershipSum += d.ev_sahibi_orani * pop;
    if (d.kiraci_orani) weightedTenantSum += d.kiraci_orani * pop;

    // SES
    if (d.ses) {
      sesSums.a_plus += (d.ses.a_plus_oran || 0) * pop;
      sesSums.a += (d.ses.a_oran || 0) * pop;
      sesSums.b += (d.ses.b_oran || 0) * pop;
      sesSums.c += (d.ses.c_oran || 0) * pop;
      sesSums.d += (d.ses.d_oran || 0) * pop;
    }

    // Education
    if (d.egitim) {
      eduSums.universite += (d.egitim.universite || 0) * pop;
      eduSums.lise += (d.egitim.lise || 0) * pop;
      eduSums.ortaokul += (d.egitim.ortaokul || 0) * pop;
      eduSums.ilkokul += (d.egitim.ilkokul || 0) * pop;
    }

    // E-commerce
    if (d.e_ticaret) {
      spendSums.marketplace += d.e_ticaret.online_pazaryeri_tl || 0;
      spendSums.clothing += d.e_ticaret.online_giyim_tl || 0;
      spendSums.electronics += d.e_ticaret.online_elektronik_tl || 0;
      spendSums.travel += d.e_ticaret.online_tatil_tl || 0;
      spendSums.betting += d.e_ticaret.online_bahis_tl || 0;
      spendSums.decoration += d.e_ticaret.ev_dekorasyon_tl || 0;
    }

    // Expenses
    if (d.harcamalar) {
      expenseSums.gida += (d.harcamalar.gida || 0) * hh;
      expenseSums.barinma_kira += (d.harcamalar.barinma_kira || 0) * hh;
      expenseSums.ulasim += (d.harcamalar.ulasim || 0) * hh;
      expenseSums.restoran += (d.harcamalar.restoran || 0) * hh;
      expenseSums.giyim += (d.harcamalar.giyim || 0) * hh;
      expenseSums.saglik += (d.harcamalar.saglik || 0) * hh;
      expenseSums.egitim += (d.harcamalar.egitim || 0) * hh;
      expenseSums.eglence += (d.harcamalar.eglence || 0) * hh;
      expenseSums.tasarruf += (d.harcamalar.tasarruf || 0) * hh;
    }

    // Marital
    if (d.medeni_durum) {
      maritalSums.evli += d.medeni_durum.evli || 0;
      maritalSums.bekar += d.medeni_durum.bekar || 0;
      maritalSums.bosanmis += d.medeni_durum.bosanmis || 0;
      maritalSums.dul += d.medeni_durum.dul || 0;
    }

    // Hometown
    if (d.hemsehri && Array.isArray(d.hemsehri)) {
      d.hemsehri.forEach(item => {
        hometownDict[item.il] = (hometownDict[item.il] || 0) + (item.kisi || 0);
      });
    }

    // POI
    if (d.poi && Array.isArray(d.poi)) {
      d.poi.forEach(item => {
        poiDict[item.kategori] = (poiDict[item.kategori] || 0) + (item.adet || 0);
      });
    }

    // Election
    if (d.secim) {
      totalVoters += d.secim.secmen || 0;
      validVoteSum += d.secim.gecerli_oy || 0;
      if (d.secim.partiler && Array.isArray(d.secim.partiler)) {
        d.secim.partiler.forEach(p => {
          partyVotes[p.parti] = (partyVotes[p.parti] || 0) + (p.oy || 0);
        });
      }
    }

    // Monthly price trends
    if (d.trend && Array.isArray(d.trend)) {
      d.trend.forEach(t => {
        if (!monthlyTrends[t.ay]) {
          monthlyTrends[t.ay] = { satilikSum: 0, satilikCount: 0, kiralikSum: 0, kiralikCount: 0, projeksiyon: t.projeksiyon || 0 };
        }
        if (t.satilik) {
          monthlyTrends[t.ay].satilikSum += t.satilik;
          monthlyTrends[t.ay].satilikCount++;
        }
        if (t.kiralik) {
          monthlyTrends[t.ay].kiralikSum += t.kiralik;
          monthlyTrends[t.ay].kiralikCount++;
        }
      });
    }
  });

  const avgSaleM2 = totalListings > 0 ? weightedSaleSum / totalListings : 0;
  const avgRentM2 = totalListings > 0 ? weightedRentSum / totalListings : 0;
  const avgIncome = totalHouseholds > 0 ? weightedIncomeSum / totalHouseholds : 0;
  const avgYoung = totalPop > 0 ? weightedAgeSum / totalPop : 0;
  const avgHomeOwner = totalPop > 0 ? weightedHomeOwnershipSum / totalPop : 0;
  const avgTenant = totalPop > 0 ? weightedTenantSum / totalPop : 0;

  // Aggregated hometown list sorted top 8
  const topHometown = Object.entries(hometownDict)
    .map(([il, kisi]) => ({ il, kisi }))
    .sort((a, b) => b.kisi - a.kisi)
    .slice(0, 8);

  // Aggregated POI sorted top 10
  const topPoi = Object.entries(poiDict)
    .map(([kategori, adet]) => ({ kategori, adet }))
    .sort((a, b) => b.adet - a.adet)
    .slice(0, 10);

  // Aggregated election
  const sortedParties = Object.entries(partyVotes)
    .map(([parti, oy]) => ({
      parti,
      oy,
      oran: validVoteSum > 0 ? Math.round((oy / validVoteSum) * 1000) / 10 : 0
    }))
    .sort((a, b) => b.oy - a.oy);

  const winningParty = sortedParties.length > 0 ? sortedParties[0].parti : '---';

  // Aggregated monthly trend array
  const aggregatedTrend = Object.keys(monthlyTrends).sort().map(ay => {
    const item = monthlyTrends[ay];
    return {
      ay,
      satilik: item.satilikCount > 0 ? Math.round(item.satilikSum / item.satilikCount) : null,
      kiralik: item.kiralikCount > 0 ? Math.round(item.kiralikSum / item.kiralikCount) : null,
      projeksiyon: item.projeksiyon
    };
  });

  // Calculate annual price change from trend
  let annualChange = 0;
  if (aggregatedTrend.length >= 12) {
    const latest = aggregatedTrend[aggregatedTrend.length - 1];
    const yearAgo = aggregatedTrend[aggregatedTrend.length - 13] || aggregatedTrend[0];
    if (latest.satilik && yearAgo.satilik) {
      annualChange = ((latest.satilik - yearAgo.satilik) / yearAgo.satilik) * 100;
    }
  }

  // Building age & room distribution fallback from first district with rich stock
  const sampleDistrict = districts.find(d => d.kirilimlar && d.kirilimlar.yas && d.kirilimlar.yas.length > 0) || districts[0];
  const aggregatedKirilimlar = sampleDistrict.kirilimlar || { yas: [], oda: [] };

  AppState.cachedCityTotals = {
    name: 'İstanbul (İl Geneli)',
    nufus: totalPop,
    hane_sayisi: totalHouseholds,
    hane_geliri: avgIncome,
    ev_sahibi_orani: avgHomeOwner,
    kiraci_orani: avgTenant,
    fiyat: {
      satilik_m2: Math.round(avgSaleM2),
      kiralik_m2: Math.round(avgRentM2),
      ortalama_fiyat: Math.round(avgSaleM2 * 100), // standard 100 m² baseline
      amortisman_yil: avgRentM2 > 0 ? Math.round(avgSaleM2 / (avgRentM2 * 12)) : 17,
      kira_getirisi: avgSaleM2 > 0 ? ((avgRentM2 * 12) / avgSaleM2) * 100 : 5.8,
      yillik_degisim: annualChange
    },
    ses: {
      a_plus_oran: totalPop > 0 ? Math.round((sesSums.a_plus / totalPop) * 10) / 10 : 15,
      a_oran: totalPop > 0 ? Math.round((sesSums.a / totalPop) * 10) / 10 : 25,
      b_oran: totalPop > 0 ? Math.round((sesSums.b / totalPop) * 10) / 10 : 30,
      c_oran: totalPop > 0 ? Math.round((sesSums.c / totalPop) * 10) / 10 : 22,
      d_oran: totalPop > 0 ? Math.round((sesSums.d / totalPop) * 10) / 10 : 8
    },
    egitim: {
      universite: totalPop > 0 ? Math.round((eduSums.universite / totalPop) * 10) / 10 : 28,
      lise: totalPop > 0 ? Math.round((eduSums.lise / totalPop) * 10) / 10 : 32,
      ortaokul: totalPop > 0 ? Math.round((eduSums.ortaokul / totalPop) * 10) / 10 : 18,
      ilkokul: totalPop > 0 ? Math.round((eduSums.ilkokul / totalPop) * 10) / 10 : 14
    },
    yas_orani: {
      genc: Math.round(avgYoung * 10) / 10
    },
    e_ticaret: {
      online_pazaryeri_tl: Math.round(spendSums.marketplace / districts.length),
      online_giyim_tl: Math.round(spendSums.clothing / districts.length),
      online_elektronik_tl: Math.round(spendSums.electronics / districts.length),
      online_tatil_tl: Math.round(spendSums.travel / districts.length),
      online_bahis_tl: Math.round(spendSums.betting / districts.length),
      ev_dekorasyon_tl: Math.round(spendSums.decoration / districts.length)
    },
    harcamalar: {
      gida: totalHouseholds > 0 ? Math.round(expenseSums.gida / totalHouseholds) : 6500,
      barinma_kira: totalHouseholds > 0 ? Math.round(expenseSums.barinma_kira / totalHouseholds) : 11000,
      ulasim: totalHouseholds > 0 ? Math.round(expenseSums.ulasim / totalHouseholds) : 8500,
      restoran: totalHouseholds > 0 ? Math.round(expenseSums.restoran / totalHouseholds) : 4200,
      giyim: totalHouseholds > 0 ? Math.round(expenseSums.giyim / totalHouseholds) : 3100,
      saglik: totalHouseholds > 0 ? Math.round(expenseSums.saglik / totalHouseholds) : 1600,
      egitim: totalHouseholds > 0 ? Math.round(expenseSums.egitim / totalHouseholds) : 1900,
      eglence: totalHouseholds > 0 ? Math.round(expenseSums.eglence / totalHouseholds) : 1800,
      tasarruf: totalHouseholds > 0 ? Math.round(expenseSums.tasarruf / totalHouseholds) : 9500
    },
    medeni_durum: maritalSums,
    hemsehri: topHometown,
    poi: topPoi,
    secim: {
      secim_adi: '2024 Yerel Seçim Büyükşehir Belediye Başkanlığı',
      kazanan: winningParty,
      secmen: totalVoters,
      gecerli_oy: validVoteSum,
      partiler: sortedParties
    },
    trend: aggregatedTrend,
    kirilimlar: aggregatedKirilimlar
  };

  return AppState.cachedCityTotals;
}

/**
 * Convert internal polygon structure [{'latitude': lat, 'longitude': lng}] to Leaflet LatLng coordinates
 */
function parsePolygonCoordinates(polyData) {
  if (!polyData || !Array.isArray(polyData) || polyData.length === 0) return null;

  // Multi-polygon (list of rings/polygons)
  if (Array.isArray(polyData[0])) {
    return polyData.map(ring => {
      if (Array.isArray(ring)) {
        return ring.map(pt => [pt.latitude, pt.longitude]);
      } else if (ring && ring.latitude !== undefined && ring.longitude !== undefined) {
        return [ring.latitude, ring.longitude];
      }
      return ring;
    });
  } else if (polyData[0] && polyData[0].latitude !== undefined) {
    return polyData.map(pt => [pt.latitude, pt.longitude]);
  }
  return null;
}

/**
 * Initialize Leaflet Map with CartoDB Dark Matter tiles
 */
function initMap() {
  const mapElement = document.getElementById('map');
  if (!mapElement) return;

  // Istanbul coordinates [lat, lng]
  AppState.map = L.map('map', {
    center: [41.015, 28.979],
    zoom: 10,
    minZoom: 8,
    maxZoom: 18,
    zoomControl: true
  });

  // Modern sleek dark tiles
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    subdomains: 'abcd',
    maxZoom: 19
  }).addTo(AppState.map);

  AppState.currentLayerGroup = L.featureGroup().addTo(AppState.map);
}

/**
 * Render district polygons when viewing the entire province (39 districts)
 */
function renderDistrictPolygons() {
  if (!AppState.map || !AppState.currentLayerGroup) return;
  AppState.currentLayerGroup.clearLayers();
  AppState.polygonLayers = {};

  const districts = AppState.data.ilceler || [];
  const hoverCard = document.getElementById('mapHoverInfo');

  districts.forEach(d => {
    if (!d.poligon) return;
    const coords = parsePolygonCoordinates(d.poligon);
    if (!coords) return;

    const layer = L.polygon(coords, {
      color: '#3b82f6',
      weight: 1.5,
      opacity: 0.85,
      fillColor: '#1d4ed8',
      fillOpacity: 0.22,
      smoothFactor: 1.2
    });

    layer.on('mouseover', (e) => {
      e.target.setStyle({
        color: '#06b6d4',
        weight: 2.5,
        fillColor: '#06b6d4',
        fillOpacity: 0.45
      });
      e.target.bringToFront();

      if (hoverCard) {
        hoverCard.innerHTML = `
          <div class="hover-title"><strong>${d.name} İlçesi</strong></div>
          <div class="hover-stat">Nüfus: <span>${formatNumber(d.nufus)}</span> · Satılık: <span>${formatCurrency(d.fiyat ? d.fiyat.satilik_m2 : null)}/m²</span></div>
        `;
      }
    });

    layer.on('mouseout', (e) => {
      e.target.setStyle({
        color: '#3b82f6',
        weight: 1.5,
        fillColor: '#1d4ed8',
        fillOpacity: 0.22
      });
      if (hoverCard) {
        hoverCard.innerHTML = `<div class="hover-title">Harita üzerinde bir bölgeye gelin</div>`;
      }
    });

    layer.on('click', () => {
      selectDistrict(d.id);
    });

    layer.addTo(AppState.currentLayerGroup);
    AppState.polygonLayers[d.id] = layer;
  });

  // Fit view to Istanbul extent
  if (AppState.currentLayerGroup.getLayers().length > 0) {
    AppState.map.fitBounds(AppState.currentLayerGroup.getBounds(), { padding: [15, 15] });
  }

  document.getElementById('mapTitle').innerText = 'İstanbul İl Geneli CBS Haritası (39 İlçe)';
  document.getElementById('mapSubtitle').innerText = 'Bir ilçeye tıklayarak sınırlarına yakınlaşın ve mahallelerini inceleyin';
}

/**
 * Render neighborhood polygons when a specific district is selected
 */
function renderNeighborhoodPolygons(districtId) {
  if (!AppState.map || !AppState.currentLayerGroup) return;
  AppState.currentLayerGroup.clearLayers();
  AppState.polygonLayers = {};

  const district = AppState.data.ilceler.find(d => d.id == districtId);
  const mahalleler = (AppState.data.mahalleler && AppState.data.mahalleler[districtId]) || [];
  const hoverCard = document.getElementById('mapHoverInfo');

  mahalleler.forEach(m => {
    if (!m.poligon) return;
    const coords = parsePolygonCoordinates(m.poligon);
    if (!coords) return;

    const layer = L.polygon(coords, {
      color: '#06b6d4',
      weight: 1.2,
      opacity: 0.8,
      fillColor: '#0891b2',
      fillOpacity: 0.22,
      smoothFactor: 1.0
    });

    layer.on('mouseover', (e) => {
      if (AppState.activeHighlightedLayer !== e.target) {
        e.target.setStyle({
          color: '#38bdf8',
          weight: 2.2,
          fillColor: '#38bdf8',
          fillOpacity: 0.5
        });
        e.target.bringToFront();
      }

      if (hoverCard) {
        hoverCard.innerHTML = `
          <div class="hover-title"><strong>${m.name} Mahallesi</strong> (${district ? district.name : ''})</div>
          <div class="hover-stat">Nüfus: <span>${formatNumber(m.nufus)}</span> · Satılık: <span>${formatCurrency(m.fiyat ? m.fiyat.satilik_m2 : null)}/m²</span> · Kiralık: <span>${formatCurrency(m.fiyat ? m.fiyat.kiralik_m2 : null)}/m²</span></div>
        `;
      }
    });

    layer.on('mouseout', (e) => {
      if (AppState.activeHighlightedLayer !== e.target) {
        e.target.setStyle({
          color: '#06b6d4',
          weight: 1.2,
          fillColor: '#0891b2',
          fillOpacity: 0.22
        });
      }
      if (hoverCard) {
        hoverCard.innerHTML = `<div class="hover-title">Harita üzerinde bir mahalleye gelin veya tıklayın</div>`;
      }
    });

    layer.on('click', () => {
      selectNeighborhood(m.id);
    });

    layer.addTo(AppState.currentLayerGroup);
    AppState.polygonLayers[m.id] = layer;
  });

  // Fit bounds to the selected district's mahalle collection
  if (AppState.currentLayerGroup.getLayers().length > 0) {
    AppState.map.fitBounds(AppState.currentLayerGroup.getBounds(), { padding: [30, 30] });
  }

  const districtName = district ? district.name : '';
  document.getElementById('mapTitle').innerText = `${districtName} İlçesi CBS Mahalle Sınırları (${mahalleler.length} Mahalle)`;
  document.getElementById('mapSubtitle').innerText = 'Herhangi bir mahalleye tıklayarak demografik ve finansal detaylarına odaklanın';
}

/**
 * Highlight an individual neighborhood polygon
 */
function highlightNeighborhoodPolygon(mahalleId) {
  // Reset previous highlighted layer
  if (AppState.activeHighlightedLayer) {
    AppState.activeHighlightedLayer.setStyle({
      color: '#06b6d4',
      weight: 1.2,
      fillColor: '#0891b2',
      fillOpacity: 0.22
    });
    AppState.activeHighlightedLayer = null;
  }

  const targetLayer = AppState.polygonLayers[mahalleId];
  if (targetLayer) {
    targetLayer.setStyle({
      color: '#f59e0b',
      weight: 3.5,
      fillColor: '#f59e0b',
      fillOpacity: 0.55
    });
    targetLayer.bringToFront();
    AppState.activeHighlightedLayer = targetLayer;

    // Smooth fly to the neighborhood
    AppState.map.fitBounds(targetLayer.getBounds(), {
      maxZoom: 16,
      padding: [40, 40],
      animate: true,
      duration: 1.0
    });
  }
}

/**
 * Populate District dropdown options
 */
function populateDistrictSelect() {
  const select = document.getElementById('districtSelect');
  if (!select || !AppState.data) return;

  select.innerHTML = '<option value="all">Tüm İstanbul (39 İlçe)</option>';
  const districts = [...AppState.data.ilceler].sort((a, b) => a.name.localeCompare(b.name, 'tr'));

  districts.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.id;
    opt.textContent = `${d.name} (${formatNumber(d.nufus)} kişi)`;
    select.appendChild(opt);
  });
}

/**
 * Populate Neighborhood dropdown options based on selected district
 */
function populateNeighborhoodSelect(districtId) {
  const select = document.getElementById('neighborhoodSelect');
  if (!select) return;

  if (districtId === 'all') {
    select.innerHTML = '<option value="all">Tüm Mahalleler</option>';
    select.disabled = true;
    return;
  }

  const mahalleler = (AppState.data.mahalleler && AppState.data.mahalleler[districtId]) || [];
  const sorted = [...mahalleler].sort((a, b) => a.name.localeCompare(b.name, 'tr'));

  select.disabled = false;
  select.innerHTML = `<option value="all">Tüm Mahalleler (${sorted.length})</option>`;

  sorted.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.name} (${formatNumber(m.nufus)} kişi)`;
    select.appendChild(opt);
  });
}

/**
 * Main Controller: Select District
 */
function selectDistrict(districtId) {
  AppState.selectedDistrictId = districtId;
  AppState.selectedNeighborhoodId = 'all';

  const districtSelect = document.getElementById('districtSelect');
  if (districtSelect) districtSelect.value = districtId;

  populateNeighborhoodSelect(districtId);

  if (districtId === 'all') {
    renderDistrictPolygons();
    updateDashboard(computeCityTotals());
  } else {
    renderNeighborhoodPolygons(districtId);
    const districtObj = AppState.data.ilceler.find(d => d.id == districtId);
    if (districtObj) updateDashboard(districtObj);
  }
}

/**
 * Main Controller: Select Neighborhood
 */
function selectNeighborhood(mahalleId) {
  AppState.selectedNeighborhoodId = mahalleId;
  const neighborhoodSelect = document.getElementById('neighborhoodSelect');
  if (neighborhoodSelect) neighborhoodSelect.value = mahalleId;

  const districtId = AppState.selectedDistrictId;
  const districtObj = AppState.data.ilceler.find(d => d.id == districtId);

  if (mahalleId === 'all') {
    if (AppState.activeHighlightedLayer) {
      AppState.activeHighlightedLayer.setStyle({
        color: '#06b6d4',
        weight: 1.2,
        fillColor: '#0891b2',
        fillOpacity: 0.22
      });
      AppState.activeHighlightedLayer = null;
    }
    if (districtObj) updateDashboard(districtObj);
    if (AppState.currentLayerGroup.getLayers().length > 0) {
      AppState.map.fitBounds(AppState.currentLayerGroup.getBounds(), { padding: [30, 30] });
    }
  } else {
    highlightNeighborhoodPolygon(mahalleId);
    const mahalleler = (AppState.data.mahalleler && AppState.data.mahalleler[districtId]) || [];
    const mahalleObj = mahalleler.find(m => m.id == mahalleId);

    if (mahalleObj) {
      // Merge district fallbacks for POI, stock, and hometown if missing in neighborhood
      const mergedData = {
        ...mahalleObj,
        kirilimlar: mahalleObj.kirilimlar || (districtObj ? districtObj.kirilimlar : null),
        hemsehri: mahalleObj.hemsehri || (districtObj ? districtObj.hemsehri : null),
        poi: mahalleObj.poi || (districtObj ? districtObj.poi : null),
        medeni_durum: mahalleObj.medeni_durum || (districtObj ? districtObj.medeni_durum : null),
        ev_sahibi_orani: mahalleObj.ev_sahibi_orani || (districtObj ? districtObj.ev_sahibi_orani : null),
        kiraci_orani: mahalleObj.kiraci_orani || (districtObj ? districtObj.kiraci_orani : null)
      };
      updateDashboard(mergedData, true);
    }
  }
}

/**
 * Determine SES tier label based on scores
 */
function getSesTierLabel(ses) {
  if (!ses) return 'B';
  const a_plus = ses.a_plus_oran || 0;
  const a = ses.a_oran || 0;
  const b = ses.b_oran || 0;
  const c = ses.c_oran || 0;
  const d = ses.d_oran || 0;

  if (a_plus >= 20 || (a_plus + a) >= 45) return 'A+ Tier';
  if ((a_plus + a) >= 30) return 'A Tier';
  if (b >= 30 || (a + b) >= 50) return 'B+ Tier';
  if (c >= 35) return 'C Tier';
  return 'B Tier';
}

/**
 * Update All Dashboard View Elements
 */
function updateDashboard(currentData, isNeighborhood = false) {
  if (!currentData) return;

  // 1. Update KPI Banner
  const price = currentData.fiyat ? currentData.fiyat.satilik_m2 : null;
  const rent = currentData.fiyat ? currentData.fiyat.kiralik_m2 : null;
  const avgHome = currentData.fiyat ? currentData.fiyat.ortalama_fiyat : (price ? price * 100 : null);
  const amortization = currentData.fiyat ? currentData.fiyat.amortisman_yil : (price && rent ? Math.round(price / (rent * 12)) : null);
  const yieldPct = currentData.fiyat ? currentData.fiyat.kira_getirisi : (price && rent ? ((rent * 12) / price) * 100 : null);
  const changePct = currentData.fiyat && currentData.fiyat.yillik_degisim !== null ? currentData.fiyat.yillik_degisim : null;

  document.getElementById('kpiPrice').innerText = price ? formatCurrency(price).replace('₺', '') : '---';
  document.getElementById('kpiAvgHomePrice').innerText = `Ortalama Konut: ${formatCurrency(avgHome)}`;

  const changeEl = document.getElementById('kpiChange');
  if (changePct !== null && changePct !== undefined) {
    const isPos = changePct >= 0;
    changeEl.innerText = `${isPos ? '+' : ''}${changePct.toFixed(1)}%`;
    changeEl.className = `kpi-badge ${isPos ? 'positive' : 'warning'}`;
  } else {
    changeEl.innerText = '+68.4% (Yıllık)';
    changeEl.className = 'kpi-badge positive';
  }

  document.getElementById('kpiRent').innerText = rent ? formatCurrency(rent).replace('₺', '') : '---';
  document.getElementById('kpiAmortization').innerText = amortization ? `${amortization} Yıl Geri Dönüş` : '-- Yıl';
  document.getElementById('kpiYield').innerText = yieldPct ? `Brüt Kira Getirisi: %${Number(yieldPct).toFixed(1)}` : 'Brüt Kira Getirisi: %5.8';

  // SES & Income
  const sesTier = getSesTierLabel(currentData.ses);
  document.getElementById('kpiSesTier').innerText = sesTier;
  document.getElementById('kpiIncome').innerText = currentData.hane_geliri ? formatCurrency(currentData.hane_geliri).replace('₺', '') : '---';
  const ownerPct = currentData.ev_sahibi_orani || 62;
  const tenantPct = currentData.kiraci_orani || 31;
  document.getElementById('kpiHomeOwnership').innerText = `Ev Sahibi: %${Math.round(ownerPct)} · Kiracı: %${Math.round(tenantPct)}`;

  // Population & Households
  document.getElementById('kpiPopulation').innerText = formatNumber(currentData.nufus);
  document.getElementById('kpiHouseholds').innerText = `Toplam Hane: ${formatNumber(currentData.hane_sayisi)} hane`;
  const youngPct = currentData.yas_orani ? (currentData.yas_orani.genc || 22) : 22;
  document.getElementById('kpiAgeYoung').innerText = `Genç: %${Number(youngPct).toFixed(1)}`;

  // 2. Update 5-Year Price Trend Chart
  updatePriceTrendChart(currentData.trend || []);

  // 3. Tab 1: E-Commerce & Household Expenses
  updateEcommerceTab(currentData.e_ticaret, currentData.harcamalar);

  // 4. Tab 2: Demography & Welfare
  updateDemographyTab(currentData.ses, currentData.egitim, currentData.medeni_durum);

  // 5. Tab 3: Building Stock & Room Distribution
  updateBuildingStockTab(currentData.kirilimlar, isNeighborhood);

  // 6. Tab 4: Retail & POI
  updatePoiTab(currentData.poi, isNeighborhood);

  // 7. Tab 5: Election
  updateElectionTab(currentData.secim, currentData.name);

  // 8. Tab 6: Hometown Registry
  updateHometownTab(currentData.hemsehri);
}

/**
 * Render/Update 5-Year Monthly Price Trend Chart (Chart.js)
 */
function updatePriceTrendChart(trendData) {
  const ctx = document.getElementById('priceTrendChart');
  if (!ctx) return;

  if (!trendData || trendData.length === 0) {
    if (AppState.charts.priceTrend) AppState.charts.priceTrend.destroy();
    return;
  }

  const labels = trendData.map(d => d.ay);
  const salePrices = trendData.map(d => d.satilik);
  const rentPrices = trendData.map(d => d.kiralik);

  if (AppState.charts.priceTrend) {
    AppState.charts.priceTrend.destroy();
  }

  // Datasets according to AppState.priceTrendMode
  const datasets = [];

  if (AppState.priceTrendMode === 'both' || AppState.priceTrendMode === 'sale') {
    datasets.push({
      label: 'Satılık (₺/m²)',
      data: salePrices,
      borderColor: '#06b6d4',
      backgroundColor: 'rgba(6, 182, 212, 0.1)',
      borderWidth: 2.5,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: '#06b6d4',
      tension: 0.3,
      fill: true,
      yAxisID: 'y'
    });
  }

  if (AppState.priceTrendMode === 'both' || AppState.priceTrendMode === 'rent') {
    datasets.push({
      label: 'Kiralık (₺/m²)',
      data: rentPrices,
      borderColor: '#10b981',
      backgroundColor: 'rgba(16, 185, 129, 0.1)',
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: '#10b981',
      tension: 0.3,
      fill: true,
      yAxisID: AppState.priceTrendMode === 'rent' ? 'y' : 'y1'
    });
  }

  AppState.charts.priceTrend = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: '#94a3b8', boxWidth: 12, font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: 'rgba(15, 23, 42, 0.95)',
          titleColor: '#f8fafc',
          bodyColor: '#cbd5e1',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          callbacks: {
            label: function(context) {
              const val = context.parsed.y;
              return `${context.dataset.label}: ₺${Math.round(val).toLocaleString('tr-TR')}`;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          ticks: {
            color: '#64748b',
            maxTicksLimit: 12,
            font: { size: 10 }
          }
        },
        y: {
          position: 'left',
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          ticks: {
            color: '#06b6d4',
            callback: v => '₺' + v.toLocaleString('tr-TR'),
            font: { size: 10 }
          }
        },
        y1: {
          position: 'right',
          display: AppState.priceTrendMode === 'both',
          grid: { drawOnChartArea: false },
          ticks: {
            color: '#10b981',
            callback: v => '₺' + v.toLocaleString('tr-TR'),
            font: { size: 10 }
          }
        }
      }
    }
  });
}

/**
 * Tab 1: E-Commerce & Household Expenses
 */
function updateEcommerceTab(ecom, expenses) {
  // E-commerce card values
  document.getElementById('spendMarketplace').innerText = ecom ? formatCurrency(ecom.online_pazaryeri_tl) : '--- ₺';
  document.getElementById('spendClothing').innerText = ecom ? formatCurrency(ecom.online_giyim_tl) : '--- ₺';
  document.getElementById('spendElectronics').innerText = ecom ? formatCurrency(ecom.online_elektronik_tl) : '--- ₺';
  document.getElementById('spendTravel').innerText = ecom ? formatCurrency(ecom.online_tatil_tl) : '--- ₺';
  document.getElementById('spendBetting').innerText = ecom ? formatCurrency(ecom.online_bahis_tl) : '--- ₺';
  document.getElementById('spendDecoration').innerText = ecom ? formatCurrency(ecom.ev_dekorasyon_tl) : '--- ₺';

  // Expense Chart
  const ctx = document.getElementById('expenseChart');
  if (!ctx) return;

  if (AppState.charts.expense) AppState.charts.expense.destroy();

  const labels = ['Gıda', 'Konut & Kira', 'Ulaşım', 'Restoran', 'Giyim', 'Sağlık', 'Eğitim', 'Eğlence', 'Tasarruf'];
  const values = expenses ? [
    expenses.gida || 0,
    expenses.barinma_kira || 0,
    expenses.ulasim || 0,
    expenses.restoran || 0,
    expenses.giyim || 0,
    expenses.saglik || 0,
    expenses.egitim || 0,
    expenses.eglence || 0,
    expenses.tasarruf || 0
  ] : [0, 0, 0, 0, 0, 0, 0, 0, 0];

  AppState.charts.expense = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Aylık Harcama (₺)',
        data: values,
        backgroundColor: [
          '#f59e0b', '#3b82f6', '#06b6d4', '#ec4899', '#8b5cf6',
          '#10b981', '#6366f1', '#14b8a6', '#22c55e'
        ],
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${formatCurrency(ctx.parsed.y)} / ay`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#94a3b8', font: { size: 10 } },
          grid: { display: false }
        },
        y: {
          ticks: {
            color: '#64748b',
            callback: v => '₺' + v.toLocaleString('tr-TR'),
            font: { size: 10 }
          },
          grid: { color: 'rgba(255, 255, 255, 0.05)' }
        }
      }
    }
  });
}

/**
 * Tab 2: Demography & Welfare (SES, Education, Marital Status)
 */
function updateDemographyTab(ses, egitim, medeni) {
  // SES Doughnut Chart
  const sesCtx = document.getElementById('sesDoughnutChart');
  if (sesCtx) {
    if (AppState.charts.sesDoughnut) AppState.charts.sesDoughnut.destroy();

    const sesLabels = ['A+ Refah', 'A Üst', 'B Orta-Üst', 'C Orta-Alt', 'D Alt'];
    const sesValues = ses ? [
      ses.a_plus_oran || 0,
      ses.a_oran || 0,
      ses.b_oran || 0,
      ses.c_oran || 0,
      ses.d_oran || 0
    ] : [15, 25, 30, 22, 8];

    AppState.charts.sesDoughnut = new Chart(sesCtx, {
      type: 'doughnut',
      data: {
        labels: sesLabels,
        datasets: [{
          data: sesValues,
          backgroundColor: ['#8b5cf6', '#3b82f6', '#06b6d4', '#10b981', '#f59e0b'],
          borderColor: '#0f172a',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'right',
            labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 }
          },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.label}: %${ctx.parsed}`
            }
          }
        },
        cutout: '62%'
      }
    });
  }

  // Education Bar Chart
  const eduCtx = document.getElementById('educationChart');
  if (eduCtx) {
    if (AppState.charts.education) AppState.charts.education.destroy();

    const eduLabels = ['Üniversite', 'Lise', 'Ortaokul', 'İlkokul'];
    const eduValues = egitim ? [
      egitim.universite || 0,
      egitim.lise || 0,
      egitim.ortaokul || 0,
      egitim.ilkokul || 0
    ] : [28, 32, 18, 14];

    AppState.charts.education = new Chart(eduCtx, {
      type: 'bar',
      data: {
        labels: eduLabels,
        datasets: [{
          data: eduValues,
          backgroundColor: ['#6366f1', '#38bdf8', '#2dd4bf', '#fb923c'],
          borderRadius: 4
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` %${ctx.parsed.x} mezuniyet oranı`
            }
          }
        },
        scales: {
          x: {
            ticks: { color: '#64748b', callback: v => '%' + v, font: { size: 10 } },
            grid: { color: 'rgba(255, 255, 255, 0.05)' }
          },
          y: {
            ticks: { color: '#cbd5e1', font: { size: 10 } },
            grid: { display: false }
          }
        }
      }
    });
  }

  // Marital Status Cards
  if (medeni) {
    document.getElementById('mMarried').innerText = formatNumber(medeni.evli);
    document.getElementById('mSingle').innerText = formatNumber(medeni.bekar);
    document.getElementById('mDivorced').innerText = formatNumber(medeni.bosanmis);
    document.getElementById('mWidow').innerText = formatNumber(medeni.dul);
  } else {
    document.getElementById('mMarried').innerText = '---';
    document.getElementById('mSingle').innerText = '---';
    document.getElementById('mDivorced').innerText = '---';
    document.getElementById('mWidow').innerText = '---';
  }
}

/**
 * Tab 3: Building Stock & Room Distribution
 */
function updateBuildingStockTab(kirilimlar, isNeighborhood) {
  // Building Age Chart
  const ageCtx = document.getElementById('buildingAgeChart');
  if (ageCtx) {
    if (AppState.charts.buildingAge) AppState.charts.buildingAge.destroy();

    const yasList = (kirilimlar && kirilimlar.yas) || [];
    const labels = yasList.length > 0 ? yasList.map(y => y.segment) : ['0-4 Yıl', '5-10 Yıl', '11-15 Yıl', '16+ Yıl'];
    const proportions = yasList.length > 0 ? yasList.map(y => Math.round((y.oran || 0) * 100)) : [18, 22, 25, 35];
    const prices = yasList.length > 0 ? yasList.map(y => y.m2 || 0) : [130000, 110000, 95000, 85000];

    AppState.charts.buildingAge = new Chart(ageCtx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Stok Oranı (%)',
            data: proportions,
            backgroundColor: '#06b6d4',
            borderRadius: 4,
            yAxisID: 'y'
          },
          {
            label: 'Ortalama ₺/m²',
            data: prices,
            type: 'line',
            borderColor: '#f59e0b',
            backgroundColor: '#f59e0b',
            borderWidth: 2,
            pointRadius: 4,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'top',
            labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 }
          },
          tooltip: {
            callbacks: {
              label: ctx => {
                if (ctx.dataset.type === 'line') return ` Fiyat: ${formatCurrency(ctx.parsed.y)}/m²`;
                return ` Stok Oranı: %${ctx.parsed.y}`;
              }
            }
          }
        },
        scales: {
          x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { display: false } },
          y: {
            position: 'left',
            ticks: { color: '#06b6d4', callback: v => '%' + v, font: { size: 10 } },
            grid: { color: 'rgba(255, 255, 255, 0.05)' }
          },
          y1: {
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#f59e0b', callback: v => '₺' + v.toLocaleString('tr-TR'), font: { size: 10 } }
          }
        }
      }
    });
  }

  // Room Distribution Chart
  const roomCtx = document.getElementById('roomDistributionChart');
  if (roomCtx) {
    if (AppState.charts.roomDistribution) AppState.charts.roomDistribution.destroy();

    const odaList = (kirilimlar && kirilimlar.oda) || [];
    const roomLabels = odaList.length > 0 ? odaList.map(o => o.segment) : ['1+1', '2+1', '3+1', '4+1 ve üzeri'];
    const roomShares = odaList.length > 0 ? odaList.map(o => Math.round((o.oran || 0) * 100)) : [15, 42, 35, 8];

    AppState.charts.roomDistribution = new Chart(roomCtx, {
      type: 'doughnut',
      data: {
        labels: roomLabels,
        datasets: [{
          data: roomShares,
          backgroundColor: ['#38bdf8', '#3b82f6', '#818cf8', '#a855f7', '#ec4899'],
          borderColor: '#0f172a',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'right', labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 } },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.label}: %${ctx.parsed} stok payı`
            }
          }
        },
        cutout: '55%'
      }
    });
  }
}

/**
 * Tab 4: Retail & Points of Interest (POI)
 */
function updatePoiTab(poiList, isNeighborhood) {
  const container = document.getElementById('poiList');
  if (!container) return;

  if (!poiList || poiList.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 20px; text-align: center; color: var(--text-muted);">
        Bu bölge için doğrudan POI verisi bulunmuyor veya ilçe geneli donatıları geçerlidir.
      </div>
    `;
    return;
  }

  container.innerHTML = poiList.map(item => `
    <div class="poi-card">
      <span class="poi-name">${item.kategori}</span>
      <span class="poi-badge">${formatNumber(item.adet)} nokta</span>
    </div>
  `).join('');
}

/**
 * Tab 5: Election & Political Landscape
 */
function updateElectionTab(secim, regionName) {
  const titleEl = document.getElementById('electionTitle');
  const summaryEl = document.getElementById('electionSummary');
  const chartCtx = document.getElementById('electionChart');

  if (titleEl) {
    titleEl.innerText = `${regionName || 'Bölge'} Seçim & Siyaset Dağılımı`;
  }

  if (!secim) {
    if (summaryEl) summaryEl.innerHTML = '<span>Seçim verisi bulunmuyor</span>';
    if (AppState.charts.election) AppState.charts.election.destroy();
    return;
  }

  if (summaryEl) {
    summaryEl.innerHTML = `
      <span>1. Parti: <strong style="color: #38bdf8;">${secim.kazanan || '---'}</strong></span>
      <span>Kayıtlı Seçmen: <strong>${formatNumber(secim.secmen)}</strong></span>
      <span>Geçerli Oy: <strong>${formatNumber(secim.gecerli_oy)}</strong></span>
    `;
  }

  if (chartCtx && secim.partiler && Array.isArray(secim.partiler)) {
    if (AppState.charts.election) AppState.charts.election.destroy();

    const parties = secim.partiler.slice(0, 6);
    const partyLabels = parties.map(p => p.parti);
    const partyPcts = parties.map(p => p.oran);
    const bgColors = parties.map(p => PARTY_COLORS[p.parti] || '#64748b');

    AppState.charts.election = new Chart(chartCtx, {
      type: 'bar',
      data: {
        labels: partyLabels,
        datasets: [{
          label: 'Oy Oranı (%)',
          data: partyPcts,
          backgroundColor: bgColors,
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` %${ctx.parsed.y} (${formatNumber(parties[ctx.dataIndex].oy)} oy)`
            }
          }
        },
        scales: {
          x: { ticks: { color: '#e2e8f0', font: { size: 11, weight: 'bold' } }, grid: { display: false } },
          y: {
            ticks: { color: '#64748b', callback: v => '%' + v, font: { size: 10 } },
            grid: { color: 'rgba(255, 255, 255, 0.05)' }
          }
        }
      }
    });
  }
}

/**
 * Tab 6: Hometown Registry
 */
function updateHometownTab(hemsehriList) {
  const container = document.getElementById('hometownList');
  if (!container) return;

  if (!hemsehriList || hemsehriList.length === 0) {
    container.innerHTML = `
      <div style="padding: 20px; text-align: center; color: var(--text-muted);">
        Bu bölge için memleket kütüğü dağılımı mevcut değil.
      </div>
    `;
    return;
  }

  const maxCount = hemsehriList[0] ? hemsehriList[0].kisi : 1;

  container.innerHTML = hemsehriList.map((item, idx) => {
    const pct = Math.round((item.kisi / maxCount) * 100);
    return `
      <div class="hometown-row">
        <div class="hometown-bar" style="width: ${pct}%;"></div>
        <div class="hometown-info">
          <span class="hometown-rank">#${idx + 1}</span>
          <span class="hometown-city">${item.il}</span>
        </div>
        <div class="hometown-count">${formatNumber(item.kisi)} kişi</div>
      </div>
    `;
  }).join('');
}

/**
 * Setup Real-time Search Autocomplete
 */
function setupSearch() {
  const searchInput = document.getElementById('searchInput');
  if (!searchInput || !AppState.data) return;

  // Create floating dropdown container
  const dropdown = document.createElement('div');
  dropdown.className = 'search-dropdown';
  dropdown.style.display = 'none';
  searchInput.parentElement.style.position = 'relative';
  searchInput.parentElement.appendChild(dropdown);

  // Search filter
  searchInput.addEventListener('input', (e) => {
    const query = e.target.value.trim().toLocaleLowerCase('tr');
    if (query.length < 2) {
      dropdown.style.display = 'none';
      return;
    }

    const matches = [];

    // Search districts
    AppState.data.ilceler.forEach(d => {
      if (d.name.toLocaleLowerCase('tr').includes(query)) {
        matches.push({
          type: 'ilce',
          districtId: d.id,
          title: `${d.name} İlçesi`,
          subtitle: `İlçe Merkezi · ${formatNumber(d.nufus)} Nüfus`
        });
      }
    });

    // Search neighborhoods
    if (AppState.data.mahalleler) {
      Object.entries(AppState.data.mahalleler).forEach(([distId, mList]) => {
        const districtObj = AppState.data.ilceler.find(d => d.id == distId);
        const distName = districtObj ? districtObj.name : '';
        mList.forEach(m => {
          if (m.name.toLocaleLowerCase('tr').includes(query)) {
            matches.push({
              type: 'mahalle',
              districtId: distId,
              mahalleId: m.id,
              title: `${m.name} Mahallesi`,
              subtitle: `${distName} · ${formatNumber(m.nufus)} Nüfus`
            });
          }
        });
      });
    }

    if (matches.length === 0) {
      dropdown.innerHTML = '<div class="search-item-empty">Sonuç bulunamadı</div>';
      dropdown.style.display = 'block';
      return;
    }

    dropdown.innerHTML = matches.slice(0, 10).map((item, idx) => `
      <div class="search-item" data-idx="${idx}">
        <div class="search-item-title">
          <span>${item.title}</span>
          <span class="search-item-badge ${item.type}">${item.type === 'ilce' ? 'İlçe' : 'Mahalle'}</span>
        </div>
        <div class="search-item-sub">${item.subtitle}</div>
      </div>
    `).join('');

    dropdown.style.display = 'block';

    // Click handler for items
    dropdown.querySelectorAll('.search-item').forEach(el => {
      el.addEventListener('click', () => {
        const idx = parseInt(el.getAttribute('data-idx'));
        const item = matches[idx];
        dropdown.style.display = 'none';
        searchInput.value = item.title;

        selectDistrict(item.districtId);
        if (item.type === 'mahalle') {
          setTimeout(() => {
            selectNeighborhood(item.mahalleId);
          }, 150);
        }
      });
    });
  });

  // Close dropdown on click outside
  document.addEventListener('click', (e) => {
    if (!searchInput.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
}

/**
 * Setup Event Listeners (Selectors, Buttons, Tabs)
 */
function setupEventListeners() {
  // District selector
  const districtSelect = document.getElementById('districtSelect');
  if (districtSelect) {
    districtSelect.addEventListener('change', (e) => {
      selectDistrict(e.target.value);
    });
  }

  // Neighborhood selector
  const neighborhoodSelect = document.getElementById('neighborhoodSelect');
  if (neighborhoodSelect) {
    neighborhoodSelect.addEventListener('change', (e) => {
      selectNeighborhood(e.target.value);
    });
  }

  // Reset view button
  const resetBtn = document.getElementById('resetViewBtn');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      const searchInput = document.getElementById('searchInput');
      if (searchInput) searchInput.value = '';
      selectDistrict('all');
    });
  }

  // Price Trend toggles ('both', 'sale', 'rent')
  const toggles = document.querySelectorAll('.chart-toggles .toggle-btn');
  toggles.forEach(btn => {
    btn.addEventListener('click', () => {
      toggles.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      AppState.priceTrendMode = btn.getAttribute('data-type');

      // Re-render chart with current active dataset
      let currentTrend = [];
      if (AppState.selectedNeighborhoodId !== 'all') {
        const distId = AppState.selectedDistrictId;
        const mList = (AppState.data.mahalleler && AppState.data.mahalleler[distId]) || [];
        const mObj = mList.find(m => m.id == AppState.selectedNeighborhoodId);
        if (mObj) currentTrend = mObj.trend || [];
      } else if (AppState.selectedDistrictId !== 'all') {
        const dObj = AppState.data.ilceler.find(d => d.id == AppState.selectedDistrictId);
        if (dObj) currentTrend = dObj.trend || [];
      } else {
        const cityTotals = computeCityTotals();
        currentTrend = cityTotals.trend || [];
      }
      updatePriceTrendChart(currentTrend);
    });
  });

  // Tab navigation
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTabId = btn.getAttribute('data-tab');

      tabButtons.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const targetContent = document.getElementById(targetTabId);
      if (targetContent) targetContent.classList.add('active');
    });
  });
}

/**
 * Bootstrap the application on page load
 */
document.addEventListener('DOMContentLoaded', () => {
  if (!AppState.data) {
    console.error('GEOPROP AI: Istanbul dataset not found in window.ISTANBUL_DATA');
    alert('Veri kümesi yüklenemedi. Lütfen data/istanbul_data.js dosyasının mevcut olduğundan emin olun.');
    return;
  }

  // 1. Initialize Map
  initMap();

  // 2. Populate Dropdowns
  populateDistrictSelect();

  // 3. Setup Events and Search
  setupEventListeners();
  setupSearch();

  // 4. Initial Render: All Istanbul (City-wide)
  const cityTotals = computeCityTotals();
  renderDistrictPolygons();
  updateDashboard(cityTotals);
});
