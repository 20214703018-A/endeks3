#!/usr/bin/env python3
"""
POI kategori çıkarımı v2 — v1.3 üstüne: (1) veri-güdümlü genişletilmiş taksonomi (ticaret dışı kategoriler dahil), (2) yorum-metni modeli,
(3) genişletilmiş arama-eki haritası. Ham etiket değişmez; her tahminde method/confidence/reasons.
Sıra: RULE(isim) → NAME_MODEL(p≥0.85) → REVIEW_MODEL(p≥0.85) → NAME_MODEL(0.70–0.85, review) → REVIEW_MODEL(0.70–0.85, review) → SEARCH_HINT → UNRESOLVED
Çıktı: mappings/poi_category_predictions_v2.parquet, validation/poi_category_v2_report.md, validation/golden/poi_category_golden_v2.csv (+html)
"""
import json, re, random, datetime as dt, hashlib, collections, time, csv, html as H, sys
from pathlib import Path
import duckdb, pyarrow as pa, pyarrow.parquet as pq, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

OUT = Path.home() / "Desktop" / "GEOPROP_CONSOLIDATION"
sys.path.insert(0, str(OUT / "tools"))
import phase3_poi_category_v1 as v1
V = "POI_CATEGORY_V2"; SEED = 20260919; random.seed(SEED); np.random.seed(SEED)

