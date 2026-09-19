let isRunning = false;
let currentTabId = null;

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "start") {
        isRunning = true;
        chrome.runtime.sendMessage({ log: "Sunucuya bağlanılıyor..." });
        fetchNextAndNavigate();
    } else if (request.action === "stop") {
        isRunning = false;
    } else if (request.action === "done_and_next" && isRunning) {
        chrome.runtime.sendMessage({ log: "Sıradaki mekana geçiliyor..." });
        setTimeout(fetchNextAndNavigate, 2000);
    }
    return true;
});

async function fetchNextAndNavigate() {
    if (!isRunning) return;
    try {
        let res = await fetch("http://127.0.0.1:5050/next");
        let data = await res.json();
        
        if (data.status === "done") {
            isRunning = false;
            chrome.runtime.sendMessage({ log: "Tüm mekanlar bitti!" });
            return;
        }

        chrome.runtime.sendMessage({ log: "Taraniyor: " + data.query });
        let query = encodeURIComponent(data.query);
        let url = `https://www.google.com/search?q=${query}&hl=tr&geoprop_bot=1`;
        
        await chrome.storage.local.set({ currentVenue: data });

        if (currentTabId) {
            chrome.tabs.update(currentTabId, { url: url });
        } else {
            chrome.tabs.create({ url: url }, (tab) => {
                currentTabId = tab.id;
            });
        }
    } catch (e) {
        isRunning = false;
        chrome.runtime.sendMessage({ log: "HATA: Sunucuya bağlanılamadı (server.py açık mı?)" });
    }
}
