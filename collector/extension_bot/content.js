(async function() {
    if (!window.location.href.includes("geoprop_bot=1")) return;
    
    await new Promise(r => setTimeout(r, 1500)); // Sayfanın oturmasını bekle
    
    // CAPTCHA KONTROLÜ
    let bodyText = document.body.innerText.toLowerCase();
    if (document.title.includes("CAPTCHA") || document.title.includes("Robot") || bodyText.includes("sıra dışı trafik")) {
        console.error("GEOPROP BOT: CAPTCHA TESPİT EDİLDİ!");
        chrome.runtime.sendMessage({ log: "HATA: CAPTCHA ÇIKTI! Lütfen kutuyu çözün." });
        return;
    }
    
    let storage = await chrome.storage.local.get("currentVenue");
    let venue = storage.currentVenue;
    if (!venue) return;

    let payload = {
        id: venue.id,
        adi: venue.adi,
        il: venue.il,
        ilce: venue.ilce,
        mahalle: venue.mahalle,
        gorseller: [],
        fiyatlar: []
    };

    // 1. GÖRSELLERİ BUL (Sınıf bağımsız, doğrudan Google resim sunucusu linklerini yakala)
    let images = document.querySelectorAll("img");
    let uniqueImages = new Set();
    images.forEach(img => {
        let src = img.src || img.getAttribute("data-src") || "";
        // Sadece googleusercontent resimlerini al (Harita/Mekan fotoğrafları)
        if (src.includes("googleusercontent.com/p/") || src.includes("googleusercontent.com/gps-cs-s/")) {
            // Yüksek çözünürlüğe çevir ve kaydet
            let highRes = src.replace(/=w\d+-h\d+-.*/, "=w1080-h1080");
            if(highRes.length > 20) {
                uniqueImages.add(highRes);
            }
        }
    });
    payload.gorseller = Array.from(uniqueImages);

    // 2. MENÜ BUTONUNA TIKLA VE FİYATLARI BUL
    let allBtns = Array.from(document.querySelectorAll("a, button, div[role='button']"));
    let menuBtn = allBtns.find(el => {
        let t = (el.innerText || "").trim().toLowerCase();
        return t === "menü" || t === "menüyü göster" || t === "tüm menüyü göster";
    });
    
    if (menuBtn) {
        menuBtn.click();
        await new Promise(r => setTimeout(r, 4000)); // Popup'ın yüklenmesi için bolca bekle
        
        let popupText = document.body.innerText;
        let provider = "Google_Arama_Panosu";
        if (popupText.includes("Sağlayan: Yemeksepeti")) provider = "Yemeksepeti";
        else if (popupText.includes("Sağlayan: Getir")) provider = "Getir";
        else if (popupText.includes("Sağlayan: Trendyol")) provider = "Trendyol";
        
        // Fiyat Regex 1: Tavuk Şiş 250 TL veya 250,00 TL
        let regex1 = /([A-Za-zÇŞĞÜÖİçşğüöı\s\-\'&]{3,50})[\r\n\s]+(\d{1,4}(?:[.,]\d{2})?)\s*(?:TL|₺)/g;
        // Fiyat Regex 2: Tavuk Şiş ₺250 veya ₺ 250.00
        let regex2 = /([A-Za-zÇŞĞÜÖİçşğüöı\s\-\'&]{3,50})[\r\n\s]+(?:TL|₺)\s*(\d{1,4}(?:[.,]\d{2})?)/g;
        
        let match;
        while ((match = regex1.exec(popupText)) !== null) {
            let urun = match[1].trim();
            if (urun.length >= 3 && !urun.toLowerCase().includes("çalışma saat")) {
                payload.fiyatlar.push({ urun: urun, fiyat: parseFloat(match[2].replace(',', '.')), platform: provider });
            }
        }
        while ((match = regex2.exec(popupText)) !== null) {
            let urun = match[1].trim();
            if (urun.length >= 3 && !urun.toLowerCase().includes("çalışma saat")) {
                payload.fiyatlar.push({ urun: urun, fiyat: parseFloat(match[2].replace(',', '.')), platform: provider });
            }
        }
    }

    // 3. SUNUCUYA GÖNDER
    try {
        await fetch("http://127.0.0.1:5050/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
    } catch (e) {
        console.error("GEOPROP BOT: Sunucu hatası!", e);
    }

    chrome.runtime.sendMessage({ action: "done_and_next" });
})();