EXTRA = [  # özgülden genele; v1 listesinin ÖNÜNE eklenir
    ("Yeme-İçme", "Çay Ocağı & Kahvehane", ["cay ocagi", "çay ocağı", "cay evi", "çay evi", "kiraathanesi", "kahvehanesi", "cay bahcesi"], []),
    ("Yeme-İçme", "Pide & Lahmacun", ["etliekmek", "etli ekmek"], []),
    ("Yeme-İçme", "Bar & Lounge", ["lounge", "bar", "pub", "bistro", "meyhane", "nargile", "shisha", "hookah"], []),
    ("Yeme-İçme", "Dinlenme Tesisi", ["dinlenme tesis", "tesisleri", "tesisi", "mola"], []),
    ("Gıda Perakende", "Tekel Bayii", ["tekel", "tobacco", "tütün", "tutun", "sigara"], []),
    ("Gıda Perakende", "Yöresel & Gıda Ürünleri", ["gida", "gıda", "yoresel", "yöresel", "lezzet", "sut urunleri", "süt ürünleri", "sut", "süt", "peynir", "yogurt", "yoğurt", "bal", "zeytin", "tavuk", "yumurta", "organik", "dogal", "doğal", "kuru gida", "kuru gıda", "unlu"], []),
    ("Gıda Perakende", "Ucuzluk & İndirim Market", ["ucuzluk", "indirim", "gross", "toptan gida", "toptan gıda"], []),
    ("Gıda Perakende", "Pazar Yeri & Çarşı", ["pazari", "pazarı", "pazar yeri", "carsi", "çarşı", "carsisi", "çarşısı", "halk pazari"], []),
    ("Perakende", "AVM & Alışveriş Merkezi", ["avm", "alisveris merkezi", "alışveriş merkezi", "alisveris", "alışveriş", "shopping", "outlet", "mall"], []),
    ("Perakende", "Mağaza (Genel Perakende)", ["magaza", "mağaza", "magazasi", "mağazası", "magazalari", "shop", "store", "boutique", "collection", "fashion", "moda", "konfeksiyon", "esarp", "eşarp", "kundura", "aksesuar", "tesettur", "tesettür", "abiye"], []),
    ("Perakende", "Tuhafiye & Çeyiz & Bebek", ["tuhafiye", "ceyiz", "çeyiz", "bebe", "bebek", "kids", "oyuncak", "anne bebek"], []),
    ("Perakende", "Spot & İkinci El Eşya", ["spot", "ikinci el", "2. el", "2.el", "eskici"], []),
    ("Perakende", "Ev Aletleri & Beyaz Eşya", ["beyaz esya", "beyaz eşya", "ev aletleri", "kucuk ev", "küçük ev", "elektro"], ["Arçelik", "Beko", "Vestel", "Bosch", "Siemens", "Samsung", "LG", "Profilo"]),
    ("Elektronik", "Elektronik & Telefon", ["iletisim", "iletişim", "elektronikci", "elektronikçi", "cep telefonu", "telefon aksesuar"], ["Turkcell", "Vodafone", "Türk Telekom"]),
    ("Otomotiv", "Oto Servis & Yedek Parça", ["auto", "motors", "motor", "motorlu", "garage", "garaj", "car", "arac", "araç", "araclar", "araçlar", "yetkili servis", "servisi", "servis", "otomobil", "tuning", "rot", "balans", "fren", "sanayi sitesi"], []),
    ("Otomotiv", "Otoyıkama", ["oto yikama", "oto yıkama", "yikama", "yıkama", "car wash", "detailing"], []),
    ("Otomotiv", "Akaryakıt & Otopark", ["istasyonu", "istasyon"], []),
    ("Ev & Yapı", "Elektrikçi & Elektrik Malzeme", ["elektrik", "elektrikci", "elektrikçi", "aydinlatma", "aydınlatma", "avize"], []),
    ("Ev & Yapı", "Yapı & İnşaat Firması", ["yapi", "yapı", "insaat", "inşaat", "muhendislik", "mühendislik", "mimarlik", "mimarlık", "proje", "tasarim", "tasarım", "sistemleri", "cati", "çatı", "izolasyon", "asansor", "asansör"], []),
    ("Ev & Yapı", "Mobilya & Ev Dekorasyon", ["home", "house", "ev dekor", "dekor", "mutfak", "banyo", "hali", "halı", "perde"], []),
    ("Sanayi & Toptan", "Toptan & Ticaret", ["ticaret", "toptan", "tic.", "ltd", "sti", "şti", "a.s.", "a.ş.", "sanayi", "san.", "pazarlama", "dis ticaret", "dış ticaret", "ithalat", "ihracat", "lojistik", "depo"], []),
    ("Sanayi & Toptan", "Fabrika & Üretim", ["fabrika", "fabrikasi", "fabrikası", "uretim", "üretim", "imalat", "atolye", "atölye", "iplik", "dokuma", "plastik", "metal", "makina", "makine", "kimya", "ambalaj", "mermer", "tekstil san"], []),
    ("Sanayi & Toptan", "Tarım & Hayvancılık", ["tarim", "tarım", "zirai", "ziraat", "tohum", "gubre", "gübre", "hayvancilik", "hayvancılık", "ciftlik", "çiftlik", "sera", "fidan", "yem"], []),
    ("Sağlık", "Sağlık Kabini & Eczane", ["saglik", "sağlık", "sagligi", "sağlığı", "kabini", "tahlil", "laboratuvar", "gorüntüleme", "goruntuleme", "diyaliz"], []),
    ("Kişisel Bakım", "Kuaför & Berber", ["saloon", "salon", "salonu", "erkek kuaforu", "bayan kuaforu", "makas", "tras", "tıraş", "traş"], []),
    ("Hizmet", "Park & Bahçe & Rekreasyon", ["park", "parki", "parkı", "bahce", "bahçe", "garden", "mesire", "piknik", "yesil alan", "yeşil alan", "sahil", "plaj", "plaji", "plajı", "koy", "iskele", "sehir ormani"], []),
    ("Hizmet", "Müze & Tarihi Yer & Turizm", ["muze", "müze", "muzesi", "müzesi", "kumbet", "kümbet", "turbe", "türbe", "kale", "kalesi", "camii", "cami", "kilise", "anit", "anıt", "tarihi", "harabe", "antik", "hamam", "koprusu", "köprüsü", "saat kulesi"], []),
    ("Hizmet", "Konaklama", ["kiralik daire", "kiralık daire", "gunluk kiralik", "günlük kiralık", "apart", "suit", "suites", "villa", "bungalov", "bungalow", "kamp", "camping", "konak", "konaklama"], []),
    ("Hizmet", "Düğün & Organizasyon", ["dugun salonu", "düğün salonu", "dugun", "düğün", "kina", "kına", "organizasyon", "davet", "balo"], []),
    ("Hizmet", "Kamu & Kurum", ["belediye", "muhtarlik", "muhtarlık", "mudurlugu", "müdürlüğü", "mudurluk", "kaymakamlik", "kaymakamlık", "valilik", "adliye", "postane", "ptt", "karakol", "jandarma", "osb", "organize sanayi", "sendika", "dernek", "vakif", "vakıf", "oda", "kooperatif", "birligi", "birliği", "okulu", "ilkokulu", "ortaokulu", "lisesi", "yurdu", "kres", "kreş", "anaokulu"], []),
    ("Hizmet", "Şirket & Ofis (sektörü belirsiz)", ["ofis", "office", "buro", "büro", "danismanlik", "danışmanlık", "holding", "grup", "group", "hizmetleri", "hizmet", "yonetim", "yönetim", "ajans", "ajansi", "ajansı", "reklam", "medya", "yazilim", "yazılım", "bilisim", "bilişim", "teknoloji"], []),
]
NAME_STOP = {"yeri", "evi", "merkezi", "merkez", "usta", "ustanin", "kardesler", "aile", "sube", "subesi", "bayi", "bayii", "dunyasi", "center", "ozel", "urunleri", "urunler", "satis", "teknik", "bakim", "tamir", "cadde", "mahallesi"}

