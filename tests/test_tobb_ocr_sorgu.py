#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import zlib
from pathlib import Path

from collector.tobb_ocr_sorgu import (
    build_query_spec,
    classify_text,
    fold_tr,
    search_ocr_database,
)
from collector.tobb_sayfa_indeksli_sirket_veritabani import ensure_page_linked_schema


def test_fold_tr_handles_turkish_and_combining_dot():
    assert fold_tr("Boha Demir Çelik Sanayi İç İçe") == "BOHA DEMIR CELIK SANAYI IC ICE"
    assert fold_tr("İ".lower() + "stanbul şeker ğü") == "ISTANBUL SEKER GU"


def test_classify_text_proximity_and_dotted_acronym():
    spec = build_query_spec("acr demir çelik")
    assert classify_text(fold_tr("A.C.R. Demir Çelik Sanayi ve Ticaret A.Ş."), spec) == "exact_all_tokens"
    assert classify_text(fold_tr("ACR Demir Ticaret"), spec) == "anchor_with_other_token"
    assert classify_text(fold_tr("ACR Metal Sanayi"), spec) == "anchor_with_sector"
    assert classify_text(fold_tr("ACR Tekstil Ltd."), spec) == "anchor_only"
    assert classify_text(fold_tr("ACRA Elektronik A.Ş."), spec) is None

    # Sayfa metni > 2500 karakter olduğunda farklı şirket ilanları arası yanlış pozitif olmamalı
    long_page = ("X" * 3000) + " ACR TEKSTIL " + ("Y" * 1000) + " ANADOLU DEMIR CELIK "
    assert classify_text(long_page, spec) == "anchor_only"


def test_search_ocr_database_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_ocr.sqlite"
        conn = sqlite3.connect(str(db_path))
        ensure_page_linked_schema(conn)
        conn.executescript("""
            CREATE TABLE sirketler (
                mersis_no TEXT, ticaret_sicil_no TEXT, tsm_adi TEXT, vkn TEXT, unvan TEXT,
                sirket_turu TEXT, durum TEXT, sermaye_tl REAL, kurulus_tarihi TEXT,
                il TEXT, ilce TEXT, tam_adres TEXT, faaliyet_konusu TEXT, nace_kodu TEXT,
                telefon TEXT, web_sitesi TEXT, kaynak TEXT, guncellenme_tarihi TEXT
            );
            CREATE TABLE ticaret_sicil_gazetesi_ilanlari (
                ticaret_sicil_no TEXT, tsm_adi TEXT, unvan TEXT, ilan_turu TEXT,
                gazete_sayisi TEXT, sayfa_no TEXT, ilan_tarihi TEXT, ilan_metni TEXT
            );
        """)
        conn.execute(
            "INSERT INTO sirketler (mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan, il, tam_adres, kaynak) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("0000000000000001", "TEST-1", "İSTANBUL", "0000000001", "ORNEK DEMİR ÇELİK TEST ANONİM ŞİRKETİ", "İSTANBUL", "TEST OSB", "TOBB_PDF_OCR"),
        )
        conn.execute(
            "INSERT INTO sirketler (mersis_no, ticaret_sicil_no, tsm_adi, vkn, unvan, il, tam_adres, kaynak) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("0000000000000002", "TEST-2", "İSTANBUL", "0000000002", "ORNEK DEMİR ÇELİK PREOCR", "İSTANBUL", "PREOCR", "ITO"),
        )
        raw_page_1 = "İSTANBUL TİCARET SİCİLİ MÜDÜRLÜĞÜ\nUnvan: Ornek Demir Çelik Test A.Ş.\nMüdür: Test Kisi Bir"
        raw_page_2 = "A.B.C. DEMİR ÇELİK TEST LTD. kuruluş ilanı.\nYönetim Kurulu Başkanı: Test Kisi Iki"
        conn.execute(
            "INSERT INTO gazete_sayfa_arsivi (gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi, archive_url, pdf_member, sayfa_metni_zlib, ocr_durumu) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("11001", "5", "2024-05-01", "İSTANBUL", "https://example.invalid/a.tar.gz", "g1.pdf", zlib.compress(raw_page_1.encode("utf-8")), 1),
        )
        conn.execute(
            "INSERT INTO gazete_sayfa_arsivi (gazete_sayisi, sayfa_no, yayin_tarihi, tsm_adi, archive_url, pdf_member, sayfa_metni_zlib, ocr_durumu) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("11002", "9", "2024-06-10", "KOCAELİ", "https://example.invalid/b.tar.gz", "g2.pdf", zlib.compress(raw_page_2.encode("utf-8")), 1),
        )
        conn.execute(
            """INSERT INTO gazete_kisi_ve_isim_indeksi
            (kisi_adi_soyadi, tckn_veya_kimlik, gorevi_veya_rolu, bagli_unvan, gazete_sayisi, sayfa_no, archive_url, pdf_member)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("TEST KISI BIR", "000*****00", "ŞİRKET MÜDÜRÜ", "ORNEK DEMİR ÇELİK TEST A.Ş.", "11001", "5", "https://example.invalid/a.tar.gz", "g1.pdf"),
        )
        conn.commit()
        conn.close()

        out = search_ocr_database(
            db_path=db_path,
            raw_queries=["ornek demir çelik", "abc demir çelik"],
            workers=2,
            ocr_only=True,
        )

        assert out["taranan_sayfa_tam_metni"] == 2
        q1 = out["sorgular"]["ornek demir çelik"]
        q2 = out["sorgular"]["abc demir çelik"]
        assert len(q1["sirketler"]) == 1
        assert len(q1["gazete_sayfa_arsivi_eslesmeleri"]) == 1
        assert q1["gazete_sayfa_arsivi_eslesmeleri"][0]["_eslesme_seviyesi"] == "exact_all_tokens"
        assert len(q1["gazete_kisi_ve_isim_indeksi"]) == 1
        assert len(q2["gazete_sayfa_arsivi_eslesmeleri"]) == 1
        assert q2["gazete_sayfa_arsivi_eslesmeleri"][0]["gazete_sayisi"] == "11002"
