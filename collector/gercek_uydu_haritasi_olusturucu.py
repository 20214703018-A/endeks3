import concurrent.futures
import io
import json
import math
import os
import urllib.request
from PIL import Image, ImageDraw, ImageFont

ARTIFACT_DIR = "/Users/acar/.gemini/antigravity-ide/brain/e62ed488-8504-44f0-905c-e3fa7d283ea7"
CACHE_DIR = "collector/cache/tiles"
GEOJSON_PATH = "collector/sektor_01_guneybati_ege.geojson"

os.makedirs(CACHE_DIR, exist_ok=True)

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(min(max(lat_deg, -85.0511), 85.0511))
    n = 1 << zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile

def get_tile(zoom, x, y):
    tile_file = os.path.join(CACHE_DIR, f"{zoom}_{x}_{y}.jpg")
    if os.path.exists(tile_file):
        try:
            return Image.open(tile_file).convert("RGBA")
        except Exception:
            pass

    url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{y}/{x}.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            with open(tile_file, "wb") as f:
                f.write(data)
            return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:
        print(f"Warning: Failed to fetch tile {zoom}/{x}/{y}: {e}")
        # fallback empty dark tile
        return Image.new("RGBA", (256, 256), (15, 23, 42, 255))

def get_font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFPro.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def build_satellite_map(data, bbox, zoom, output_path, title, subtitle):
    min_lon, min_lat, max_lon, max_lat = bbox

    # Determine tile range
    x_min_f, y_max_f = deg2num(min_lat, min_lon, zoom)
    x_max_f, y_min_f = deg2num(max_lat, max_lon, zoom)

    x_start = int(math.floor(x_min_f))
    x_end = int(math.ceil(x_max_f))
    y_start = int(math.floor(y_min_f))
    y_end = int(math.ceil(y_max_f))

    num_x = x_end - x_start
    num_y = y_end - y_start
    total_w = num_x * 256
    total_h = num_y * 256

    print(f"Fetching {num_x}x{num_y} = {num_x * num_y} satellite tiles at zoom {zoom} ({total_w}x{total_h} px)...")

    # Download in parallel
    tile_coords = [(x, y) for x in range(x_start, x_end) for y in range(y_start, y_end)]
    tiles_map = {}

    def fetch_one(coord):
        x, y = coord
        return coord, get_tile(zoom, x, y)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        results = executor.map(fetch_one, tile_coords)
        for coord, tile_img in results:
            tiles_map[coord] = tile_img

    # Stitch tiles into base satellite image
    base_img = Image.new("RGBA", (total_w, total_h), (10, 15, 29, 255))
    for (x, y), tile in tiles_map.items():
        px = (x - x_start) * 256
        py = (y - y_start) * 256
        base_img.paste(tile, (px, py))

    # Pixel transform function
    def to_pixel(lon, lat):
        xf, yf = deg2num(lat, lon, zoom)
        return (xf - x_start) * 256, (yf - y_start) * 256

    # Overlay for semi-transparent polygons
    poly_overlay = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw_poly = ImageDraw.Draw(poly_overlay)

    layers = {
        "ORMAN_ALANI": [],
        "SIT_VE_KORUNAN_ALAN": [],
        "GOL_BARAJ_HAZNE": [],
        "MEVCUT_YOL": [],
        "SU_YOLU_DERE": [],
        "SAHIL_SERIDI": [],
        "ELEKTRIK_HATTI": [],
        "DİRİ_FAY_HATTI": [],
        "SU_KUYUSU": [],
        "CESME_ICME_SUYU": [],
        "DOGAL_PINAR": []
    }

    for feat in data.get("features", []):
        k = feat.get("properties", {}).get("katman")
        if k in layers:
            layers[k].append(feat)

    def extract_rings(geom):
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon":
            return [r for r in coords if isinstance(r, list) and len(r) > 0 and isinstance(r[0], list)]
        elif gtype == "MultiPolygon":
            rings = []
            for poly in coords:
                if isinstance(poly, list):
                    for r in poly:
                        if isinstance(r, list) and len(r) > 0 and isinstance(r[0], list):
                            rings.append(r)
            return rings
        return []

    def extract_lines(geom):
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "LineString":
            return [coords] if (coords and isinstance(coords[0], list)) else []
        elif gtype == "MultiLineString":
            return [l for l in coords if (l and isinstance(l[0], list))]
        return []

    # 1. Forest polygons (Deep emerald semi-transparent with green border)
    for f in layers["ORMAN_ALANI"]:
        for ring in extract_rings(f.get("geometry", {})):
            pts = [to_pixel(p[0], p[1]) for p in ring if len(p) >= 2]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(22, 163, 74, 90), outline=(34, 197, 94, 220), width=1)

    # 2. Sit & Protected Conservation Areas (Purple semi-transparent)
    for f in layers["SIT_VE_KORUNAN_ALAN"]:
        for ring in extract_rings(f.get("geometry", {})):
            pts = [to_pixel(p[0], p[1]) for p in ring if len(p) >= 2]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(147, 51, 234, 110), outline=(192, 132, 252, 240), width=2)

    # 3. Lakes & Reservoirs (Deep Blue)
    for f in layers["GOL_BARAJ_HAZNE"]:
        for ring in extract_rings(f.get("geometry", {})):
            pts = [to_pixel(p[0], p[1]) for p in ring if len(p) >= 2]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(2, 132, 199, 140), outline=(56, 189, 248, 230), width=2)

    # Composite polygons onto satellite image
    img = Image.alpha_composite(base_img, poly_overlay)
    draw = ImageDraw.Draw(img)

    # 4. Roads (Slate / Off-white for contrast against satellite imagery)
    for f in layers["MEVCUT_YOL"]:
        for line in extract_lines(f.get("geometry", {})):
            if len(line) >= 2:
                pts = [to_pixel(p[0], p[1]) for p in line if len(p) >= 2]
                draw.line(pts, fill=(226, 232, 240, 160), width=2)

    # 5. Streams & Waterways (Vibrant Sky Blue)
    for f in layers["SU_YOLU_DERE"]:
        for line in extract_lines(f.get("geometry", {})):
            if len(line) >= 2:
                pts = [to_pixel(p[0], p[1]) for p in line if len(p) >= 2]
                draw.line(pts, fill=(56, 189, 248, 230), width=2)

    # 6. Coastline (Crisp Bright Cyan line along the satellite shore)
    for f in layers["SAHIL_SERIDI"]:
        for line in extract_lines(f.get("geometry", {})):
            if len(line) >= 2:
                pts = [to_pixel(p[0], p[1]) for p in line if len(p) >= 2]
                draw.line(pts, fill=(6, 182, 212, 255), width=2)

    # 7. TEİAŞ High Voltage Power Lines (Bright Electric Yellow with outer glow)
    for f in layers["ELEKTRIK_HATTI"]:
        for line in extract_lines(f.get("geometry", {})):
            if len(line) >= 2:
                pts = [to_pixel(p[0], p[1]) for p in line if len(p) >= 2]
                draw.line(pts, fill=(234, 179, 8, 90), width=7)
                draw.line(pts, fill=(250, 204, 21, 255), width=3)

    # 8. Active Fault Traces / Diri Fay Hatları (Glowing Bright Red)
    for f in layers["DİRİ_FAY_HATTI"]:
        for line in extract_lines(f.get("geometry", {})):
            if len(line) >= 2:
                pts = [to_pixel(p[0], p[1]) for p in line if len(p) >= 2]
                draw.line(pts, fill=(239, 68, 68, 120), width=9)
                draw.line(pts, fill=(255, 30, 30, 255), width=4)

    # 9. Point Features (Wells, Fountains, Springs)
    # Wells (Deep blue circle with white ring)
    for f in layers["SU_KUYUSU"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_pixel(c[0], c[1])
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(2, 132, 199, 255), outline=(255, 255, 255, 255), width=2)

    # Fountains (Cyan circle)
    for f in layers["CESME_ICME_SUYU"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_pixel(c[0], c[1])
            draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(6, 182, 212, 255), outline=(255, 255, 255, 255), width=2)

    # Natural Springs (Emerald circle)
    for f in layers["DOGAL_PINAR"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_pixel(c[0], c[1])
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(16, 185, 129, 255), outline=(255, 255, 255, 255), width=2)

    # 10. Place Labels with dark halos
    place_labels = [
        ("BODRUM", 27.4287, 37.0344),
        ("GÖKOVA KÖRFEZİ", 27.8500, 37.0100),
        ("MARMARİS", 28.2700, 36.8550),
        ("DATÇA", 27.6850, 36.7230),
        ("BOZBURUN", 28.0500, 36.6800),
        ("RODOS ADASI", 28.1000, 36.2000),
        ("KOS (İSTANKÖY)", 27.1500, 36.8500),
        ("SANTORİNİ", 25.4300, 36.4000),
        ("SİMİ", 27.8300, 36.5900),
        ("TİLOS", 27.3700, 36.4200)
    ]

    f_place = get_font(24, bold=True)
    for name, plon, plat in place_labels:
        if min_lon <= plon <= max_lon and min_lat <= plat <= max_lat:
            px, py = to_pixel(plon, plat)
            for dx in [-2, -1, 0, 1, 2]:
                for dy in [-2, -1, 0, 1, 2]:
                    draw.text((px + dx, py + dy), name, fill=(0, 0, 0, 255), font=f_place)
            draw.text((px, py), name, fill=(255, 255, 255, 255), font=f_place)

    # 11. Header Panel
    pad = 30
    hdr_w = min(1000, total_w - 60)
    hdr_h = 135
    draw.rounded_rectangle([pad, pad, pad + hdr_w, pad + hdr_h], radius=14, fill=(15, 23, 42, 235), outline=(56, 189, 248, 180), width=2)
    f_title = get_font(30, bold=True)
    f_sub = get_font(18)
    draw.text((pad + 24, pad + 18), title, fill=(255, 255, 255, 255), font=f_title)
    draw.text((pad + 24, pad + 70), subtitle, fill=(148, 163, 184, 255), font=f_sub)
    draw.text((pad + 24, pad + 98), "Altlık: Esri World Imagery (Gerçek Yüksek Çözünürlüklü Uydu Fotoğrafı)", fill=(56, 189, 248, 255), font=f_sub)

    # 12. Legend Box
    leg_w = 520
    leg_h = 500
    leg_x = total_w - leg_w - pad
    leg_y = total_h - leg_h - pad
    draw.rounded_rectangle([leg_x, leg_y, leg_x + leg_w, leg_y + leg_h], radius=14, fill=(15, 23, 42, 240), outline=(255, 255, 255, 50), width=2)
    f_leg_hdr = get_font(20, bold=True)
    f_leg = get_font(17)
    draw.text((leg_x + 20, leg_y + 16), "GERÇEK UYDU ÜZERİNDE CBS KATMANLARI", fill=(56, 189, 248, 255), font=f_leg_hdr)

    def in_bbox(feat):
        g = feat.get("geometry", {})
        c = g.get("coordinates")
        gt = g.get("type")
        if gt == "Point": pt = c
        elif gt == "LineString": pt = c[0]
        elif gt == "Polygon": pt = c[0][0]
        else: return False
        return min_lon <= pt[0] <= max_lon and min_lat <= pt[1] <= max_lat

    legend_items = [
        ("Diri Fay Hatları (MTA / GEM)", (255, 30, 30), f"{sum(1 for f in layers['DİRİ_FAY_HATTI'] if in_bbox(f))} segment"),
        ("TEİAŞ Elektrik İletim Hatları", (250, 204, 21), f"{sum(1 for f in layers['ELEKTRIK_HATTI'] if in_bbox(f))} ENH"),
        ("Sahil & Kıyı Şeridi (3621 Kıyı Kanunu)", (6, 182, 212), f"{sum(1 for f in layers['SAHIL_SERIDI'] if in_bbox(f))} segment"),
        ("Devlet Orman Alanları (OGM)", (34, 197, 94), f"{sum(1 for f in layers['ORMAN_ALANI'] if in_bbox(f))} poligon"),
        ("Sit & Korunan Alanlar (ÖÇK / Milli Park)", (192, 132, 252), f"{sum(1 for f in layers['SIT_VE_KORUNAN_ALAN'] if in_bbox(f))} bölge"),
        ("Akarsu & Kuru Dere Yatakları", (56, 189, 248), f"{sum(1 for f in layers['SU_YOLU_DERE'] if in_bbox(f))} yatak"),
        ("Göl & Su Hazneleri / Barajlar", (2, 132, 199), f"{sum(1 for f in layers['GOL_BARAJ_HAZNE'] if in_bbox(f))} göl"),
        ("Karayolları & Mevcut Yollar", (226, 232, 240), f"{sum(1 for f in layers['MEVCUT_YOL'] if in_bbox(f))} hat"),
        ("Artezyen / Yeraltı Su Kuyusu", (2, 132, 199), f"{sum(1 for f in layers['SU_KUYUSU'] if in_bbox(f))} kuyu"),
        ("İçme Suyu Çeşmesi (Aktif Şebeke)", (6, 182, 212), f"{sum(1 for f in layers['CESME_ICME_SUYU'] if in_bbox(f))} çeşme"),
        ("Doğal Kaynak / Pınar", (16, 185, 129), f"{sum(1 for f in layers['DOGAL_PINAR'] if in_bbox(f))} pınar")
    ]

    curr_y = leg_y + 58
    for label, color, count in legend_items:
        draw.ellipse([leg_x + 20, curr_y + 3, leg_x + 34, curr_y + 17], fill=color, outline=(255, 255, 255, 220), width=1)
        draw.text((leg_x + 44, curr_y), label, fill=(241, 245, 249, 255), font=f_leg)
        draw.text((leg_x + leg_w - 95, curr_y), count, fill=(148, 163, 184, 255), font=f_leg)
        curr_y += 37

    img.save(output_path, "PNG", optimize=True)
    print(f"Saved real satellite map to {output_path} ({total_w}x{total_h})")

