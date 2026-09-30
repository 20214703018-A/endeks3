const statusElement = document.getElementById("status");
const progressElement = document.getElementById("progress");
const citiesElement = document.getElementById("cities");

function renderQueue(queue = {}) {
    const completed = (queue.completed || 0) + (queue.completed_no_menu || 0);
    const remaining = (queue.queued || 0) + (queue.in_progress || 0) + (queue.blocked || 0);
    progressElement.innerText = `Tamamlanan: ${completed} · Kalan: ${remaining} · Başarısız: ${queue.failed || 0}`;
}

function renderCities(cities = {}) {
    citiesElement.innerText = Object.entries(cities)
        .sort(([left], [right]) => left.localeCompare(right, "tr"))
        .map(([city, count]) => `${city}: ${count}`)
        .join(" · ");
}

function refreshStatus() {
    chrome.runtime.sendMessage({ action: "status" }, (response) => {
        if (chrome.runtime.lastError || !response?.ok) {
            statusElement.innerText = "Yerel sunucuya bağlanılamadı.";
            return;
        }
        renderQueue(response.data.queue);
        renderCities(response.data.cities);
        if (response.state.pausedForCaptcha) {
            statusElement.innerText = "Google CAPTCHA bekleniyor — sekmede doğrulamayı tamamlayın, tarama kendiliğinden sürer.";
        } else if (response.data.current) {
            statusElement.innerText = `${response.state.isRunning ? "Aktif" : "Yarım kalan"}: ${response.data.current.adi}`;
        } else {
            statusElement.innerText = response.state.isRunning ? "Kuyruk bekleniyor..." : "Bekliyor...";
        }
    });
}

document.getElementById("startBtn").addEventListener("click", () => {
    statusElement.innerText = "Kaldığı yerden başlatılıyor...";
    chrome.runtime.sendMessage({ action: "start" });
});

document.getElementById("stopBtn").addEventListener("click", () => {
    statusElement.innerText = "Durduruldu; ilerleme korundu.";
    chrome.runtime.sendMessage({ action: "stop" });
});

chrome.runtime.onMessage.addListener((message) => {
    if (message.log) statusElement.innerText = message.log;
    if (message.queue) renderQueue(message.queue);
});

refreshStatus();
