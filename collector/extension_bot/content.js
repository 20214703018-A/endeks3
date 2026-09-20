(async function() {
    if (!window.location.href.includes("geoprop_bot=1")) return;
    
    await new Promise(r => setTimeout(r, 1500)); 
    
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
        tam_adres: venue.tam_adres,
        lat: venue.lat,
        lon: venue.lon,
        gorseller: [],
        fiyatlar: []
    };

    let images = document.querySelectorAll("img");
    let uniqueImages = new Set();
    images.forEach(img => {
        let src = img.src || img.getAttribute("data-src") || "";
        if (src.includes("googleusercontent.com") || src.includes("encrypted-tbn0.gstatic.com")) {
            let clean = src.replace(/=w\d+-h\d+-.*/, "=w1080-h1080");
            if(clean.length > 20) uniqueImages.add(clean);
        }
    });
    payload.gorseller = Array.from(uniqueImages).slice(0, 10);

    let allBtns = Array.from(document.querySelectorAll("a, button, div[role='button']"));
    let menuBtn = allBtns.find(el => {
        let t = (el.innerText || "").trim().toLowerCase();
        return t === "menü" || t === "menüyü göster" || t === "tüm menüyü göster";
    });
    
    if (menuBtn) {
        menuBtn.click();
        await new Promise(r => setTimeout(r, 4000));
        
        let popupText = document.body.innerText;
        let provider = "Google_Arama_Panosu";
        if (popupText.includes("Sağlayan: Yemeksepeti")) provider = "Yemeksepeti";
        else if (popupText.includes("Sağlayan: Getir")) provider = "Getir";
        else if (popupText.includes("Sağlayan: Trendyol")) provider = "Trendyol";
        
        let regex1 = /([A-Za-zÇŞĞÜÖİçşğüöı\s\-\'&]{3,50})[\r\n\s]+(\d{1,4}(?:[.,]\d{2})?)\s*(?:TL|₺)/g;
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
