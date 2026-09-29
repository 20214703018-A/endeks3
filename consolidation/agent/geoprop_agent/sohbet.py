"""Claude API ile doğal dil sohbeti (kendi arayüzümüz). Gerekli: `pip install anthropic` ve kimlik bilgisi
(ANTHROPIC_API_KEY ya da `ant auth login`).

Döngü: kullanıcı sorusu → Claude araç çağırır (araclar.py, MCP ile aynı) → motor sayıları üretir → Claude cevabı yazar
→ SAYI DENETİMİ (denetim.py): cevaptaki her sayı araç çıktısında olmalı; değilse Claude'dan bir kez düzeltme istenir,
hâlâ tutmayan sayı varsa cevap uyarıyla işaretlenir.
"""
from __future__ import annotations

import json
import os
import sys

from .araclar import arac_tanimlari, calistir_arac
from .denetim import denetle
from .yonerge import YONERGE

MODEL = os.environ.get("GEOPROP_MODEL", "claude-opus-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"   # reddedilen istek sunucu tarafında önerilen modele yönlenir


def _web_metinleri(icerik) -> list[str]:
    """Sunucu tarafı web araçlarının sonuçları ve metin bloklarındaki alıntılar (cited_text)."""
    out = []
    for b in icerik:
        tur = getattr(b, "type", "")
        if tur.endswith("_tool_result") and tur != "tool_result":
            try:
                out.append(json.dumps(b.model_dump(), ensure_ascii=False, default=str))
            except Exception:
                pass
        for c in getattr(b, "citations", None) or []:
            t = getattr(c, "cited_text", None)
            if t:
                out.append(t)
    return out


class Sohbet:
    def __init__(self, model: str = MODEL, gunluk=None, web: bool = True):
        try:
            import anthropic
        except ImportError as e:
            raise SystemExit("Claude API sohbeti için 'anthropic' paketi gerekli: pip install anthropic") from e
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        # kendi araçlarımız + sunucu tarafı web araması/okuma (Anthropic sunucularında çalışır; dış varsayım toplamak için)
        self.araclar = arac_tanimlari() + ([{"type": "web_search_20260209", "name": "web_search"},
                                            {"type": "web_fetch_20260209", "name": "web_fetch"}] if web else [])
        self.mesajlar: list[dict] = []
        self.gunluk = gunluk or (lambda *a: None)
        # Yönerge + araçlar sabit → istem önbelleği (her turda yeniden faturalanmaz)
        self.sistem = [{"type": "text", "text": YONERGE, "cache_control": {"type": "ephemeral"}}]

    def _istek(self):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=self.sistem,
            tools=self.araclar,
            messages=self.mesajlar,
            thinking={"type": "adaptive"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def _tur(self) -> tuple[str, list[str]]:
        """Araç döngüsünü tamamlar; (son metin, bu turdaki araç çıktıları)."""
        ciktilar: list[str] = []
        for _ in range(25):   # güvenlik sınırı: bir soruda en fazla 25 model çağrısı
            yanit = self._istek()
            if yanit.stop_reason == "refusal":
                self.mesajlar.append({"role": "assistant", "content": yanit.content})
                return "İstek model tarafından reddedildi; soruyu farklı ifade edin.", ciktilar
            self.mesajlar.append({"role": "assistant", "content": yanit.content})
            ciktilar.extend(_web_metinleri(yanit.content))   # web alıntıları da sayı denetiminin kaynağıdır
            if yanit.stop_reason == "pause_turn":
                continue
            cagrilar = [b for b in yanit.content if b.type == "tool_use"]
            if yanit.stop_reason == "tool_use" and cagrilar:
                sonuclar = []
                for c in cagrilar:   # paralel çağrıların tüm sonuçları TEK kullanıcı mesajında döner
                    self.gunluk(f"→ {c.name} {json.dumps(c.input, ensure_ascii=False)}")
                    metin, hata = calistir_arac(c.name, c.input if isinstance(c.input, dict) else {})
                    ciktilar.append(metin)
                    sonuclar.append({"type": "tool_result", "tool_use_id": c.id, "content": metin, "is_error": hata})
                self.mesajlar.append({"role": "user", "content": sonuclar})
                continue
            metin = "".join(b.text for b in yanit.content if b.type == "text")
            if yanit.stop_reason == "max_tokens":
                metin += "\n\n⚠️ Cevap uzunluk sınırında kesildi."
            return metin, ciktilar
        return "Araç çağrı sınırına ulaşıldı; soruyu daraltın.", ciktilar

    def sor(self, soru: str) -> dict:
        self.mesajlar.append({"role": "user", "content": soru})
        metin, ciktilar = self._tur()
        supheli = denetle(metin, ciktilar, soru) if ciktilar else []
        duzeltildi = False
        if supheli:
            self.gunluk(f"sayı denetimi: kaynakta olmayan {supheli} → düzeltme isteniyor")
            self.mesajlar.append({"role": "user", "content":
                                  "[Otomatik sayı denetimi] Cevabındaki şu sayılar araç sonuçlarında yok: " + ", ".join(supheli) +
                                  ". Yalnız araç sonuçlarındaki sayıları kullanarak cevabı yeniden yaz; gerekiyorsa aracı yeniden çağır."})
            metin2, cikti2 = self._tur()
            ciktilar += cikti2
            supheli = denetle(metin2, ciktilar, soru)
            metin, duzeltildi = metin2, True
        if supheli:
            metin += "\n\n⚠️ Sayı denetimi: şu değerler araç sonuçlarında doğrulanamadı: " + ", ".join(supheli)
        return {"cevap": metin, "arac_cagrisi": len(ciktilar), "denetim_temiz": not supheli, "duzeltildi": duzeltildi}


def main():
    s = Sohbet(gunluk=lambda m: print(f"  \033[2m{m}\033[0m", file=sys.stderr))
    print(f"GEOPROP sohbet ({s.model}). Çıkmak için boş satır.\n")
    while True:
        try:
            soru = input("soru> ").strip()
        except EOFError:
            break
        if not soru:
            break
        r = s.sor(soru)
        print("\n" + r["cevap"] + "\n")


if __name__ == "__main__":
    main()
