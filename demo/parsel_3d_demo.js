/* global maplibregl, JSZip */
"use strict";

const SAMPLE_GEOMETRY = {
  type: "Polygon",
  coordinates: [[
    [32.86755, 39.89177], [32.86511, 39.89131], [32.86513, 39.89218],
    [32.86441, 39.8919], [32.86394, 39.89171], [32.86357, 39.89155],
    [32.86303, 39.89129], [32.86258, 39.89107], [32.8626, 39.89099],
    [32.86224, 39.89079], [32.86217, 39.89081], [32.86192, 39.89065],
    [32.86374, 39.88736], [32.86417, 39.88714], [32.86589, 39.88788],
    [32.8672, 39.8866], [32.86739, 39.88752], [32.86744, 39.88776],
    [32.86747, 39.88804], [32.86747, 39.8882], [32.86747, 39.88843],
    [32.86746, 39.88861], [32.86733, 39.88956], [32.86725, 39.8901],
    [32.86722, 39.89031], [32.86722, 39.89063], [32.86724, 39.89085],
    [32.86729, 39.89109], [32.86734, 39.89128], [32.86739, 39.89142],
    [32.86746, 39.89159], [32.86755, 39.89177]
  ]]
};

const DEMO_PARCEL = {
  il: "Ankara",
  ilce: "Çankaya",
  mahalle: "Çankaya",
  ada_no: "5964",
  parsel_no: "6",
  alan_m2: 180657,
  nitelik: "Yerleşke",
  enlem: 39.8900675,
  boylam: 32.86572625,
  geometry: SAMPLE_GEOMETRY,
  source: "Demo poligonu",
  live: false
};

const els = {
  form: document.querySelector("#parcelForm"),
  queryButton: document.querySelector("#queryButton"),
  sampleButton: document.querySelector("#sampleButton"),
  downloadButton: document.querySelector("#downloadButton"),
  kmlButton: document.querySelector("#kmlButton"),
  status: document.querySelector("#statusMessage span:last-child"),
  error: document.querySelector("#errorMessage"),
  title: document.querySelector("#parcelTitle"),
  location: document.querySelector("#locationValue"),
  area: document.querySelector("#areaValue"),
  quality: document.querySelector("#qualityValue"),
  source: document.querySelector("#sourceValue"),
  sourceBadge: document.querySelector("#sourceBadge"),
  visualSubtitle: document.querySelector("#visualSubtitle"),
  rotateLeft: document.querySelector("#rotateLeft"),
  rotateRight: document.querySelector("#rotateRight"),
  resetCamera: document.querySelector("#resetCamera")
};

let currentParcel = structuredClone(DEMO_PARCEL);
let currentPois = [];
let mapReady = false;
let poiRequestId = 0;
let poiLoadPromise = Promise.resolve();

