(async function() {
    if (!window.location.href.includes("geoprop_bot=1")) return;

    console.log("GEOPROP BOT: Sayfa yüklendi, veri çekiliyor...");
    
    // Rastgele insanvari bekleme (1-2 sn)
    await new Promise(r => setTimeout(r, 1000 + Math.random() * 1000));
    
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

    // 1. GÖRSELLERİ BUL
    let images = document.querySelectorAll("g-scrolling-carousel img, div[data-attrid='kc:/local:menu'] img");
    images.forEach(img => {
        let src = img.src || img.getAttribute("data-src");
        if (src && src.startsWith("http")) {
            // Yüksek çözünürlüklü
            src = src.replace(/=w\d+-h\d+-.*/, "=w1080-h1080");
            payload.gorseller.push(src);
        }
    });

    // 2. FİYATLARI BUL (Yemeksepeti vb.)
    let menuBtn = Array.from(document.querySelectorAll("a, button, div[role='button']")).find(el => el.innerText && el.innerText.trim() === "Menü");
    
    if (menuBtn) {
        console.log("GEOPROP BOT: Menü butonuna tıklanıyor...");
        menuBtn.click();
        
        // Popup'ın yüklenmesini bekle
        await new Promise(r => setTimeout(r, 3000));
        
        let popupText = document.body.innerText;
        let provider = "Google_Arama_Panosu";
        if (popupText.includes("Sağlayan: Yemeksepeti")) provider = "Yemeksepeti";
        else if (popupText.includes("Sağlayan: Getir")) provider = "Getir";
        else if (popupText.includes("Sağlayan: Trendyol")) provider = "Trendyol";
        
        let regex = /([A-Za-zÇŞĞÜÖİçşğüöı\s]{3,40})\s*(\d+(?:,\d{2})?)\s*(?:TL|₺)/g;
        let match;
        while ((match = regex.exec(popupText)) !== null) {
            let urun = match[1].trim();
            if (urun.length >= 3) {
                payload.fiyatlar.push({
                    urun: urun,
                    fiyat: parseFloat(match[2].replace(',', '.')),
                    platform: provider
                });
            }
        }
    }

    // 3. SUNUCUYA GÖNDER VE SONRAKİNE GEÇ
    try {
        await fetch("http://127.0.0.1:5050/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        console.log("GEOPROP BOT: Veri kaydedildi.");
    } catch (e) {
        console.error("GEOPROP BOT: Sunucuya gönderilemedi!", e);
    }

    chrome.runtime.sendMessage({ action: "done_and_next" });
})();