TAX2 = EXTRA + v1.TAX
NORM_TAX2 = [(sec, cat, [v1.norm(k) for k in kws], [v1.norm(b) for b in brands]) for sec, cat, kws, brands in TAX2]


def rule2(name):
    n = v1.norm(name); toks = n.split()
    for sec, cat, kws, brands in NORM_TAX2:
        for b in brands:
            if b and v1._phrase_hit(b, n, toks): return sec, cat, f"brand='{b}'"
    for sec, cat, kws, brands in NORM_TAX2:
        for k in kws:
            if k and v1._phrase_hit(k, n, toks): return sec, cat, f"keyword='{k}'"
    return None


SEARCH_MAP = {"restoranlar": ("Yeme-İçme", "Restoran & Lokanta"), "kafeler": ("Yeme-İçme", "3. Nesil Kahveci"), "pastaneler": ("Yeme-İçme", "Pastane & Fırın & Börekçi"), "kuaför güzellik": ("Kişisel Bakım", None),
              "otomotiv servisleri": ("Otomotiv", "Oto Servis & Yedek Parça"), "marketler": ("Gıda Perakende", "Süpermarket & Market"), "giyim": ("Moda", "Giyim & Ayakkabı"), "eczaneler": ("Sağlık", "Eczane"),
              "elektronikçiler": ("Elektronik", "Elektronik & Telefon"), "mobilyacılar": ("Ev & Yapı", "Mobilya & Ev Dekorasyon"), "nalburlar": ("Ev & Yapı", "Yapı Malzemesi & Nalbur"), "kasaplar": ("Gıda Perakende", "Kasap"),
              "manavlar": ("Gıda Perakende", "Manav"), "oteller": ("Hizmet", "Konaklama"), "bankalar": ("Hizmet", "Finans & Sigorta"), "okullar": ("Hizmet", "Kamu & Kurum"), "dükkanlar": (None, None)}


def search_hint(term):
    t = (term or "").lower()
    for key, (sec, cat) in SEARCH_MAP.items():
        if t.endswith(key): return key, sec, cat
    return None, None, None