const map = new maplibregl.Map({
  container: "map",
  center: [DEMO_PARCEL.boylam, DEMO_PARCEL.enlem],
  zoom: 15.2,
  pitch: 62,
  bearing: -24,
  maxPitch: 80,
  attributionControl: false,
  canvasContextAttributes: { antialias: true, preserveDrawingBuffer: true },
  style: {
    version: 8,
    sources: {
      satellite: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 19,
        attribution: "Tiles © Esri"
      },
      terrain: {
        type: "raster-dem",
        url: "https://demotiles.maplibre.org/terrain-tiles/tiles.json",
        tileSize: 256,
        encoding: "mapbox"
      }
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#25362e" } },
      { id: "satellite", type: "raster", source: "satellite", paint: { "raster-saturation": -.05, "raster-contrast": .08, "raster-brightness-min": .06, "raster-brightness-max": .96 } },
      { id: "hillshade", type: "hillshade", source: "terrain", paint: { "hillshade-exaggeration": 0.22, "hillshade-shadow-color": "#10261d", "hillshade-highlight-color": "#ffffff" } }
    ]
  }
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

map.on("load", () => {
  map.setTerrain({ source: "terrain", exaggeration: 1.35 });
  map.addSource("selected-parcel", { type: "geojson", data: featureFor(currentParcel.geometry) });
  map.addSource("nearby-pois", { type: "geojson", data: poiFeatureCollection([]) });
  map.addLayer({
    id: "parcel-fill",
    type: "fill",
    source: "selected-parcel",
    paint: {
      "fill-color": "#dfff57",
      "fill-opacity": 0.48
    }
  });
  map.addLayer({
    id: "parcel-outline",
    type: "line",
    source: "selected-parcel",
    paint: { "line-color": "#123f31", "line-width": 5, "line-opacity": 1 }
  });
  map.addLayer({
    id: "parcel-highlight",
    type: "line",
    source: "selected-parcel",
    paint: { "line-color": "#efffa6", "line-width": 2, "line-opacity": 1 }
  });
  ["health", "education", "mall", "transit", "park"].forEach((category) => {
    map.addImage(`poi-${category}`, createPoiPin(category), { pixelRatio: 2 });
  });
  map.addLayer({
    id: "nearby-poi-pins",
    type: "symbol",
    source: "nearby-pois",
    layout: {
      "icon-image": ["concat", "poi-", ["get", "category"]],
      "icon-anchor": "bottom",
      "icon-size": 1,
      "icon-allow-overlap": true,
      "icon-ignore-placement": true
    }
  });
  mapReady = true;
  focusParcel(false);
  poiLoadPromise = loadNearbyPois(currentParcel);
});

map.on("error", (event) => {
  if (!mapReady && event && event.error) {
    setError("Harita katmanı yüklenemedi. İnternet bağlantısını kontrol edip sayfayı yenileyin.");
  }
});

function featureFor(geometry) {
  return { type: "Feature", properties: {}, geometry };
}

function poiFeatureCollection(points) {
  return {
    type: "FeatureCollection",
    features: points.map((point) => ({
      type: "Feature",
      properties: { category: point.category },
      geometry: { type: "Point", coordinates: [point.lon, point.lat] }
    }))
  };
}

function createPoiPin(category) {
  const colors = { health: "#e33a45", education: "#326fd1", mall: "#f08a24", transit: "#6b4bc5", park: "#24975a" };
  const canvas = document.createElement("canvas");
  canvas.width = 80;
  canvas.height = 96;
  const ctx = canvas.getContext("2d");
  ctx.shadowColor = "rgba(0,0,0,.35)";
  ctx.shadowBlur = 7;
  ctx.shadowOffsetY = 4;
  ctx.fillStyle = colors[category];
  ctx.beginPath();
  ctx.arc(40, 36, 29, Math.PI * .12, Math.PI * .88, true);
  ctx.lineTo(40, 91);
  ctx.closePath();
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.strokeStyle = "#ffffff";
  ctx.fillStyle = "#ffffff";
  ctx.lineWidth = 6;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  if (category === "health") {
    ctx.beginPath(); ctx.moveTo(40, 21); ctx.lineTo(40, 52); ctx.moveTo(25, 36); ctx.lineTo(55, 36); ctx.stroke();
  } else if (category === "education") {
    ctx.beginPath(); ctx.moveTo(20, 31); ctx.lineTo(40, 21); ctx.lineTo(60, 31); ctx.lineTo(40, 41); ctx.closePath(); ctx.fill();
    ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(28, 37); ctx.lineTo(28, 48); ctx.quadraticCurveTo(40, 55, 52, 48); ctx.lineTo(52, 37); ctx.stroke();
  } else if (category === "mall") {
    ctx.lineWidth = 5; ctx.strokeRect(24, 30, 32, 25); ctx.beginPath(); ctx.arc(40, 30, 10, Math.PI, 0); ctx.stroke();
  } else if (category === "transit") {
    ctx.lineWidth = 4; ctx.strokeRect(23, 20, 34, 33); ctx.beginPath(); ctx.moveTo(23, 38); ctx.lineTo(57, 38); ctx.stroke();
    ctx.beginPath(); ctx.arc(30, 55, 4, 0, Math.PI * 2); ctx.arc(50, 55, 4, 0, Math.PI * 2); ctx.fill();
  } else {
    ctx.beginPath(); ctx.moveTo(40, 18); ctx.lineTo(22, 45); ctx.lineTo(34, 45); ctx.lineTo(27, 56); ctx.lineTo(53, 56); ctx.lineTo(46, 45); ctx.lineTo(58, 45); ctx.closePath(); ctx.fill();
  }
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function geometryBounds(geometry) {
  const bounds = new maplibregl.LngLatBounds();
  const visit = (node) => {
    if (Array.isArray(node) && typeof node[0] === "number" && typeof node[1] === "number") {
      bounds.extend(node);
      return;
    }
    if (Array.isArray(node)) node.forEach(visit);
  };
  visit(geometry.coordinates);
  return bounds;
}

function focusParcel(animated = true) {
  if (!mapReady || !currentParcel.geometry) return;
  map.fitBounds(geometryBounds(currentParcel.geometry), {
    padding: { top: 95, right: 95, bottom: 95, left: 95 },
    pitch: 54,
    bearing: -24,
    maxZoom: 17.5,
    duration: animated ? 1200 : 0
  });
}

function focusScene(animated = true) {
  if (!mapReady || !currentParcel.geometry) return;
  const bounds = geometryBounds(currentParcel.geometry);
  currentPois.forEach((point) => bounds.extend([point.lon, point.lat]));
  map.fitBounds(bounds, {
    padding: { top: 95, right: 95, bottom: 95, left: 95 },
    pitch: 54,
    bearing: -24,
    maxZoom: 15.8,
    duration: animated ? 1100 : 0
  });
}

function formatArea(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0
    ? `${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(numeric)} m²`
    : "Alan bilgisi yok";
}

function safeText(value, fallback = "—") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function renderParcel(parcel) {
  currentParcel = parcel;
  if (mapReady) {
    map.getSource("selected-parcel").setData(featureFor(parcel.geometry));
    currentPois = [];
    map.getSource("nearby-pois").setData(poiFeatureCollection([]));
    focusParcel(true);
    poiLoadPromise = loadNearbyPois(parcel);
  }
  els.title.textContent = `Ada ${safeText(parcel.ada_no)} · Parsel ${safeText(parcel.parsel_no)}`;
  els.location.textContent = [parcel.il, parcel.ilce, parcel.mahalle].filter(Boolean).join(" / ") || "Konum bilgisi yok";
  els.area.textContent = formatArea(parcel.alan_m2);
  els.quality.textContent = safeText(parcel.nitelik, "Belirtilmemiş");
  els.source.textContent = parcel.live ? "TKGM MEGSİS" : "Demo poligonu";
  els.sourceBadge.textContent = parcel.live ? "KAYNAKTA DOĞRULANDI" : "TANITIM VERİSİ";
  els.visualSubtitle.textContent = parcel.live ? "TKGM sınırı · etiketsiz uydu katmanı" : "Etiketsiz uydu katmanı · tanıtım modu";
}

async function loadNearbyPois(parcel) {
  const requestId = ++poiRequestId;
  const lat = Number(parcel.enlem);
  const lon = Number(parcel.boylam);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return [];
  try {
    const response = await fetch(`/api/yakin-poiler?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&radius=1800`);
    const payload = await response.json();
    if (!response.ok || payload.status !== "success") throw new Error(payload.message || "Yakın noktalar alınamadı.");
    if (requestId !== poiRequestId) return [];
    currentPois = Array.isArray(payload.points) ? payload.points : [];
    if (mapReady) {
      map.getSource("nearby-pois").setData(poiFeatureCollection(currentPois));
      focusScene(true);
    }
    setStatus(`${currentPois.length} yakın değer noktası simgesel pinlerle eklendi.`);
    return currentPois;
  } catch (error) {
    if (requestId === poiRequestId) setError(`Yakın çevre pinleri alınamadı: ${error.message}`);
    return [];
  }
}

function setStatus(message) {
  els.status.textContent = message;
  els.error.hidden = true;
}

function setError(message) {
  els.error.textContent = message;
  els.error.hidden = false;
}

function normalizedGeometry(value) {
  const geometry = typeof value === "string" ? JSON.parse(value) : value;
  if (!geometry || !["Polygon", "MultiPolygon"].includes(geometry.type) || !Array.isArray(geometry.coordinates)) {
    throw new Error("Kaynak yanıtında çizilebilir parsel poligonu bulunamadı.");
  }
  return geometry;
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.queryButton.disabled = true;
  setStatus("TKGM parsel sınırı sorgulanıyor…");

  const raw = Object.fromEntries(new FormData(els.form).entries());
  const params = new URLSearchParams();
  Object.entries(raw).forEach(([key, value]) => {
    if (String(value).trim()) params.set(key, String(value).trim());
  });

  try {
    const response = await fetch(`/api/parsel-sorgu?${params.toString()}`, { headers: { Accept: "application/json" } });
    const payload = await response.json();
    if (!response.ok || payload.status !== "success" || !payload.data?.parsel) {
      throw new Error(payload.mesaj || payload.hata || "Parsel kaydı bulunamadı.");
    }
    const sourceParcel = payload.data.parsel;
    const parcel = {
      ...sourceParcel,
      geometry: normalizedGeometry(sourceParcel.poligon_geojson || sourceParcel.geometry),
      source: "TKGM MEGSİS",
      live: true
    };
    renderParcel(parcel);
    setStatus(`Parsel doğrulandı ve 3D görünüm hazırlandı (${payload.sure_ms ?? "—"} ms).`);
  } catch (error) {
    setError(`${error.message} Örnek demo poligonu ekranda korunuyor.`);
    els.status.textContent = "Canlı sorgu tamamlanamadı.";
  } finally {
    els.queryButton.disabled = false;
  }
});

els.sampleButton.addEventListener("click", () => {
  renderParcel(structuredClone(DEMO_PARCEL));
  setStatus("Örnek tanıtım poligonu yüklendi.");
});

els.rotateLeft.addEventListener("click", () => map.easeTo({ bearing: map.getBearing() - 35, duration: 550 }));
els.rotateRight.addEventListener("click", () => map.easeTo({ bearing: map.getBearing() + 35, duration: 550 }));
els.resetCamera.addEventListener("click", () => focusScene(true));

els.kmlButton.addEventListener("click", () => {
  const geometry = currentParcel.geometry;
  if (!geometry) return setError("KML oluşturmak için parsel geometrisi bulunamadı.");
  const rings = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  const placemarks = rings.map((polygon, index) => {
    const coordinates = polygon[0].map(([lng, lat]) => `${lng},${lat},0`).join(" ");
    return `<Placemark><name>${xmlEscape(parcelDisplayName())}${rings.length > 1 ? ` - ${index + 1}` : ""}</name><styleUrl>#parcelStyle</styleUrl><Polygon><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode><outerBoundaryIs><LinearRing><coordinates>${coordinates}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>`;
  }).join("");
  const kml = `<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>${xmlEscape(parcelDisplayName())}</name><Style id="parcelStyle"><LineStyle><color>ff314012</color><width>5</width></LineStyle><PolyStyle><color>8839ffdf</color></PolyStyle></Style>${placemarks}</Document></kml>`;
  downloadBlob(new Blob([kml], { type: "application/vnd.google-earth.kml+xml;charset=utf-8" }), `${fileStem()}.kml`);
  setStatus("Google Earth ile açılabilir KML dosyası indirildi.");
});

els.downloadButton.addEventListener("click", async () => {
  if (!mapReady) return setError("Harita henüz görsel indirmeye hazır değil.");
  if (typeof JSZip === "undefined") return setError("ZIP bileşeni yüklenemedi. İnternet bağlantısını kontrol edin.");
  els.downloadButton.disabled = true;
  setStatus("Dört farklı 3D uydu açısı hazırlanıyor…");
  try {
    await poiLoadPromise;
    if (!currentPois.length) throw new Error("Yakın çevre pinleri henüz hazır değil.");
    const originalBearing = map.getBearing();
    const zip = new JSZip();
    const angles = [
      { name: "kuzey", bearing: 0 },
      { name: "dogu", bearing: 90 },
      { name: "guney", bearing: 180 },
      { name: "bati", bearing: 270 }
    ];
    for (let index = 0; index < angles.length; index += 1) {
      const angle = angles[index];
      setStatus(`${index + 1}/4: ${angle.name} açısı hazırlanıyor…`);
      map.jumpTo({ bearing: angle.bearing, pitch: 54 });
      await waitForMapIdle();
      const blob = await captureCleanMap();
      zip.file(`${fileStem()}-${angle.name}.png`, blob);
    }
    map.jumpTo({ bearing: originalBearing, pitch: 54 });
    const archive = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } });
    downloadBlob(archive, `${fileStem()}-4-uydu-acisi-${timestampStem()}.zip`);
    setStatus("Kuzey, doğu, güney ve batı açıları ZIP olarak indirildi.");
  } catch (error) {
    setError(`Dört açı oluşturulamadı: ${error.message}`);
  } finally {
    els.downloadButton.disabled = false;
  }
});

