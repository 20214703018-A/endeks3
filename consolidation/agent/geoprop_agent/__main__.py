"""Komut satırı.

  python3 -m geoprop_agent plan '{"analiz": "sirala", "olcu": "kira getirisi", "seviye": "ilce", "kapsam": "İzmir"}'
  python3 -m geoprop_agent plan dosya.json [--json] [--sorgular]
  python3 -m geoprop_agent yer "Bostanlı, Karşıyaka"
  python3 -m geoprop_agent olcu "kira getirisi"
  python3 -m geoprop_agent katalog            # ölçü ve analiz kataloğu
  python3 -m geoprop_agent sema               # plan JSON şeması
  python3 -m geoprop_agent mcp                # MCP sunucusu (stdio)
  python3 -m geoprop_agent sohbet             # Claude API sohbeti (anthropic paketi + API anahtarı gerekir)
"""
import json
import sys
from pathlib import Path


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    komut, arg = a[0], [x for x in a[1:] if not x.startswith("--")]
    bayrak = {x for x in a[1:] if x.startswith("--")}
    if komut == "plan":
        from .motor import calistir
        kaynak = arg[0]
        plan = json.loads(Path(kaynak).read_text(encoding="utf-8") if Path(kaynak).exists() else kaynak)
        s = calistir(plan)
        print(json.dumps(s.sozluk(sorgular="--sorgular" in bayrak), ensure_ascii=False, indent=1, default=str)
              if "--json" in bayrak else s.markdown())
    elif komut == "yer":
        from .yer import coz
        print(json.dumps(coz(" ".join(arg)).sozluk(), ensure_ascii=False, indent=1))
    elif komut == "olcu":
        from .araclar import ontoloji_ara
        print(json.dumps(ontoloji_ara(" ".join(arg)), ensure_ascii=False, indent=1))
    elif komut == "katalog":
        from .araclar import ontoloji_ara
        print(json.dumps(ontoloji_ara(""), ensure_ascii=False, indent=1))
    elif komut == "sema":
        from .plan import json_sema
        print(json.dumps(json_sema(), ensure_ascii=False, indent=1))
    elif komut == "mcp":
        from .mcp_sunucu import main as m
        m()
    elif komut == "sohbet":
        from .sohbet import main as m
        m()
    else:
        print(f"Bilinmeyen komut: {komut}\n{__doc__}")
        sys.exit(2)


if __name__ == "__main__":
    main()