def main():
    t0 = time.time()
    c = duckdb.connect(str(OUT / "staging" / "geoprop_staging.duckdb"), read_only=True); c.execute("SET memory_limit='2GB'")
    g = "stg.poi_business__stg_google_places_ve_yogunluk__google_places_gozlem"; y = "stg.poi_business__stg_google_places_ve_yogunluk__google_places_yorumlar_ve_niyet"
    rows = c.execute(f"SELECT raw_google_place_id, arg_max(raw_isim, raw_observed_at), arg_max(raw_ana_kategori, raw_observed_at), arg_max(raw_arama_terimi, raw_observed_at), arg_max(source_file_id, raw_observed_at) FROM {g} GROUP BY 1").fetchall()
    reviews = {pid: txt for pid, txt in c.execute(f"SELECT raw_google_place_id, string_agg(raw_yorum_metni, ' | ') FROM {y} WHERE raw_yorum_metni IS NOT NULL GROUP BY 1").fetchall()}
    print(f"mekân={len(rows):,} yorumlu={len(reviews):,} ({time.time()-t0:.0f}s)")
    labeled = [(pid, v1.norm(n), a) for pid, n, a, s, f in rows if a and a != "Ticari Mekan" and n]
    cnt = collections.Counter(a for _, _, a in labeled); keep = {a for a, k in cnt.items() if k >= 200}
    # --- isim modeli ---
    Xn = [n for _, n, _ in labeled]; yn = [a if a in keep else "Diğer" for _, _, a in labeled]
    Xtr, Xte, ytr, yte = train_test_split(Xn, yn, test_size=0.2, random_state=SEED, stratify=yn)
    vec_n = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3, max_features=300000, sublinear_tf=True)
    clf_n = SGDClassifier(loss="log_loss", alpha=2e-6, max_iter=25, random_state=SEED, n_jobs=4).fit(vec_n.fit_transform(Xtr), ytr)
    Pte = clf_n.predict_proba(vec_n.transform(Xte)); acc_n = accuracy_score(yte, clf_n.classes_[Pte.argmax(1)]); hi = Pte.max(1) >= 0.85
    acc_n_hi = accuracy_score(np.array(yte)[hi], clf_n.classes_[Pte.argmax(1)][hi])
    # --- yorum modeli (etiketli & yorumlu) ---
    lr = [(v1.norm(reviews[pid]), a) for pid, n, a in labeled if pid in reviews]
    Xr = [t for t, _ in lr]; yr = [a if a in keep else "Diğer" for _, a in lr]
    Xtr_r, Xte_r, ytr_r, yte_r = train_test_split(Xr, yr, test_size=0.2, random_state=SEED, stratify=yr)
    vec_r = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, max_features=300000, sublinear_tf=True)
    clf_r = SGDClassifier(loss="log_loss", alpha=5e-6, max_iter=25, random_state=SEED, n_jobs=4).fit(vec_r.fit_transform(Xtr_r), ytr_r)
    Pte_r = clf_r.predict_proba(vec_r.transform(Xte_r)); acc_r = accuracy_score(yte_r, clf_r.classes_[Pte_r.argmax(1)]); hi_r = Pte_r.max(1) >= 0.85
    acc_r_hi = accuracy_score(np.array(yte_r)[hi_r], clf_r.classes_[Pte_r.argmax(1)][hi_r]) if hi_r.any() else None
    print(f"isim modeli acc={acc_n:.3f} (p≥.85: %{100*hi.mean():.0f} kapsama, acc={acc_n_hi:.3f}) | yorum modeli n={len(lr):,} acc={acc_r:.3f} (p≥.85: %{100*hi_r.mean():.0f}, acc={acc_r_hi}) ({time.time()-t0:.0f}s)")
    # --- hedefler ---
    targets = [(pid, n, a, s, f) for pid, n, a, s, f in rows if a == "Ticari Mekan"]
    Pn = clf_n.predict_proba(vec_n.transform([v1.norm(n) for _, n, _, _, _ in targets])); cn = clf_n.classes_
    idx_r = [i for i, t in enumerate(targets) if t[0] in reviews]
    Pr = clf_r.predict_proba(vec_r.transform([v1.norm(reviews[targets[i][0]]) for i in idx_r])) if idx_r else np.zeros((0, 1)); cr = clf_r.classes_
    pr_by_i = {i: Pr[k] for k, i in enumerate(idx_r)}
    out = []; methods = collections.Counter(); bands = collections.Counter()
    for i, (pid, n, a, s, f) in enumerate(targets):
        r = rule2(n); tn = int(Pn[i].argmax()); pn = float(Pn[i][tn]); cat_n = cn[tn]
        pr = pr_by_i.get(i); tr = int(pr.argmax()) if pr is not None else None; prob_r = float(pr[tr]) if pr is not None else None; cat_r = cr[tr] if pr is not None else None
        sec = cat = None; conf = 0.0; method = "UNRESOLVED"; band = "no_auto_merge"; reasons = []
        if r:
            sec, cat, why = r; conf, method, band = 0.92, "RULE", "high"; reasons.append(why)
        elif cat_n != "Diğer" and pn >= 0.85:
            cat, conf, method, band = cat_n, round(pn, 3), "NAME_MODEL", "high"
        elif cat_r and cat_r != "Diğer" and prob_r >= 0.85:
            cat, conf, method, band = cat_r, round(prob_r, 3), "REVIEW_MODEL", "high"
        elif cat_n != "Diğer" and pn >= 0.70:
            cat, conf, method, band = cat_n, round(pn, 3), "NAME_MODEL", "review_recommended"
        elif cat_r and cat_r != "Diğer" and prob_r >= 0.70:
            cat, conf, method, band = cat_r, round(prob_r, 3), "REVIEW_MODEL", "review_recommended"
        else:
            key, hs, hc = search_hint(s)
            if hs:
                sec, cat, conf, method, band = hs, hc, 0.55 if hc else 0.45, "SEARCH_HINT", "review_recommended"; reasons.append(f"arama_eki='{key}'")
        reasons.append(f"name_model={cat_n}({pn:.2f})")
        if cat_r: reasons.append(f"review_model={cat_r}({prob_r:.2f})")
        methods[method] += 1; bands[band] += 1
        out.append({"google_place_id": pid, "source_file_id": f, "raw_name": n, "raw_ana_kategori": a, "raw_arama_terimi": s, "has_reviews": pid in reviews, "predicted_sector": sec, "predicted_category": cat,
                    "confidence": conf, "band": band, "method": method, "reasons": "; ".join(reasons), "classifier_version": V, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    pq.write_table(pa.Table.from_pylist(out), OUT / "mappings" / "poi_category_predictions_v2.parquet")
    (OUT / "mappings" / "poi_taxonomy_v2.json").write_text(json.dumps({"version": V, "rules_in_order": [{"sector": s_, "category": c_, "keywords": k, "brands": b} for s_, c_, k, b in TAX2], "search_map": SEARCH_MAP,
        "name_model": {"holdout_accuracy": acc_n, "p085_coverage": float(hi.mean()), "p085_accuracy": acc_n_hi}, "review_model": {"train": len(lr), "holdout_accuracy": acc_r, "p085_coverage": float(hi_r.mean()), "p085_accuracy": acc_r_hi},
        "seed": SEED, "code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, ensure_ascii=False, indent=1))
    cats = collections.Counter(o["predicted_category"] for o in out if o["predicted_category"])
    gold = random.sample(out, 200)
    keys = list(gold[0].keys()) + ["correct(human)", "correct_category(human)", "verified_by", "verified_at"]
    with open(OUT / "validation" / "golden" / "poi_category_golden_v2.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); [w.writerow({**g_, "correct(human)": "", "correct_category(human)": "", "verified_by": "", "verified_at": ""}) for g_ in gold]
    allcats = sorted({s_c[1] for s_c in [(x[0], x[1]) for x in TAX2]} | {"Diğer"})
    opts = "".join(f"<option>{H.escape(x)}</option>" for x in allcats)
    items = "".join(f'''<div class="p" data-id="{H.escape(g_['google_place_id'])}"><div><b>{H.escape(g_['raw_name'])}</b> <span class="s">— arama: {H.escape((g_['raw_arama_terimi'] or '')[-45:])}</span></div>
<div class="t">Tahmin: <b>{H.escape(g_['predicted_category'] or (g_['predicted_sector'] or '—') + ' (yalnız sektör)')}</b> <span class="m {g_['method']}">{g_['method']}</span> güven {g_['confidence']} · {H.escape(g_['reasons'][:110])}</div>
{('<div class="r">Yorum: ' + H.escape(reviews.get(g_['google_place_id'], '')[:220]) + '</div>') if g_['has_reviews'] else ''}
<div class="v"><label><input type="radio" name="r{i}" value="dogru"> Doğru</label> <label><input type="radio" name="r{i}" value="yanlis"> Yanlış</label> <label><input type="radio" name="r{i}" value="emin_degil"> Emin değilim</label> doğrusu: <select><option></option>{opts}</select></div></div>''' for i, g_ in enumerate(gold))
    page = f'''<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><title>POI kategori golden set v2</title><style>body{{font-family:-apple-system,system-ui;margin:16px;background:#fafafa}} .p{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px;margin:8px 0}} .s{{color:#777;font-size:12px}} .t,.r{{font-size:13px;color:#333;margin-top:4px}} .r{{color:#555;font-style:italic}} .m{{padding:1px 6px;border-radius:4px;color:#fff;font-size:11px}} .RULE{{background:#2f855a}} .NAME_MODEL{{background:#3182ce}} .REVIEW_MODEL{{background:#805ad5}} .SEARCH_HINT{{background:#dd6b20}} .UNRESOLVED{{background:#c53030}} .v{{margin-top:6px;font-size:13px}} #bar{{position:sticky;top:0;background:#fff;padding:8px;border-bottom:1px solid #ccc}} textarea{{width:100%;height:110px}}</style></head><body>
<div id="bar"><b>POI kategori doğrulaması v2 — 200 mekân.</b> Tahmin doğru mu? Yanlışsa doğrusunu seç. <button onclick="ex()">Sonuçları CSV göster</button> <span id="c"></span></div>{items}<h3>Sonuç CSV</h3><textarea id="o"></textarea>
<script>function ex(){{let o='google_place_id,verdict,correct_category,verified_at\\n';let n=0;document.querySelectorAll('.p').forEach(p=>{{const v=p.querySelector('input:checked');if(v){{n++;o+=`${{p.dataset.id}},${{v.value}},"${{p.querySelector('select').value}}",${{new Date().toISOString()}}\\n`;}}}});document.getElementById('o').value=o;document.getElementById('c').textContent=n+' / 200';}}
document.addEventListener('change',()=>{{document.getElementById('c').textContent=document.querySelectorAll('.p input:checked').length+' / 200';}});</script></body></html>'''
    (OUT / "validation" / "golden" / "poi_category_golden_review_v2.html").write_text(page, encoding="utf-8")
    rep = f"""# POI kategori çıkarımı v2 — RAPOR ({dt.datetime.now().date()})
"Ticari Mekan": {len(targets):,} / {len(rows):,}. Taksonomi: {len(TAX2)} kategori (v1.3 + {len(EXTRA)} veri-güdümlü ek; ticaret dışı: park, müze/tarihi yer, kamu, çay ocağı, AVM, fabrika, toptan…).
İsim modeli: doğruluk {acc_n:.3f}; p≥0,85'te kapsama %{100*hi.mean():.0f}, doğruluk {acc_n_hi:.3f}. **Yorum modeli** (eğitim {len(lr):,} yorumlu etiketli mekân): doğruluk {acc_r:.3f}; p≥0,85'te kapsama %{100*hi_r.mean():.0f}, doğruluk {acc_r_hi}.

| Yöntem | Mekân |
|---|---|
""" + "\n".join(f"| {m} | {n_:,} |" for m, n_ in methods.most_common()) + """

| Band | Mekân |
|---|---|
""" + "\n".join(f"| {b} | {n_:,} |" for b, n_ in bands.most_common()) + """

## En sık türetilen kategoriler
| Kategori | Mekân |
|---|---|
""" + "\n".join(f"| {k} | {v_:,} |" for k, v_ in cats.most_common(30)) + f"""

v1.3 ile karşılaştırma: çözülemeyen 70.630 → {methods['UNRESOLVED']:,}; yalnız sektör ipucu 92.473 → {methods['SEARCH_HINT']:,}.
Çıktı: mappings/poi_category_predictions_v2.parquet · golden: validation/golden/poi_category_golden_review_v2.html (200). Süre {time.time()-t0:.0f}s.
"""
    (OUT / "validation" / "poi_category_v2_report.md").write_text(rep, encoding="utf-8"); print(rep)


if __name__ == "__main__":
    main()
