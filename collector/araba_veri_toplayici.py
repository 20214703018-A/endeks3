#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEOPROP AI - Eksiksiz Araç Verisi Toplayıcısı (Collector Modül Girişi)
=====================================================================
Bu dosya kök dizindeki araba_veri_toplayici.py betiğini çağırır veya doğrudan çalıştırır.
"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from araba_veri_toplayici import main

if __name__ == "__main__":
    main()
