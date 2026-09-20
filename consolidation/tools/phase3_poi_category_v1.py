#!/usr/bin/env python3
"""
POI kategori çıkarımı v1 — "Ticari Mekan" gibi genel etiketli mekânların kategorisini isim (+arama terimi) üzerinden türetir.
Ham etiket (raw_ana_kategori) DEĞİŞMEZ; çıktı ayrı tablo: predicted_category, confidence, method, reasons, second_guess.

Katmanlar (sırayla):
 1) RULE   : Türkçe anahtar kelime + marka sözlüğü (mappings/poi_taxonomy_v1.json), en özgül kural önce; gerekçe = eşleşen kelime.
 2) MODEL  : karakter n-gram TF-IDF + lojistik regresyon; eğitim = zaten etiketli 255 K mekân (52 sınıf ≥200 örnek); %20 tutulan veride ölçüm.
 3) SEARCH : arama_terimi'nin kategori eki (zayıf; yalnız REVIEW).
Skor bantları STANDARD §6 ile aynı; model p≥0.85 → 'high', 0.70–0.85 → review, altı → unresolved.
Çıktı: mappings/poi_category_predictions_v1.parquet, validation/poi_category_v1_report.md, validation/golden/poi_category_golden_v1.csv
"""
import json, re, random, unicodedata, datetime as dt, hashlib, collections, time
from pathlib import Path
import duckdb, pyarrow as pa, pyarrow.parquet as pq
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
V = "POI_CATEGORY_V1_3"; SEED = 20260919
random.seed(SEED); np.random.seed(SEED)

