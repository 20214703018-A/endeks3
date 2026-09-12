import json
import math
import os
from PIL import Image, ImageDraw, ImageFont

ARTIFACT_DIR = "/Users/acar/.gemini/antigravity-ide/brain/e62ed488-8504-44f0-905c-e3fa7d283ea7"
GEOJSON_PATH = "collector/sektor_01_guneybati_ege.geojson"

def load_data():
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def mercator_y(lat):
    lat_rad = math.radians(min(max(lat, -85.0), 85.0))
    return math.log(math.tan(math.pi / 4 + lat_rad / 2))

def get_font(size, bold=False):
    # Try system fonts on macOS
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFPro.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
        "/Library/Fonts/Arial.ttf"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def render_map(data, bbox, output_path, title, subtitle, width=2800, height=1800):
    min_lon, min_lat, max_lon, max_lat = bbox
    min_my = mercator_y(min_lat)
    max_my = mercator_y(max_lat)

    pad_x = 120
    pad_y = 100
    map_w = width - 2 * pad_x
    map_h = height - 2 * pad_y

    def to_xy(lon, lat):
        x = pad_x + (lon - min_lon) / (max_lon - min_lon) * map_w
        my = mercator_y(lat)
        y = height - pad_y - (my - min_my) / (max_my - min_my) * map_h
        return x, y

    # Base image
    img = Image.new("RGBA", (width, height), (10, 15, 29, 255))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_base = ImageDraw.Draw(img)
    draw_poly = ImageDraw.Draw(overlay)

    # 1. Subtle Grid Lines
    grid_font = get_font(20)
    for lon_tick in [min_lon + i * (max_lon - min_lon) / 8 for i in range(1, 8)]:
        gx, _ = to_xy(lon_tick, min_lat)
        draw_base.line([(gx, pad_y), (gx, height - pad_y)], fill=(30, 41, 59, 140), width=1)
        draw_base.text((gx - 35, height - pad_y + 12), f"{lon_tick:.2f}°E", fill=(148, 163, 184, 200), font=grid_font)

    for lat_tick in [min_lat + i * (max_lat - min_lat) / 6 for i in range(1, 6)]:
        _, gy = to_xy(min_lon, lat_tick)
        draw_base.line([(pad_x, gy), (width - pad_x, gy)], fill=(30, 41, 59, 140), width=1)
        draw_base.text((pad_x - 95, gy - 12), f"{lat_tick:.2f}°N", fill=(148, 163, 184, 200), font=grid_font)

    # Categorize features
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

    # 2. Draw Polygons (Orman, Sit, Göl)
    # Forests (Green)
    for f in layers["ORMAN_ALANI"]:
        g = f.get("geometry", {})
        coords = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            rings = coords
        elif g.get("type") == "MultiPolygon":
            rings = [r for poly in coords for r in poly]
        else:
            continue
        for ring in rings:
            pts = [to_xy(p[0], p[1]) for p in ring]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(22, 101, 52, 90), outline=(34, 197, 94, 200), width=1)

    # Sit & Conservation (Purple)
    for f in layers["SIT_VE_KORUNAN_ALAN"]:
        g = f.get("geometry", {})
        coords = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            rings = coords
        elif g.get("type") == "MultiPolygon":
            rings = [r for poly in coords for r in poly]
        else:
            continue
        for ring in rings:
            pts = [to_xy(p[0], p[1]) for p in ring]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(124, 58, 237, 100), outline=(168, 85, 247, 230), width=2)

    # Lakes & Reservoirs (Deep Blue)
    for f in layers["GOL_BARAJ_HAZNE"]:
        g = f.get("geometry", {})
        coords = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            rings = coords
        elif g.get("type") == "MultiPolygon":
            rings = [r for poly in coords for r in poly]
        else:
            continue
        for ring in rings:
            pts = [to_xy(p[0], p[1]) for p in ring]
            if len(pts) >= 3:
                draw_poly.polygon(pts, fill=(12, 74, 110, 150), outline=(56, 189, 248, 220), width=2)

    # Composite polygons onto base
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # 3. Draw Roads (Slate gray)
    for f in layers["MEVCUT_YOL"]:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            pts = [to_xy(p[0], p[1]) for p in coords]
            draw.line(pts, fill=(100, 116, 139, 180), width=2)

    # 4. Draw Waterways / Streams (Sky blue)
    for f in layers["SU_YOLU_DERE"]:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            pts = [to_xy(p[0], p[1]) for p in coords]
            draw.line(pts, fill=(56, 189, 248, 220), width=2)

    # 5. Draw Coastline (Bright Cyan)
    for f in layers["SAHIL_SERIDI"]:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            pts = [to_xy(p[0], p[1]) for p in coords]
            draw.line(pts, fill=(6, 182, 212, 255), width=3)

    # 6. Draw TEİAŞ Power Transmission Lines (Bright Yellow with glow)
    for f in layers["ELEKTRIK_HATTI"]:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            pts = [to_xy(p[0], p[1]) for p in coords]
            # Outer glow
            draw.line(pts, fill=(234, 179, 8, 80), width=6)
            # Main line
            draw.line(pts, fill=(250, 204, 21, 255), width=3)

    # 7. Draw Diri Fay Hatları (Bright Red / Crimson with glow)
    for f in layers["DİRİ_FAY_HATTI"]:
        coords = f.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            pts = [to_xy(p[0], p[1]) for p in coords]
            # Outer glow
            draw.line(pts, fill=(239, 68, 68, 90), width=8)
            # Inner line
            draw.line(pts, fill=(239, 68, 68, 255), width=4)

    # 8. Draw Point Features (Wells, Springs, Fountains)
    # Wells (Sky Blue circle with white rim)
    for f in layers["SU_KUYUSU"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_xy(c[0], c[1])
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(2, 132, 199, 255), outline=(255, 255, 255, 255), width=2)

    # Fountains (Cyan circle)
    for f in layers["CESME_ICME_SUYU"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_xy(c[0], c[1])
            draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(6, 182, 212, 255), outline=(255, 255, 255, 255), width=2)

    # Natural Springs (Emerald circle)
    for f in layers["DOGAL_PINAR"]:
        c = f.get("geometry", {}).get("coordinates", [])
        if len(c) == 2:
            x, y = to_xy(c[0], c[1])
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(16, 185, 129, 255), outline=(255, 255, 255, 255), width=2)

    # Map Border
    draw.rectangle([pad_x, pad_y, width - pad_x, height - pad_y], outline=(71, 85, 105, 255), width=2)

    # Place Labels to orient the map geographically
    place_labels = [
        ("BODRUM", 27.4287, 37.0344),
        ("GÖKOVA KÖRFEZİ", 27.8500, 37.0100),
        ("MARMARİS", 28.2700, 36.8750),
        ("DATÇA", 27.6850, 36.7230),
        ("BOZBURUN", 28.0500, 36.6800),
        ("RODOS ADASI", 28.1000, 36.2000),
        ("KOS (İSTANKÖY)", 27.1500, 36.8500),
        ("SANTORİNİ", 25.4300, 36.4000),
        ("SİMİ", 27.8300, 36.5900),
        ("TİLOS", 27.3700, 36.4200)
    ]

    f_place = get_font(22, bold=True)
    f_place_sub = get_font(18)
    for name, plon, plat in place_labels:
        if min_lon <= plon <= max_lon and min_lat <= plat <= max_lat:
            px, py = to_xy(plon, plat)
            # draw subtle halo for readability
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)]:
                draw.text((px + dx, py + dy), name, fill=(15, 23, 42, 255), font=f_place)
            draw.text((px, py), name, fill=(248, 250, 252, 220), font=f_place)

    # Header Card (Glassmorphic look)
    hdr_box = [pad_x + 30, pad_y + 30, pad_x + 940, pad_y + 155]
    draw.rounded_rectangle(hdr_box, radius=16, fill=(15, 23, 42, 240), outline=(56, 189, 248, 160), width=2)
    
    f_title = get_font(32, bold=True)
    f_sub = get_font(20)
    draw.text((pad_x + 50, pad_y + 46), title, fill=(255, 255, 255, 255), font=f_title)
    draw.text((pad_x + 50, pad_y + 98), subtitle, fill=(148, 163, 184, 255), font=f_sub)

    # Legend Box (Bottom Right)
    leg_w = 540
    leg_h = 510
    leg_x = width - pad_x - leg_w - 30
    leg_y = height - pad_y - leg_h - 30
    draw.rounded_rectangle([leg_x, leg_y, leg_x + leg_w, leg_y + leg_h], radius=16, fill=(15, 23, 42, 245), outline=(255, 255, 255, 40), width=2)

    f_leg_hdr = get_font(22, bold=True)
    f_leg = get_font(18)
    draw.text((leg_x + 24, leg_y + 20), "CBS KATMAN LEJANTI", fill=(56, 189, 248, 255), font=f_leg_hdr)

    # Count features strictly inside this map's bbox
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
        ("Diri Fay Hatları (MTA / GEM)", (239, 68, 68), f"{sum(1 for f in layers['DİRİ_FAY_HATTI'] if in_bbox(f))} segment"),
        ("TEİAŞ Elektrik İletim Hatları", (250, 204, 21), f"{sum(1 for f in layers['ELEKTRIK_HATTI'] if in_bbox(f))} ENH"),
        ("Sahil & Kıyı Şeridi (3621 Kıyı Kanunu)", (6, 182, 212), f"{sum(1 for f in layers['SAHIL_SERIDI'] if in_bbox(f))} segment"),
        ("Devlet Orman Alanları (OGM)", (34, 197, 94), f"{sum(1 for f in layers['ORMAN_ALANI'] if in_bbox(f))} poligon"),
        ("Sit & Korunan Alanlar (ÖÇK / Milli Park)", (168, 85, 247), f"{sum(1 for f in layers['SIT_VE_KORUNAN_ALAN'] if in_bbox(f))} bölge"),
        ("Akarsu & Kuru Dere Yatakları", (56, 189, 248), f"{sum(1 for f in layers['SU_YOLU_DERE'] if in_bbox(f))} yatak"),
        ("Göl & Su Hazneleri / Rezervuarlar", (2, 132, 199), f"{sum(1 for f in layers['GOL_BARAJ_HAZNE'] if in_bbox(f))} göl"),
        ("Karayolları & Mevcut Yollar", (148, 163, 184), f"{sum(1 for f in layers['MEVCUT_YOL'] if in_bbox(f))} hat"),
        ("Artezyen / Yeraltı Su Kuyusu", (2, 132, 199), f"{sum(1 for f in layers['SU_KUYUSU'] if in_bbox(f))} kuyu"),
        ("İçme Suyu Çeşmesi (Aktif Şebeke)", (6, 182, 212), f"{sum(1 for f in layers['CESME_ICME_SUYU'] if in_bbox(f))} çeşme"),
        ("Doğal Kaynak / Pınar", (16, 185, 129), f"{sum(1 for f in layers['DOGAL_PINAR'] if in_bbox(f))} pınar")
    ]

    curr_y = leg_y + 65
    for label, color, count in legend_items:
        draw.ellipse([leg_x + 24, curr_y + 4, leg_x + 38, curr_y + 18], fill=color, outline=(255, 255, 255, 200), width=1)
        draw.text((leg_x + 48, curr_y), label, fill=(241, 245, 249, 255), font=f_leg)
        draw.text((leg_x + leg_w - 95, curr_y), count, fill=(148, 163, 184, 255), font=f_leg)
        curr_y += 38

    # Save image
    img.save(output_path, "PNG", optimize=True)
    print(f"Rendered map to {output_path} ({width}x{height})")