def main():
    print("Loading Sector 1 GeoJSON...")
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. High Resolution Focus Map: Marmaris, Datça, Bodrum, Gökova, Bozburun
    # Bbox: Lon 27.0 to 28.5, Lat 36.5 to 37.3
    # Zoom level 11 (~75m per pixel detail, real terrain, roads, coast)
    out1 = os.path.join(ARTIFACT_DIR, "sektor_01_gercek_uydu_marmaris_datca.png")
    build_satellite_map(
        data,
        (27.0, 36.5, 28.5, 37.3),
        zoom=11,
        output_path=out1,
        title="SEKTÖR 1: MARMARİS - DATÇA - BODRUM UYDU HARİTASI",
        subtitle="Diri Faylar, TEİAŞ ENH, Kıyı Kenar, Ormanlar ve Su Kuyuları Gerçek Uydu Üzerinde"
    )

    # 2. Regional Overview Satellite Map: Entire Sector 1
    # Bbox: Lon 24.4 to 28.5, Lat 35.4 to 37.5
    # Zoom level 9
    out2 = os.path.join(ARTIFACT_DIR, "sektor_01_gercek_uydu_genel.png")
    build_satellite_map(
        data,
        (24.4, 35.4, 28.5, 37.5),
        zoom=9,
        output_path=out2,
        title="SEKTÖR 1: GÜNEYBATI EGE GENEL UYDU HARİTASI",
        subtitle="Rodos - Marmaris - Santorini Kıyı & Kırık Hattı | 6.407 Vektör"
    )

if __name__ == "__main__":
    main()