# --- taksonomi: (sektör, kategori, anahtar kelimeler[normalize], markalar) — özgülden genele ---
TAX = [
    ("Yeme-İçme", "Kokoreççi & Sakatatçı", ["kokorec", "kokoreç", "iskembe", "işkembe", "sakatat", "midye", "kelle paca", "kelle paça", "paca", "paça"], []),
    ("Yeme-İçme", "Çiğ Köfteci", ["cig kofte", "çiğ köfte", "cigkofte", "çiğköfte", "oses", "komagene"], ["Oses", "Komagene"]),
    ("Yeme-İçme", "Dönerci", ["doner", "döner", "iskender", "tantuni"], ["Bursa İskender"]),
    ("Yeme-İçme", "Kebapçı & Ocakbaşı", ["kebap", "kebab", "ocakbasi", "ocakbaşı", "ciger", "ciğer", "kavurma", "kavurmaci", "kavurmacı", "izgara", "ızgara", "mangal", "kofteci", "köfteci", "kofte", "köfte", "adana", "urfa"], []),
    ("Yeme-İçme", "Pide & Lahmacun", ["pide", "lahmacun", "pidecisi", "etli ekmek"], []),
    ("Yeme-İçme", "Hamburgerci & Fast Food", ["burger", "hamburger", "fast food", "fastfood", "pizza", "waffle", "hot dog", "dürüm", "durum"], ["Burger King", "McDonald", "Popeyes", "Domino", "KFC", "Little Caesars", "Pizza Hut", "Subway"]),
    ("Yeme-İçme", "Tost & Büfe", ["tost", "bufe", "büfe", "sandvic", "sandviç", "kumru", "gozleme", "gözleme"], []),
    ("Yeme-İçme", "Balık & Deniz Ürünleri", ["balik", "balık", "deniz urun", "deniz ürün", "levrek", "cupra", "çupra", "hamsi"], []),
    ("Yeme-İçme", "Tatlıcı & Baklavacı", ["baklava", "kunefe", "künefe", "tatli", "tatlı", "sutlac", "sütlaç", "kadayif", "kadayıf", "lokum", "dondurma", "gullu", "güllü", "hafiz mustafa"], ["Hafız Mustafa", "Güllüoğlu", "Mado", "Karaköy Güllüoğlu"]),
    ("Yeme-İçme", "Pastane & Fırın & Börekçi", ["pastane", "firin", "fırın", "borek", "börek", "simit", "ekmek", "unlu mamul", "poğaça", "pogaca", "bakery", ""], ["Simit Sarayı", "Komşufırın", "Uno"]),
    ("Yeme-İçme", "3. Nesil Kahveci", ["coffee", "kahve", "roastery", "espresso", "cafe", "kafe", "cay bahcesi", "çay bahçesi", "kahvehane", "kiraathane", "kıraathane"], ["Starbucks", "Kahve Dünyası", "Espressolab", "Gloria Jean", "Caribou", "Coffy", "Petra"]),
    ("Yeme-İçme", "Restoran & Lokanta", ["restaurant", "restoran", "lokanta", "ev yemek", "sofrasi", "sofrası", "yemek", "mutfak", "mutfagi", "mutfağı", "meyhane", "kebaphane", "et evi", "steak", "mantı", "manti", "corba", "çorba", "pilav"], []),
    ("Gıda Perakende", "Süpermarket & Market", ["market", "bakkal", "supermarket", "süpermarket", "sarkuteri", "şarküteri", "grocery"], ["A101", "BİM", "BIM", "ŞOK", "SOK", "Migros", "CarrefourSA", "Carrefour", "Macrocenter", "Metro", "File", "Hakmar", "Tarım Kredi", "Getir"]),
    ("Gıda Perakende", "Manav", ["manav", "meyve", "sebze"], []),
    ("Gıda Perakende", "Kasap", ["kasap", "et market", "et urunleri", "et ürünleri", "tavukcu", "tavukçu"], []),
    ("Gıda Perakende", "Kuruyemiş & Şekerleme", ["kuruyemis", "kuruyemiş", "sekerleme", "şekerleme", "cerez", "çerez", "baharat", "aktar"], ["Tadım", "Peyman"]),
    ("Gıda Perakende", "Su & Tüp Bayii", ["su bayi", "damacana", "tup bayi", "tüp bayi", "aygaz", "ipragaz", "milangaz"], ["Aygaz", "İpragaz", "Milangaz"]),
    ("Kişisel Bakım", "Kuaför & Berber", ["kuafor", "kuaför", "berber", "hair", "sac", "saç", "barber", "coiffeur"], []),
    ("Kişisel Bakım", "Güzellik & Kozmetik", ["guzellik", "güzellik", "beauty", "kozmetik", "epilasyon", "nail", "tirnak", "tırnak", "makyaj", "spa", "masaj", "estetik"], ["Gratis", "Watsons", "Sephora", "Rossmann", "Eve"]),
    ("Sağlık", "Eczane", ["eczane", "pharmacy", "eczanesi"], []),
    ("Sağlık", "Klinik & Muayenehane", ["klinik", "poliklinik", "muayenehane", "hastane", "dis hekimi", "diş hekimi", "dishekimi", "dişhekimi", "ortodonti", "tip merkezi", "tıp merkezi", "fizik tedavi", "psikolog", "veteriner", "optik", "gozluk", "gözlük", "isitme", "işitme", "medikal"], ["Medical Park", "Acıbadem", "Memorial", "Lokman Hekim"]),
    ("Otomotiv", "Oto Servis & Yedek Parça", ["oto", "oto servis", "otomotiv", "lastik", "rot balans", "egzoz", "kaporta", "boya", "yedek parca", "yedek parça", "oto elektrik", "oto yikama", "oto yıkama", "araba yikama", "oto tamir", "sanayi", "motosiklet", "akü", "aku"], ["Bosch Car Service", "Lastik", "Petlas", "Michelin", "Bridgestone"]),
    ("Otomotiv", "Akaryakıt & Otopark", ["petrol", "akaryakit", "akaryakıt", "benzin", "otopark", "ispark", "lpg"], ["Shell", "Opet", "BP", "Petrol Ofisi", "Total", "Aytemiz", "Lukoil", "Alpet", "Türkiye Petrolleri", "Moil"]),
    ("Otomotiv", "Galeri & Oto Kiralama", ["oto galeri", "otogaleri", "oto kiralama", "rent a car", "otomobil", "araç kiralama", "arac kiralama"], []),
    ("Moda", "Giyim & Ayakkabı", ["giyim", "butik", "tekstil", "moda", "ayakkabi", "ayakkabı", "canta", "çanta", "kuyumcu", "saatci", "saatçi", "altin", "altın", "gumus", "gümüş", "optik", "elbise", "abiye", "gelinlik", "cocuk giyim", "çocuk giyim", "ic giyim", "iç giyim", "corap", "çorap", "triko", "kot", "jeans"], ["LC Waikiki", "LCW", "Koton", "DeFacto", "Mavi", "Colin", "Zara", "H&M", "Boyner", "Flo", "Deichmann", "Kinetix", "Penti", "Bershka", "Pull&Bear"]),
    ("Elektronik", "Elektronik & Telefon", ["elektronik", "telefon", "telefoncu", "bilgisayar", "computer", "gsm", "teknoloji", "beyaz esya", "beyaz eşya", "teknik servis", ""], ["Teknosa", "MediaMarkt", "Vatan", "Turkcell", "Vodafone", "Türk Telekom", "Apple", "Samsung", "Arçelik", "Beko", "Vestel", "Bosch"]),
    ("Ev & Yapı", "Mobilya & Ev Dekorasyon", ["mobilya", "dekorasyon", "ev tekstil", "perde", "hali", "halı", "yatak", "koltuk", "mutfak dolabi", "mutfak dolabı", "avize", "zuccaciye", "züccaciye", "cam balkon"], ["IKEA", "Bellona", "İstikbal", "Doğtaş", "Kelebek", "Yataş", "Madame Coco", "English Home", "Karaca", "Tefal", "Koçtaş", "Bauhaus"]),
    ("Ev & Yapı", "Yapı Malzemesi & Nalbur", ["nalbur", "hirdavat", "hırdavat", "yapi market", "yapı market", "yapi malzeme", "yapı malzeme", "insaat", "inşaat", "boya badana", "seramik", "fayans", "aluminyum", "alüminyum", "pvc", "elektrik malzeme", "tesisat", "kombi", "dogalgaz", "doğalgaz", "klima", "sihhi tesisat", "sıhhi tesisat"], []),
    ("Hizmet", "Emlak & Gayrimenkul", ["emlak", "gayrimenkul", "realty", "real estate", "remax", "re/max"], ["RE/MAX", "Century 21", "Coldwell Banker", "Turyap", "Keller Williams"]),
    ("Hizmet", "Finans & Sigorta", ["banka", "bank", "sigorta", "atm", "doviz", "döviz", "finans", "kredi", "muhasebe", "mali musavir", "mali müşavir"], ["Ziraat", "Garanti", "Akbank", "Yapı Kredi", "İş Bankası", "Halkbank", "VakıfBank", "QNB", "Denizbank", "TEB", "ING", "Kuveyt Türk", "Albaraka", "Enpara"]),
    ("Hizmet", "Eğitim & Kurs", ["okul", "kurs", "egitim", "eğitim", "dershane", "anaokulu", "kres", "kreş", "etut", "etüt", "universite", "üniversite", "surucu kursu", "sürücü kursu", "kolej", "akademi", "yurt"], []),
    ("Hizmet", "Kargo & Kurye", ["kargo", "kurye", "lojistik", "nakliyat", "nakliye", "evden eve"], ["Aras", "Yurtiçi", "MNG", "PTT", "Sürat", "UPS", "DHL", "Trendyol Express", "Hepsijet", "Sendeo"]),
    ("Hizmet", "Konaklama", ["otel", "hotel", "pansiyon", "hostel", "konukevi", "motel", "resort", ""], []),
    ("Hizmet", "Temizlik & Kuru Temizleme", ["kuru temizleme", "camasir", "çamaşır", "utu", "ütü", "temizlik", "hali yikama", "halı yıkama"], []),
    ("Hizmet", "Terzi & Ayakkabı Tamir", ["terzi", "tamirci", "ayakkabi tamir", "ayakkabı tamir", "anahtarci", "anahtarcı", "cilingir", "çilingir"], []),
    ("Hizmet", "Hukuk & Danışmanlık", ["avukat", "hukuk", "noter", "danismanlik", "danışmanlık", "musavir", "müşavir"], []),
    ("Hizmet", "Fotoğraf & Matbaa & Kırtasiye", ["fotograf", "fotoğraf", "matbaa", "baski", "baskı", "kirtasiye", "kırtasiye", "kitabevi", "kitap", "reklam", "tabela", "fotokopi"], ["D&R"]),
    ("Hizmet", "Çiçekçi & Hediyelik", ["cicek", "çiçek", "cicekci", "çiçekçi", "hediyelik", "hediye", "oyuncak"], []),
    ("Hizmet", "Spor & Eğlence", ["spor", "fitness", "gym", "hali saha", "halı saha", "yuzme", "yüzme", "pilates", "yoga", "bilardo", "playstation", "internet cafe", "oyun salonu", "sinema", "tiyatro", "düğün", "dugun", ""], ["MACFit", "Sports International"]),
    ("Hizmet", "Evcil Hayvan", ["petshop", "pet shop", "pet ", "evcil", "kopek", "köpek", "kedi mamasi", "kedi maması"], []),
    ("Hizmet", "Kamu & Dini Tesis", ["belediye", "muhtarlik", "muhtarlık", "cami", "camii", "mescit", "kilise", "postane", "karakol", "nufus mudurlugu", "nüfus müdürlüğü", "saglik ocagi", "sağlık ocağı", "asm", "kutuphane", "kütüphane", ""], []),
]


