"""
Zaman serisi toplayıcıları — ortak araçlar (11 Ekim 2026).

Her kaynak kendi sqlite dosyasına yazar; her satırda çekim zamanı (UTC, ISO 8601) bulunur ve tekrarlı çekimler SİLİNMEZ:
anlık görüntü tabloları (cekim_gunu) zaman serisi oluşturur. Dosyalar release `zaman-serisi`'nde `<kaynak>.sqlite.gz` olarak
birikir (iş akışı önce indirir, ekler, geri yükler).
Kibar toplama: istekler sıralı, kaynak başına ≥ aralık sn, 429/5xx'te üstel bekleme, istek başına kesin süre sınırı.
Bot doğrulaması (Cloudflare/Turnstile/captcha) olan sayfalar ATLATILMAZ: böyle bir yanıtta toplayıcı durur ve sebebi yazar.
"""
import json, random, re, signal, sqlite3, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
BOT_RX = re.compile(r"Just a moment|cf-chl|challenges\.cloudflare\.com/turnstile|captcha", re.I)


def simdi(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def bugun(): return datetime.now(timezone.utc).date().isoformat()


class BotEngeli(Exception):
    pass


class ErisimYok(BotEngeli):
    """Sunucu bu makinenin IP'sine yanıt vermiyor (bağlantı kurulamıyor / art arda zaman aşımı). Kaynak durur, not düşülür.
    16 Eki 2026: İş Yatırım, Azure Polonya IP'sinden gelen bağlantıları sessizce düşürdü → toplayıcı saatlerce takıldı."""
    pass


class Istemci:
    def __init__(self, aralik=1.0, zaman_asimi=60):
        self.aralik, self.son, self.zaman_asimi, self.sayac = aralik, 0.0, zaman_asimi, 0
        self.ardisik_zaman = 0   # art arda bağlantı/zaman aşımı hatası (başarılı yanıtta sıfırlanır)

    def get(self, url, ham=False, basliklar=None, deneme=5):
        bekle = 5.0
        for _ in range(deneme):
            gec = time.time() - self.son
            if gec < self.aralik: time.sleep(self.aralik - gec + random.random() * 0.2)
            self.son = time.time(); self.sayac += 1
            def _dur(*_): raise TimeoutError("istek süresi aşıldı")
            signal.signal(signal.SIGALRM, _dur); signal.alarm(self.zaman_asimi * 2)
            try:
                h = {"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"}; h.update(basliklar or {})
                r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=self.zaman_asimi)
                b = r.read(); signal.alarm(0); self.ardisik_zaman = 0
                if ham: return r.status, b
                t = b.decode(r.headers.get_content_charset() or "utf-8", "replace")
                if r.status == 200 and len(t) < 20000 and BOT_RX.search(t): raise BotEngeli(url)
                return r.status, t
            except urllib.error.HTTPError as e:
                signal.alarm(0)
                if e.code == 403:
                    t = e.read().decode("utf-8", "replace")
                    if BOT_RX.search(t): raise BotEngeli(url)
                    return 403, t
                if e.code in (429, 500, 502, 503, 504): time.sleep(bekle); bekle *= 2; continue
                try: govde = e.read()        # bazı siteler (ör. Secretcv filtre sayfaları) 410 ile tam içerik döndürür
                except Exception: govde = b""
                return e.code, (govde if ham else govde.decode("utf-8", "replace"))
            except (BotEngeli,):
                raise
            except Exception as e:
                signal.alarm(0)
                if isinstance(e, (TimeoutError, OSError)) and not isinstance(e, urllib.error.HTTPError):
                    self.ardisik_zaman += 1
                    if self.ardisik_zaman >= 3:
                        raise ErisimYok(f"{urllib.parse.urlparse(url).netloc}: art arda {self.ardisik_zaman} bağlantı/zaman aşımı — bu IP'den erişim yok")
                time.sleep(bekle); bekle *= 2
        return None, None

    def json(self, url, **k):
        st, t = self.get(url, **k)
        if st != 200 or not t: return None
        try: return json.loads(t)
        except ValueError: return None


def db(yol, ddl):
    c = sqlite3.connect(yol)
    c.executescript(ddl)
    c.execute("CREATE TABLE IF NOT EXISTS _kosu (kaynak TEXT, baslangic TEXT, bitis TEXT, satir INTEGER, istek INTEGER, durum TEXT, not_ TEXT)")
    return c


def kosu_yaz(c, kaynak, bas, satir, istek, durum, not_=None):
    c.execute("INSERT INTO _kosu VALUES (?,?,?,?,?,?,?)", (kaynak, bas, simdi(), satir, istek, durum, not_)); c.commit()


def next_payload(html):
    """Next.js (app router) sayfasındaki self.__next_f.push(...) parçalarını tek metne çevirir."""
    return "".join(json.loads('"' + m + '"') for m in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S))


def nesneler(metin, anahtar):
    """Metindeki '"anahtar":{' ile başlayan JSON nesnelerini çözer (gömülü veri)."""
    dec = json.JSONDecoder(); out = []
    for m in re.finditer(re.escape(f'"{anahtar}":') + r"\{", metin):
        try:
            o, _ = dec.raw_decode(metin, m.end() - 1); out.append(o)
        except ValueError:
            pass
    return out
