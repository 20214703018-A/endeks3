document.getElementById('startBtn').addEventListener('click', () => {
    document.getElementById('status').innerText = "Bot başlatılıyor...";
    chrome.runtime.sendMessage({ action: "start" });
});

document.getElementById('stopBtn').addEventListener('click', () => {
    document.getElementById('status').innerText = "Durduruldu.";
    chrome.runtime.sendMessage({ action: "stop" });
});

chrome.runtime.onMessage.addListener((msg) => {
    if (msg.log) {
        document.getElementById('status').innerText = msg.log;
    }
});
