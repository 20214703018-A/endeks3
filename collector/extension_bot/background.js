let isRunning = false;
let currentTabId = null;

chrome.action.onClicked.addListener((tab) => {
    isRunning = !isRunning;
    if (isRunning) {
        console.log("Bot başlatıldı!");
        fetchNextAndNavigate();
    } else {
        console.log("Bot durduruldu!");
    }
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "done_and_next" && isRunning) {
        setTimeout(fetchNextAndNavigate, 2000); // 2 saniye bekle, ban yememek için
    }
    return true;
});

async function fetchNextAndNavigate() {
    try {
        let res = await fetch("http://127.0.0.1:5000/next");
        let data = await res.json();
        
        if (data.status === "done") {
            isRunning = false;
            console.log("Tüm mekanlar bitti!");
            return;
        }

        let query = encodeURIComponent(data.query);
        let url = `https://www.google.com/search?q=${query}&hl=tr&geoprop_bot=1`;
        
        // Veriyi content script'e aktarabilmek için storage'a yazalım
        await chrome.storage.local.set({ currentVenue: data });

        if (currentTabId) {
            chrome.tabs.update(currentTabId, { url: url });
        } else {
            chrome.tabs.create({ url: url }, (tab) => {
                currentTabId = tab.id;
            });
        }
    } catch (e) {
        console.error("Sunucuya bağlanılamadı. Lütfen python sunucusunun açık olduğundan emin olun.", e);
        isRunning = false;
    }
}
