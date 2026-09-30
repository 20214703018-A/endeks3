(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.GeopropMenuParsers = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    function cleanLine(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
    }

    function parseTurkishPrice(value) {
        let text = cleanLine(value).replace(/(?:TL|TRY|₺)/gi, "").replace(/\s/g, "");
        if (!text || !/^\d[\d.,]*$/.test(text)) return null;
        const lastComma = text.lastIndexOf(",");
        const lastDot = text.lastIndexOf(".");
        if (lastComma >= 0 && lastDot >= 0) {
            const decimalIndex = Math.max(lastComma, lastDot);
            text = text.slice(0, decimalIndex).replace(/[.,]/g, "") + "." + text.slice(decimalIndex + 1);
        } else if (lastComma >= 0) {
            const decimals = text.length - lastComma - 1;
            text = decimals === 2 ? text.replace(/\./g, "").replace(",", ".") : text.replace(/,/g, "");
        } else if (lastDot >= 0) {
            const decimals = text.length - lastDot - 1;
            text = decimals === 2 ? text.replace(/,/g, "") : text.replace(/\./g, "");
        }
        const parsed = Number(text);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    }

    function detectProvider(text, links = []) {
        const rawText = String(text || "");
        const normalized = cleanLine(rawText);
        const explicit = rawText.match(/Sağlayan\s*:\s*([^·|\r\n]{2,80})/i);
        if (explicit) return { provider: cleanLine(explicit[1]), evidence: cleanLine(explicit[0]) };
        const haystack = `${normalized} ${links.join(" ")}`.toLocaleLowerCase("tr-TR");
        const known = [
            ["Yemeksepeti", /yemeksepeti/],
            ["GetirYemek", /getir(?:yemek)?/],
            ["Trendyol Yemek", /trendyol/],
            ["Migros Yemek", /migros/],
            ["Fuudy", /fuudy/],
            ["Restoranın Resmî Menüsü", /resm[iî].*men[üu]|official.*menu/],
        ];
        for (const [provider, pattern] of known) {
            if (pattern.test(haystack)) return { provider, evidence: `Alan adı veya panel metni: ${provider}` };
        }
        return { provider: "Google Menü Paneli", evidence: "Google panelinde harici sağlayıcı etiketi bulunamadı" };
    }

    // Google bilgi panelinde puan ve yorum sayısı yan yana basılır ("3,8" + "5.413 Yorum");
    // innerText bunları "3,85.413 Yorum" diye birleştirebilir. Bu yüzden sayının önünde
    // başka bir rakam, nokta veya virgül olmasına izin verilmez.
    function parseReviewCount(text) {
        const patterns = [
            /(?:^|[^\d.,])(\d[\d.]*)\s*(?:Google\s+)?değerlendirme/i,
            /(?:^|[^\d.,])(\d[\d.]*)\s*(?:kullanıcı\s+)?yorum/i,
            /\((\d[\d.]*)\)\s*(?:kullanıcı\s+)?yorumu/i,
        ];
        for (const pattern of patterns) {
            const match = String(text || "").match(pattern);
            if (match) return Number(match[1].replace(/\./g, ""));
        }
        return null;
    }

    // Bir metinde kaç gerçek fiyat ifadesi var ("₺815,00", "350 TL")? Panel seçiminde
    // "TL" harflerinin sıradan kelimelerde ("Yanıtlarınız") geçmesiyle yanılmamak için
    // rakam zorunlu tutulur.
    function countPriceMentions(text) {
        const matches = String(text || "").match(/(?:₺\s*\d[\d.,]*|\d[\d.,]*\s*(?:TL|TRY|₺)(?![\p{L}]))/giu);
        return matches ? matches.length : 0;
    }

    function validProductName(value) {
        const product = cleanLine(value).replace(/^[·•\-–—]+\s*/, "");
        if (product.length < 2 || product.length > 180) return null;
        if (/^(menü|fiyat|tl|try|daha fazla|çalışma saatleri)$/i.test(product)) return null;
        if (/https?:\/\//i.test(product)) return null;
        return product;
    }

    function extractMenuItems(text, providerInfo) {
        const lines = String(text || "").split(/\r?\n/).map(cleanLine).filter(Boolean);
        const results = [];
        const seen = new Set();
        const pricePattern = /(?:^|\s)(₺\s*[\d.,]+|[\d.,]+\s*(?:TL|TRY|₺))(?:\s|$)/i;
        for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index];
            const priceMatch = line.match(pricePattern);
            if (!priceMatch) continue;
            const price = parseTurkishPrice(priceMatch[1]);
            if (price === null || price > 100000) continue;
            const inlineName = cleanLine(line.replace(priceMatch[0], " "));
            const product = validProductName(inlineName) || validProductName(lines[index - 1]);
            if (!product) continue;
            const key = `${product.toLocaleLowerCase("tr-TR")}|${price}|${providerInfo.provider}`;
            if (seen.has(key)) continue;
            seen.add(key);
            results.push({
                urun: product, fiyat: price, ham_fiyat_metni: priceMatch[1],
                saglayici: providerInfo.provider, saglayici_kaniti: providerInfo.evidence,
                yakalama_yontemi: "google_menu_panel_line_parser",
                guven_puani: inlineName ? 0.92 : 0.82, kategori: "Menü",
            });
        }
        return results;
    }

    // Yalnız mekan paneline özgü biçim: "1.234 Google yorumu" ya da "56 değerlendirme".
    function parsePanelReviewCount(text) {
        const match = String(text || "").match(/(?:^|[^\d.,])(\d[\d.]*)\s*(?:Google\s+(?:yorumu|yorum|değerlendirmesi)|değerlendirme)/i);
        return match ? Number(match[1].replace(/\./g, "")) : null;
    }

    return { cleanLine, parseTurkishPrice, detectProvider, parseReviewCount, parsePanelReviewCount, countPriceMentions, extractMenuItems };
});
