"""Sağlık Bakanlığı KHGM "2. ve 3. Basamak Kamu Sağlık Tesisleri Güncel Listesi" → ham + parquet.

Kaynak: khgmsaglikhizmetleridb.saglik.gov.tr/TR-87343 (resmî). Sayfa 2026-09'da 404; dosyanın yayımlanmış
sürümleri Internet Archive'dan birebir (`id_` = değiştirilmemiş ham bayt) alınır. Her sürüm ayrı dosya
(liste tarihi dosya adında). Sayfalar: GÜNCEL TABLO (hastaneler: kurum kodu, il, ilçe, ad, EAH, tür, ünite,
rol, tescilli yatak, üniversite protokolü, DETSİS), ADSM (ağız-diş merkezleri) ve varsa diğerleri.

Çıktı: GEOPROP_RAW_INTAKE/khgm_saglik_tesisleri/<tarih>/raw/*.xls + tesisler.parquet (tüm sürümler, sayfa ve
liste_tarihi sütunlu, uzun biçim — yorum/dönüşüm yok, yalnız başlık satırı sütun adına çevrilir).
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from intake_common import Intake, now_iso  # noqa: E402

CDX = ("http://web.archive.org/cdx/search/cdx?url=khgmsaglikhizmetleridb.saglik.gov.tr/Eklenti/*"
       "&output=json&filter=original:.*basamak.*&filter=statuscode:200&collapse=digest")
XLS_MAGIC = (b"\xd0\xcf\x11\xe0", b"PK\x03\x04")  # OLE2 .xls / OOXML .xlsx


def main():
    it = Intake("khgm_saglik_tesisleri", rate=12.0)  # archive.org 429 veriyor
    caps = {}
    for r in it.get(CDX, timeout=120).json()[1:]:  # aynı dosyanın tüm arşiv kayıtları, en yenisi önce
        caps.setdefault(r[2].replace("http://", "https://"), []).append(r[1])
    frames = []
    for orig, stamps in caps.items():
        for ts in sorted(stamps, reverse=True):
            url = f"http://web.archive.org/web/{ts}id_/{orig}"
            try:
                content = it.get(url, timeout=180, tries=6).content
            except RuntimeError as e:
                it.log(f"{ts}: indirilemedi ({e}), sonraki kayıt denenir")
                continue
            if content[:4] in XLS_MAGIC:
                break
            it.log(f"{ts} {orig[-60:]}: Excel değil ({content[:40]!r}), sonraki kayıt denenir")
        else:
            it.log(f"{orig}: geçerli Excel kaydı yok, atlandı")
            continue
        m = re.search(r"listesi-(\d{2})(\d{2})(\d{4})xls", orig)
        liste_tarihi = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
        it.save_bytes(f"raw/kamu_saglik_tesisleri_{liste_tarihi}.xls", content, source_url=orig,
                      method="internet_archive_id_", compress=False,
                      extra={"archive_url": url, "archive_timestamp": ts, "liste_tarihi": liste_tarihi})
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=str)
        for name, df in sheets.items():
            hdr_i = next((i for i in range(min(10, len(df)))
                          if df.iloc[i].astype(str).str.contains("KURUM", case=False).any()), None)
            if hdr_i is None:
                it.log(f"{liste_tarihi} {name}: başlık satırı yok, atlandı ({df.shape})")
                continue
            cols = [str(c).strip() if str(c) != "nan" else f"bos_{j}" for j, c in enumerate(df.iloc[hdr_i])]
            body = df.iloc[hdr_i + 1:].copy()
            body.columns = cols
            body = body.dropna(how="all")
            body = body[[c for c in cols if not (c.startswith("bos_") and body[c].isna().all())]]
            long = body.reset_index(drop=True).melt(ignore_index=False, var_name="sutun", value_name="deger")
            long["satir_no"] = long.index + hdr_i + 2  # Excel satır numarası (1 tabanlı)
            long["sayfa"] = name.strip()
            long["liste_tarihi"] = liste_tarihi
            long["kaynak_url"] = orig
            frames.append(long.dropna(subset=["deger"]))
            it.log(f"{liste_tarihi} {name.strip()}: {len(body)} satır, {len(cols)} sütun")
    out = pd.concat(frames, ignore_index=True)
    out["guncellenme_tarihi"] = now_iso()
    it.save_parquet("tesisler_uzun", out, source_url=CDX, method="parse_xls_long",
                    note="her hücre bir satır (sayfa, satir_no, sutun, deger); değer dönüşümü yok")


if __name__ == "__main__":
    main()
