#!/usr/bin/env python3
"""Resmî / yayımcı GTFS (toplu taşıma tarife standardı) beslemelerini indirir.

GTFS: bir işletmenin hatları (routes), durakları (stops), seferleri (trips), her seferin her durağa
varış/kalkış saati (stop_times) ve çalışma günleri (calendar) için uluslararası dosya standardı.
Zip dosyaları olduğu gibi saklanır (yeniden sıkıştırılmaz); manifest'e kaynak/sha256 yazılır.

Not: İBB (İETT + raylı/deniz), Konya ve Gaziantep beslemeleri 2026-09-24 CKAN turunda indirildi
(acikveri_ckan_*/2026-09-24) ve kaynakta o tarihten beri değişmedi; burada yeniden indirilmez.
Liste: Mobility Database kataloğu (files.mobilitydatabase.org/feeds_v2.csv, country_code=TR) +
belediye CKAN portalları; 2026-09-30'da erişim denendi.
"""
from __future__ import annotations

from intake_common import Intake

FEEDS = {
    # anahtar: (il, işletme, url)
    "izmir_eshot_otobus": ("İzmir", "ESHOT", "https://www.eshot.gov.tr/gtfs/bus-eshot-gtfs.zip"),
    "izmir_metro": ("İzmir", "İzmir Metro A.Ş.", "https://www.izmirmetro.com.tr/gtfs/rail-metro-gtfs.zip"),
    "izmir_tramvay": ("İzmir", "İzmir Metro A.Ş. (Tramvay)", "https://www.tramizmir.com/gtfs/rail-tramizmir-gtfs.zip"),
    "izmir_izban": ("İzmir", "İZBAN", "https://www.izban.com.tr/gtfs/rail-izban-gtfs.zip"),
    "izmir_izdeniz": ("İzmir", "İzdeniz", "https://www.izdeniz.com.tr/gtfs/ship-izdeniz-gtfs.zip"),
    "flixbus_turkiye": ("Türkiye", "FlixBus Türkiye (şehirlerarası)", "https://gtfs.gis.flix.tech/gtfs_generic_turkey.zip"),
}


def main():
    it = Intake("toplu_tasima_gtfs", rate=1.0)
    for key, (il, operator, url) in FEEDS.items():
        try:
            r = it.get(url, timeout=180)
        except Exception as e:
            it.log(f"{key}: indirilemedi {e!r}")
            continue
        ok = r.status_code == 200 and r.content[:2] == b"PK"
        if not ok:
            it.log(f"{key}: geçersiz yanıt (HTTP {r.status_code}, {r.headers.get('content-type')}, "
                   f"{len(r.content)} bayt) — kaydedilmedi")
            continue
        it.save_bytes(f"{key}.zip", r.content, source_url=url, compress=False,
                      extra={"feed_key": key, "il": il, "operator": operator,
                             "last_modified": r.headers.get("last-modified")})
        it.log(f"{key}: {len(r.content):,} bayt")


if __name__ == "__main__":
    main()