async function captureCleanMap() {
  const output = document.createElement("canvas");
  output.width = 1600;
  output.height = 1000;
  const context = output.getContext("2d");
  const mapCanvas = map.getCanvas();
  context.drawImage(mapCanvas, 0, 0, mapCanvas.width, mapCanvas.height, 0, 0, output.width, output.height);

  // Uydu sağlayıcısının zorunlu atfı görsel üzerinde korunur.
  context.fillStyle = "rgba(0,0,0,.56)";
  context.fillRect(1330, 964, 270, 36);
  context.fillStyle = "rgba(255,255,255,.94)";
  context.textAlign = "right";
  context.font = "500 15px Arial, sans-serif";
  context.fillText("Satellite imagery © Esri", 1584, 987);

  const blob = await new Promise((resolve) => output.toBlob(resolve, "image/png", 1));
  if (!blob) throw new Error("Tarayıcı PNG çıktısını oluşturamadı.");
  return blob;
}

function waitForMapIdle() {
  return new Promise((resolve) => {
    if (map.loaded() && !map.isMoving()) return requestAnimationFrame(() => requestAnimationFrame(resolve));
    map.once("idle", () => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

function parcelDisplayName() {
  return `Ada ${safeText(currentParcel.ada_no)} Parsel ${safeText(currentParcel.parsel_no)}`;
}

function fileStem() {
  return [currentParcel.il, currentParcel.ilce, `ada-${currentParcel.ada_no}`, `parsel-${currentParcel.parsel_no}`]
    .filter(Boolean)
    .join("-")
    .toLocaleLowerCase("tr-TR")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ı/g, "i")
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-");
}

function timestampStem() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function xmlEscape(value) {
  return safeText(value).replace(/[<>&'\"]/g, (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[char]));
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

renderParcel(currentParcel);
