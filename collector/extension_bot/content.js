// Google arama sonucu sayfasında çalışır: hedef mekanı doğrular, "Menü" panelini açar,
// fiyat ve görselleri toplayıp arka plana (background.js) bildirir.
// Her yol MUTLAKA save_result veya fail_current ile biter; aksi halde kuyruk o mekanda takılı kalır.
(async function () {
    if (!location.pathname.startsWith("/search")) return;
    if (window.__geopropRan) return; // Aynı belgede iki kez çalışma (çift kayıt/çift arama önlemi)
    window.__geopropRan = true;
    const parsers = window.GeopropMenuParsers;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const log = (...args) => console.log("[GEOPROP]", ...args);
    const bodyText = () => document.body?.innerText || "";
    const snippet = () => parsers.cleanLine(bodyText()).slice(0, 160);

    // Google arama adresini yeniden yazıp (sca_esv, ved...) bizim URL işaretimizi silebiliyor;
    // bu yüzden "bot sekmesi miyim" sorusunu URL'ye değil sekme kimliğine göre arka plan yanıtlar.
    let identity;
    try {
        identity = await chrome.runtime.sendMessage({ action: "whoami" });
    } catch (_) {
        return; // eklenti yeniden yüklendi, bu sayfa eski; arka plan yeni sayfa açacak
    }
    if (!identity || !identity.isBotTab || !identity.venue || !identity.venue.lease_token) return;
    const venue = identity.venue;
    const fail = (error, blocked = false) => {
        log("Başarısız:", error);
        return chrome.runtime.sendMessage({
            action: "fail_current",
            payload: { id: venue.id, lease_token: venue.lease_token, error, blocked },
        });
    };
    const save = (payload) => {
        log(`Kaydediliyor: ${payload.fiyatlar.length} fiyat, ${payload.gorseller.length} görsel (${payload.fiyat_saglayici})`);
        return chrome.runtime.sendMessage({ action: "save_result", payload });
    };

    // Menü paneli adayları. Google'ın "Menü" diyaloğu ilk satırı "Menü" olan bir [role=dialog]'dur;
    // bazı mekanlarda yalnız fotoğraf içerir (fiyat yok) — o da menüdür, görselleri alınır.
    // Diğer adaylar (bölge/attrid) yalnız gerçek fiyat içeriyorsa dikkate alınır.
    const isMenuDialog = (element) => element.getAttribute("role") === "dialog"
        && /^menü\s*$/i.test((element.innerText || "").split("\n")[0] || "");
    const menuCandidates = () => Array.from(document.querySelectorAll(
        "[role='dialog'], [role='region'][aria-label*='Menü'], [role='region'][aria-label*='menü'], [data-attrid*='menu']"
    )).map((element) => ({
        element,
        prices: parsers.countPriceMentions(element.innerText || ""),
        images: element.querySelectorAll("img").length,
        dialog: isMenuDialog(element),
    }))
        .filter((entry) => entry.prices > 0 || (entry.dialog && entry.images > 0))
        .sort((a, b) => (b.dialog - a.dialog) || (b.prices - a.prices)
            || (b.element.innerText || "").length - (a.element.innerText || "").length);

    // "Popüler saatler" grafiği: bar yüksekliği (px) = yüzde × 0,75 (100% = 75px). Yalnız seçili
    // günün barları DOM'da olduğundan her gün sekmesine gerçek tıklama ile geçilir.
    const DAY_NAMES = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
    const busyRoot = () => document.querySelector("[data-attrid='kc:/local:busyness']");
    const busyRadios = () => Array.from(busyRoot()?.querySelectorAll("[role='radio']") || []);
    const selectedDay = () => busyRadios().find((el) => el.getAttribute("aria-checked") === "true"
        && DAY_NAMES.includes(el.getAttribute("aria-label")))?.getAttribute("aria-label") || null;
    const readDayBars = () => Array.from(busyRoot().querySelectorAll("[role='radio']"))
        .filter((el) => /^\d\d:00$/.test(el.getAttribute("aria-label") || ""))
        .map((el) => {
            const bar = el.querySelector("[style*='height']") || el.closest("[style*='height']");
            const px = parseFloat((bar?.getAttribute("style") || "").match(/height:\s*([\d.]+)px/)?.[1] || "0");
            return { saat: el.getAttribute("aria-label"), yuzde: Math.max(0, Math.min(100, Math.round(px / 0.75))) };
        });
    async function collectPopularTimes() {
        const root = busyRoot();
        if (!root) return null;
        root.scrollIntoView({ block: "center" });
        await sleep(300);
        const text = root.innerText || "";
        const result = {
            canli: (text.match(/Canlı:\s*([^\n]+)/) || [])[1] || null,
            bekleme: (text.match(/(Genellikle[^\n]*bekle[^\n]*|Maksimum[^\n]*bekleme[^\n]*)/) || [])[1] || null,
            kalis_suresi: (text.match(/genellikle\s+([^\n]+?)\s+kalıyor/) || [])[1] || null,
            gunler: {},
        };
        const initial = selectedDay();
        if (initial) result.gunler[initial] = readDayBars();
        for (const day of DAY_NAMES) {
            if (result.gunler[day]) continue;
            const radio = busyRadios().find((el) => el.getAttribute("aria-label") === day);
            if (!radio) continue;
            const rect = radio.getBoundingClientRect();
            if (!rect.width) continue;
            try {
                await chrome.runtime.sendMessage({
                    action: "trusted_click",
                    points: [{ x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) }],
                });
            } catch (error) {
                log("Gün sekmesine tıklanamadı:", error.message);
                break;
            }
            for (let waited = 0; waited < 3000 && selectedDay() !== day; waited += 150) await sleep(150);
            if (selectedDay() !== day) { log(`${day} sekmesi açılmadı`); continue; }
            await sleep(150);
            result.gunler[day] = readDayBars();
        }
        log(`Popüler saatler: ${Object.keys(result.gunler).length} gün`);
        return result;
    }

    try {
        // Google sonuçları bazen geç boyanır; sabit bekleme yerine metin gelene kadar bekle (en fazla 10 sn).
        for (let waited = 0; waited < 6000 && bodyText().length < 400; waited += 250) await sleep(250);
        await sleep(300);

        const pageTitle = document.title.toLocaleLowerCase("tr-TR");
        const lowerText = bodyText().toLocaleLowerCase("tr-TR");
        if (location.pathname.startsWith("/sorry") || /captcha|robot/.test(pageTitle)
            || lowerText.includes("sıra dışı trafik") || lowerText.includes("robot olmadığınızı")) {
            await fail("Google CAPTCHA doğrulaması bekleniyor", true);
            return;
        }

        const normalizedName = parsers.cleanLine(venue.adi).toLocaleLowerCase("tr-TR");
        const nameParts = normalizedName.split(/\s+/).filter((part) => part.length >= 3);
        if (nameParts.length && !nameParts.some((part) => lowerText.includes(part))) {
            await save({
                id: venue.id, lease_token: venue.lease_token, source_url: location.href,
                degerlendirme_sayisi: venue.degerlendirme_sayisi,
                fiyat_saglayici: "Bulunamadı",
                saglayici_kaniti: `İlk Google sonucu mekan adıyla eşleşmedi (${document.title.slice(0, 60)})`,
                fiyatlar: [], gorseller: [],
            });
            return;
        }

        // Yorum sayısı: önce yalnız "5.413 Yorum" gibi tek başına duran küçük elemanlara bak
        // (panelde puan ile yorum sayısının metin olarak birleşmesini önler), sonra tüm metne.
        const leafReview = Array.from(document.querySelectorAll("span, a, div"))
            .filter((element) => element.children.length === 0)
            .map((element) => parsers.parseReviewCount(parsers.cleanLine(element.textContent)))
            .find((count) => count !== null);
        const liveReviewCount = leafReview ?? parsers.parseReviewCount(bodyText());
        if (liveReviewCount !== null && liveReviewCount < 10) {
            await fail(`Canlı değerlendirme sayısı 10 altında: ${liveReviewCount}`);
            return;
        }

        let popularTimes = null;
        try {
            popularTimes = await collectPopularTimes();
        } catch (error) {
            log("Popüler saatler okunamadı:", error.message);
        }

        const controls = Array.from(document.querySelectorAll("a, button, [role='button']"));
        const menuButton = controls.find((element) => {
            const text = parsers.cleanLine(element.innerText).toLocaleLowerCase("tr-TR");
            return /^(menü|menüyü göster|tüm menüyü göster|menüyü görüntüle)$/.test(text);
        });
        let candidates = menuCandidates();
        if (menuButton) {
            log("Menü düğmesi bulundu, tıklanıyor.");
            menuButton.click();
            // Panel eş zamansız yüklenir; menü diyaloğu görünene kadar bekle (en fazla 4 sn).
            // Fiyatlar geldiyse hemen; yalnız fotoğraf varsa fiyat sekmesi için 1 sn daha tanı.
            let photoOnlySince = null;
            for (let waited = 0; waited < 4000; waited += 250) {
                await sleep(250);
                candidates = menuCandidates();
                const best = candidates[0];
                if (!best) continue;
                if (best.prices >= 3) break;
                if (best.dialog && best.images > 0) {
                    photoOnlySince ??= waited;
                    if (waited - photoOnlySince >= 1000) break;
                }
            }
        } else {
            log("Menü düğmesi yok; sayfadaki mevcut paneller denenecek.");
        }

        const menuRoot = candidates[0]?.element;
        if (!menuRoot) {
            await save({
                id: venue.id, lease_token: venue.lease_token, source_url: location.href,
                degerlendirme_sayisi: liveReviewCount || venue.degerlendirme_sayisi,
                fiyat_saglayici: "Bulunamadı",
                saglayici_kaniti: menuButton ? "Menü düğmesi var, menü paneli yüklenmedi" : "Google panelinde Menü düğmesi yok",
                fiyatlar: [], gorseller: [], populer_saatler: popularTimes,
            });
            return;
        }

        const menuText = menuRoot.innerText || "";
        const links = Array.from(menuRoot.querySelectorAll("a[href]"), (anchor) => anchor.href);
        const providerInfo = parsers.detectProvider(menuText, links);
        const prices = parsers.extractMenuItems(menuText, providerInfo);
        if (!prices.length) {
            providerInfo.provider = "Yalnız Fotoğraf";
            providerInfo.evidence = "Menü panelinde fiyat listesi yok, menü fotoğrafları var";
        }
        // Paneldeki görseller iki bölümden gelir: "Menüde öne çıkanlar" (yemek fotoğrafı) ve
        // "Menüyü göster" (menü kartının fotoğrafı). Her görsel, kendisinden önce gelen son
        // bölüm başlığıyla etiketlenir; sunucu bunu kategori olarak saklar.
        const sectionHeadings = Array.from(menuRoot.querySelectorAll("h1, h2, h3, h4, div, span"))
            .filter((element) => /^(menüyü göster|menüde öne çıkanlar|menü fotoğrafları|fotoğraflar)$/
                .test(parsers.cleanLine(element.innerText).toLocaleLowerCase("tr-TR")));
        const sectionOf = (image) => {
            let label = "Menü";
            for (const heading of sectionHeadings) {
                if (heading.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING) label = parsers.cleanLine(heading.innerText);
            }
            return label;
        };
        const uniqueImages = new Map();
        for (const image of menuRoot.querySelectorAll("img")) {
            // Google, görünmeyen görselleri "data:image/gif" yer tutucuyla getirir; gerçek adres data-src'dedir.
            const src = [image.currentSrc, image.src, image.getAttribute("data-src")]
                .find((value) => /^https:\/\//.test(value || "")) || "";
            if (!src) continue;
            const cleaned = src.replace(/=w\d+-h\d+-.*/, "=w1080-h1080");
            if (cleaned.length > 30 && !uniqueImages.has(cleaned)) {
                uniqueImages.set(cleaned, { url: cleaned, guven_puani: 0.85, bolum: sectionOf(image) });
            }
        }

        await save({
            id: venue.id, lease_token: venue.lease_token, source_url: location.href,
            degerlendirme_sayisi: liveReviewCount || venue.degerlendirme_sayisi,
            fiyat_saglayici: providerInfo.provider, saglayici_kaniti: providerInfo.evidence,
            fiyatlar: prices, gorseller: Array.from(uniqueImages.values()).slice(0, 30),
            populer_saatler: popularTimes,
        });
    } catch (error) {
        await fail(`Betik hatası: ${error && error.message ? error.message : error}`);
    }
})();