def main():
    print("Loading Sector 1 GeoJSON...")
    data = load_data()
    total = len(data.get("features", []))
    print(f"Loaded {total} features.")

    # 1. Overview Map (Entire Southwest Aegean Sector)
    # Bbox: 24.4 to 28.5 Lon, 35.4 to 37.5 Lat
    overview_bbox = (24.4, 35.4, 28.5, 37.5)
    out1 = os.path.join(ARTIFACT_DIR, "sektor_01_genel_vektor_haritasi.png")
    render_map(
        data,
        overview_bbox,
        out1,
        "SEKTÖR 1: GÜNEYBATI EGE HAVZASI",
        f"Rodos - Marmaris - Datça - Bodrum | Toplam {total:,} Vektör Geometrisi",
        width=2800,
        height=1800
    )

    # 2. Zoomed Map (Datça, Marmaris, Bodrum, Gökova, Bozburun)
    # Bbox: 27.0 to 28.6 Lon, 36.5 to 37.3 Lat
    zoom_bbox = (27.0, 36.5, 28.6, 37.35)
    out2 = os.path.join(ARTIFACT_DIR, "sektor_01_marmaris_datca_zoom.png")
    render_map(
        data,
        zoom_bbox,
        out2,
        "SEKTÖR 1 ODAK: MARMARİS - DATÇA - BODRUM",
        "Kıyı Kenar, Diri Fay Hatları, ENH Hatları, Orman ve Su Kuyuları Detayı",
        width=2600,
        height=1600
    )

if __name__ == "__main__":
    main()