def norm(s):
    if not s: return ""
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))
    s = unicodedata.normalize("NFKD", s); s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9&/. ]+", " ", s)).strip()


NORM_TAX = [(sec, cat, [norm(k) for k in kws], [norm(b) for b in brands]) for sec, cat, kws, brands in TAX]


def _tokens(n):
    return n.split()


def _phrase_hit(phrase, n, toks):
    """Tek kelime: token eşit ya da token = kelime + ≤4 harflik Türkçe ek (kebapci, kuaforu). Çok kelime: kelime sınırlı alt-dizgi."""
    if " " in phrase:
        return (" " + phrase + " ") in (" " + n + " ") or re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"[a-z]{0,4}(?![a-z0-9])", n) is not None
    for t in toks:
        if t == phrase or (t.startswith(phrase) and len(t) - len(phrase) <= 4 and t[len(phrase):].isalpha()):
            return True
    return False


def rule_classify(name):
    n = norm(name); toks = _tokens(n)
    for sec, cat, kws, brands in NORM_TAX:
        for b in brands:
            if b and _phrase_hit(b, n, toks): return sec, cat, f"brand='{b}'"
    for sec, cat, kws, brands in NORM_TAX:
        for k in kws:
            if k and _phrase_hit(k, n, toks): return sec, cat, f"keyword='{k}'"
    return None


SEARCH_HINT = [("restoranlar", "Yeme-İçme"), ("kafeler", "Yeme-İçme"), ("kuaför güzellik", "Kişisel Bakım"), ("otomotiv servisleri", "Otomotiv"), ("marketler", "Gıda Perakende"),
               ("pastaneler", "Yeme-İçme"), ("giyim", "Moda"), ("eczaneler", "Sağlık"), ("dükkanlar", None)]


def main():
    t0 = time.time()
    c = duckdb.connect(str(OUT / "staging" / "geoprop_staging.duckdb"), read_only=True); c.execute("SET memory_limit='2GB'")
    v = "stg.poi_business__stg_google_places_ve_yogunluk__google_places_gozlem"
    rows = c.execute(f"""SELECT raw_google_place_id, arg_max(raw_isim, raw_observed_at), arg_max(raw_ana_kategori, raw_observed_at), arg_max(raw_tum_kategoriler, raw_observed_at), arg_max(raw_arama_terimi, raw_observed_at), arg_max(source_file_id, raw_observed_at)
                          FROM {v} GROUP BY 1""").fetchall()
    print(f"mekân: {len(rows):,} ({time.time()-t0:.0f}s)")
    labeled = [(norm(n), a) for pid, n, a, t, s, f in rows if a and a != "Ticari Mekan" and n]
    cnt = collections.Counter(a for _, a in labeled)
    keep = {a for a, k in cnt.items() if k >= 200}
    X = [n for n, a in labeled]; y = [a if a in keep else "Diğer" for _, a in labeled]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=SEED, stratify=y)
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3, max_features=300000, sublinear_tf=True)
    Xtr_v = vec.fit_transform(Xtr); Xte_v = vec.transform(Xte)
    clf = SGDClassifier(loss="log_loss", alpha=2e-6, max_iter=25, random_state=SEED, n_jobs=4).fit(Xtr_v, ytr)
    pred = clf.predict(Xte_v); acc = accuracy_score(yte, pred)
    proba = clf.predict_proba(Xte_v); pmax = proba.max(axis=1)
    hi = pmax >= 0.85; acc_hi = accuracy_score(np.array(yte)[hi], pred[hi]) if hi.any() else None
    print(f"model: sınıf={len(keep)+1} eğitim={len(Xtr):,} test={len(Xte):,} doğruluk={acc:.3f} | p≥0.85 kapsama={hi.mean():.2%} doğruluk={acc_hi:.3f} ({time.time()-t0:.0f}s)")
    report = classification_report(yte, pred, zero_division=0, output_dict=True)
    # --- Ticari Mekan tahmini ---
    targets = [(pid, n, a, t, s, f) for pid, n, a, t, s, f in rows if a == "Ticari Mekan"]
    names = [norm(n) for _, n, _, _, _, _ in targets]
    P = clf.predict_proba(vec.transform(names)); classes = clf.classes_
    out = []; methods = collections.Counter()
    for i, (pid, n, a, t, s, f) in enumerate(targets):
        r = rule_classify(n)
        top = int(P[i].argmax()); p1 = float(P[i][top]); second = classes[int(np.argsort(P[i])[-2])]
        if r:
            sec, cat, why = r; conf = 0.92; method = "RULE"; band = "high"
            reasons = f"{why}; model_top={classes[top]}({p1:.2f})"
        elif classes[top] != "Diğer" and p1 >= 0.85:
            sec, cat, conf, method, band = None, classes[top], round(p1, 3), "MODEL", "high"; reasons = f"model p={p1:.2f}; second={second}"
        elif classes[top] != "Diğer" and p1 >= 0.70:
            sec, cat, conf, method, band = None, classes[top], round(p1, 3), "MODEL", "review_recommended"; reasons = f"model p={p1:.2f}; second={second}"
        else:
            hint = next((sec_ for key, sec_ in SEARCH_HINT if key in (s or "").lower()), None)
            sec, cat, conf, method, band = hint, None, 0.5 if hint else 0.0, "SEARCH_HINT" if hint else "UNRESOLVED", "review_recommended" if hint else "no_auto_merge"
            reasons = f"arama_terimi='{s}'; model_top={classes[top]}({p1:.2f})"
        methods[method] += 1
        out.append({"google_place_id": pid, "source_file_id": f, "raw_name": n, "raw_ana_kategori": a, "raw_arama_terimi": s, "predicted_sector": sec, "predicted_category": cat, "confidence": conf,
                    "band": band, "method": method, "reasons": reasons, "model_top": classes[top], "model_p": round(p1, 3), "model_second": second, "classifier_version": V,
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    (OUT / "mappings").mkdir(exist_ok=True)
    pq.write_table(pa.Table.from_pylist(out), OUT / "mappings" / "poi_category_predictions_v1.parquet")
    (OUT / "mappings" / "poi_taxonomy_v1.json").write_text(json.dumps({"version": V, "rules_in_order": [{"sector": s, "category": c_, "keywords": k, "brands": b} for s, c_, k, b in TAX],
        "model": {"features": "char_wb tfidf 2-4, min_df=3, max_features=300000", "classifier": "SGD log_loss alpha=2e-6", "train_labels": "raw_ana_kategori≠'Ticari Mekan', sınıf ≥200 örnek, diğerleri 'Diğer'", "holdout_accuracy": acc, "holdout_p>=0.85_coverage": float(hi.mean()), "holdout_p>=0.85_accuracy": acc_hi, "seed": SEED,
                  "caveat": "eğitim etiketlerinin bir kısmı toplayıcının kural setinden geldi (kural izini öğrenir); Google kaynaklı etiketler bağımsız sinyal"}, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, indent=1))
    cat_counts = collections.Counter(o["predicted_category"] for o in out if o["predicted_category"])
    band_counts = collections.Counter(o["band"] for o in out)
    gold = random.sample(out, 200)
    import csv
    (OUT / "validation" / "golden").mkdir(parents=True, exist_ok=True)
    with open(OUT / "validation" / "golden" / "poi_category_golden_v1.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(gold[0].keys()) + ["correct(human)", "correct_category(human)", "verified_by", "verified_at"]); w.writeheader()
        for g in gold: w.writerow({**g, "correct(human)": "", "correct_category(human)": "", "verified_by": "", "verified_at": ""})
    top_rep = sorted(((k, v_) for k, v_ in report.items() if isinstance(v_, dict) and k not in ("macro avg", "weighted avg")), key=lambda kv: -kv[1]["support"])[:15]
    rep = f"""# POI kategori çıkarımı v1 — RAPOR ({dt.datetime.now().date()})

Girdi: {len(rows):,} benzersiz mekân (google_places_gozlem, son gözlem); "Ticari Mekan": {len(targets):,} (%{100*len(targets)/len(rows):.1f}).
Model: {len(keep)+1} sınıf, eğitim {len(Xtr):,} / test {len(Xte):,}; **tutulan veride doğruluk {acc:.3f}**; p≥0,85 olan tahminlerde kapsama %{100*hi.mean():.1f}, doğruluk {acc_hi:.3f}.

| Yöntem | Mekân |
|---|---|
""" + "\n".join(f"| {m} | {n:,} |" for m, n in methods.most_common()) + f"""

| Band | Mekân |
|---|---|
""" + "\n".join(f"| {b} | {n:,} |" for b, n in band_counts.most_common()) + """

## En sık türetilen kategoriler
| Kategori | Mekân |
|---|---|
""" + "\n".join(f"| {k} | {v_:,} |" for k, v_ in cat_counts.most_common(25)) + """

## Model test seti (en büyük 15 sınıf)
| Sınıf | precision | recall | destek |
|---|---|---|---|
""" + "\n".join(f"| {k} | {v_['precision']:.2f} | {v_['recall']:.2f} | {int(v_['support']):,} |" for k, v_ in top_rep) + f"""

Çıktı: mappings/poi_category_predictions_v1.parquet (ham etiket değişmedi; predicted_* ayrı). Taksonomi: mappings/poi_taxonomy_v1.json.
Golden: validation/golden/poi_category_golden_v1.csv (200 rastgele; `correct(human)` = evet/hayır, yanlışsa doğru kategori).
Sınır: model etiketlerinin bir kısmı toplayıcı kural setinden (kural izi); Google kaynaklı etiketler bağımsız. Süre {time.time()-t0:.0f}s.
"""
    (OUT / "validation" / "poi_category_v1_report.md").write_text(rep, encoding="utf-8"); print(rep)


if __name__ == "__main__":
    main()
