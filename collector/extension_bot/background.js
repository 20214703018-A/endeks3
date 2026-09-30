// Arka plan servis çalışanı (Manifest V3). Kuyruğu yerel sunucudan çeker, sekmeyi yönlendirir,
// içerik betiğinden gelen sonucu kaydeder ve bir sonraki mekana geçer.
//
// Dayanıklılık kuralları:
//  - Her yönlendirmede bir bekçi alarmı kurulur; sayfa süresi içinde cevap vermezse mekan
//    "zaman aşımı" ile kuyruğa geri verilir ve tarama devam eder (sessiz takılma yok).
//  - Google CAPTCHA (/sorry/) görülürse tarama DURMAZ, duraklar; kullanıcı doğrulamayı
//    çözünce Google aynı arama sayfasına döner ve içerik betiği kaldığı yerden devam eder.
//  - Kiralama süresi dolmuş/geçersiz sonuçlar taramayı durdurmaz, bir sonraki mekana geçilir.
//  - Servis çalışanı uyuyup uyansa, eklenti yeniden yüklense de isRunning=true ise devam eder.
const API_BASE = "http://127.0.0.1:5050";
const MIN_DELAY_MS = 1000;   // İki mekan arası en az bekleme (Google'ı yormamak için)
const MAX_DELAY_MS = 2500;   // ... ve en çok bekleme; arada rastgele seçilir
const WATCHDOG_ALARM = "geoprop-watchdog";
const WATCHDOG_MINUTES = 0.5; // Chrome alarm alt sınırı 30 sn // İçerik betiğinden bu süre içinde haber gelmezse mekan bırakılır
const RESUME_ALARM = "geoprop-resume";

let inFlight = false; // Aynı anda iki kez /next çağrılmasını engeller
let nextTimer = null; // Bekleyen "sıradakine geç" zamanlayıcısı (ikinci bir tetikleme bunu çoğaltmaz)
const RENAVIGATE_GRACE_MS = 90_000; // Sunucu aynı mekanı "resumed" verirse ve sayfa yeni açıldıysa tekrar arama yapma

async function getState() {
    const state = await chrome.storage.local.get(["isRunning", "currentTabId", "currentVenue", "pausedForCaptcha"]);
    return {
        isRunning: Boolean(state.isRunning),
        currentTabId: state.currentTabId || null,
        currentVenue: state.currentVenue || null,
        pausedForCaptcha: Boolean(state.pausedForCaptcha),
    };
}

async function setRunning(isRunning) {
    await chrome.storage.local.set({ isRunning });
    if (!isRunning) await chrome.alarms.clear(WATCHDOG_ALARM);
}

function venueGoogleSearchUrl(venue) {
    const fallbackParts = [venue.adi, venue.tam_adres, venue.mahalle, venue.ilce, venue.il]
        .filter((value, index, values) => value && values.indexOf(value) === index);
    const url = new URL("https://www.google.com/search");
    url.searchParams.set("q", venue.query || fallbackParts.join(" "));
    url.searchParams.set("hl", "tr");
    url.searchParams.set("geoprop_bot", "1");
    return url.toString();
}

async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        let response;
        try {
            response = await fetch(`${API_BASE}${path}`, {
                ...options,
                headers: { "Content-Type": "application/json", ...(options.headers || {}) },
                signal: controller.signal,
            });
        } catch (networkError) {
            throw new Error(`Yerel sunucuya ulaşılamadı (${API_BASE}). server.py çalışıyor mu? ${networkError.message}`);
        }
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(data.message || data.error || `HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data;
    } finally {
        clearTimeout(timeout);
    }
}

function publishLog(log, extra = {}) {
    console.log("[GEOPROP]", log);
    chrome.runtime.sendMessage({ log, ...extra }).catch(() => {});
}

function randomDelay() {
    return MIN_DELAY_MS + Math.floor(Math.random() * (MAX_DELAY_MS - MIN_DELAY_MS));
}

async function armWatchdog() {
    await chrome.alarms.create(WATCHDOG_ALARM, { delayInMinutes: WATCHDOG_MINUTES });
}

async function openVenue(venue) {
    const state = await getState();
    const url = venueGoogleSearchUrl(venue);
    await chrome.storage.local.set({ currentVenue: venue, pausedForCaptcha: false, navigatedAt: Date.now() });
    await armWatchdog();
    if (state.currentTabId) {
        try {
            await chrome.tabs.get(state.currentTabId);
            await chrome.tabs.update(state.currentTabId, { url });
            return;
        } catch (_) {
            await chrome.storage.local.remove("currentTabId");
        }
    }
    const tab = await chrome.tabs.create({ url });
    await chrome.storage.local.set({ currentTabId: tab.id });
}

async function fetchNextAndNavigate() {
    if (inFlight) return;
    inFlight = true;
    try {
        const state = await getState();
        if (!state.isRunning) return;
        const venue = await api("/next");
        if (venue.status === "done") {
            await setRunning(false);
            await chrome.storage.local.remove("currentVenue");
            publishLog("Tüm uygun mekanlar tamamlandı.", { queue: venue.queue });
            return;
        }
        // Aynı kiralama zaten bu sekmede açıksa (ör. iki tetikleme üst üste geldi) ikinci kez arama yapma.
        const stored = await chrome.storage.local.get(["currentVenue", "navigatedAt"]);
        if (venue.resumed && stored.currentVenue?.lease_token === venue.lease_token
            && Date.now() - (stored.navigatedAt || 0) < RENAVIGATE_GRACE_MS) {
            publishLog(`Zaten açık: ${venue.adi} (tekrar aranmadı)`, { queue: venue.queue });
            return;
        }
        publishLog(`${venue.resumed ? "Devam" : "Taranıyor"}: ${venue.adi}`, { queue: venue.queue });
        await openVenue(venue);
    } catch (error) {
        // Sunucu yoksa devam etmenin anlamı yok; durdur ve nedenini göster.
        await setRunning(false);
        publishLog(`HATA: ${error.message}`);
    } finally {
        inFlight = false;
    }
}

async function scheduleNext(reason) {
    await chrome.alarms.clear(WATCHDOG_ALARM);
    if (!(await getState()).isRunning) return;
    const delay = randomDelay();
    if (reason) publishLog(`${reason} · ${Math.round(delay / 1000)} sn sonra sıradaki mekan`);
    // setTimeout servis çalışanı ~30 sn uyanık kaldığı için yeterli; alarm ise uyursa yedek olarak devreye girer.
    if (nextTimer) clearTimeout(nextTimer);
    nextTimer = setTimeout(() => { nextTimer = null; fetchNextAndNavigate(); }, delay);
    await chrome.alarms.create(RESUME_ALARM, { delayInMinutes: 0.5 });
}

// Sonuç/hata bildirimi kuyruk kiralamasıyla eşleşmiyorsa (süre dolmuş, bekçi zaten bırakmış
// vb.) bu bir "taramayı durdur" nedeni değildir; sadece sıradakine geç.
function isLeaseError(error) {
    return error.status === 400 && /kiralama/i.test(error.message);
}

async function finishAndContinue(payload) {
    try {
        const result = await api("/save", { method: "POST", body: JSON.stringify(payload) });
        await scheduleNext(`Kaydedildi: ${result.prices} fiyat, ${result.images} görsel`);
    } catch (error) {
        if (isLeaseError(error)) {
            await scheduleNext(`Kiralama eşleşmedi, atlandı (${error.message})`);
            return;
        }
        await setRunning(false);
        publishLog(`HATA (kayıt): ${error.message}`);
    }
}

async function failCurrent(payload) {
    try {
        const result = await api("/fail", { method: "POST", body: JSON.stringify(payload) });
        if (payload.blocked) {
            await pauseForCaptcha();
            return;
        }
        const label = result.status === "failed" ? "Deneme hakkı bitti, başarısız" : "Tekrar kuyruğa alındı";
        await scheduleNext(`${label}: ${payload.error.split("|")[0].trim()}`);
    } catch (error) {
        if (isLeaseError(error)) {
            await scheduleNext(`Kiralama eşleşmedi, atlandı (${error.message})`);
            return;
        }
        await setRunning(false);
        publishLog(`HATA (hata bildirimi): ${error.message}`);
    }
}

async function pauseForCaptcha() {
    await chrome.alarms.clear(WATCHDOG_ALARM);
    await chrome.storage.local.set({ pausedForCaptcha: true });
    publishLog("Google CAPTCHA algılandı. Sekmedeki doğrulamayı tamamlayın; tarama kendiliğinden devam eder.");
}

// Bekçi: sayfa belirlenen süre içinde ne kaydetti ne de hata bildirdi.
async function watchdogFired() {
    const state = await getState();
    if (!state.isRunning || state.pausedForCaptcha || !state.currentVenue) return;
    let tabUrl = "";
    try {
        tabUrl = (await chrome.tabs.get(state.currentTabId)).url || "";
    } catch (_) {
        tabUrl = "";
    }
    if (tabUrl.includes("google.com/sorry")) {
        await pauseForCaptcha();
        return;
    }
    const venue = state.currentVenue;
    publishLog(`Zaman aşımı: "${venue.adi}" için sayfa yanıt vermedi (${tabUrl.slice(0, 60) || "sekme kapalı"})`);
    await failCurrent({
        id: venue.id, lease_token: venue.lease_token, blocked: false,
        error: `Zaman aşımı: içerik betiği ${WATCHDOG_MINUTES} dk içinde yanıt vermedi | url: ${tabUrl.slice(0, 200)}`,
    });
}

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === WATCHDOG_ALARM) watchdogFired();
    else if (alarm.name === RESUME_ALARM) resumeIfIdle();
});

// Servis çalışanı setTimeout'u kaçırdıysa (uyuduysa) kaldığı yerden toparla.
async function resumeIfIdle() {
    const state = await getState();
    if (!state.isRunning || state.pausedForCaptcha) return;
    const alarm = await chrome.alarms.get(WATCHDOG_ALARM);
    if (!alarm) fetchNextAndNavigate();
}

// CAPTCHA'yı fark etmenin en erken yolu: bot sekmesinin adresi /sorry/ olursa.
// Kullanıcı çözünce Google orijinal aramaya (geoprop_bot=1) döner; duraklamayı kaldır, bekçiyi yeniden kur.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
    if (!changeInfo.url) return;
    const state = await getState();
    if (!state.isRunning || tabId !== state.currentTabId) return;
    if (changeInfo.url.includes("google.com/sorry")) {
        if (!state.pausedForCaptcha) await pauseForCaptcha();
    } else if (state.pausedForCaptcha && changeInfo.url.includes("google.com/search")) {
        await chrome.storage.local.set({ pausedForCaptcha: false });
        await armWatchdog();
        publishLog("CAPTCHA geçildi, tarama devam ediyor.");
    }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
    const state = await getState();
    if (tabId !== state.currentTabId) return;
    await chrome.storage.local.remove("currentTabId");
    if (state.isRunning) {
        publishLog("Bot sekmesi kapatıldı; yeni sekmede devam ediliyor.");
        await chrome.alarms.clear(WATCHDOG_ALARM);
        setTimeout(fetchNextAndNavigate, 1000);
    }
});

// Google, betikle üretilen (isTrusted=false) tıklamaları yok sayar; "popüler saatler" grafiğinde
// gün değiştirmek için DevTools protokolüyle gerçek fare olayı üretilir. Sekmede kısa süreli
// "GEOPROP ... bu tarayıcıda hata ayıklıyor" şeridi görünmesi normaldir.
async function trustedClicks(tabId, points) {
    const target = { tabId };
    await chrome.debugger.attach(target, "1.3");
    try {
        for (const point of points) {
            for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
                await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
                    type, x: point.x, y: point.y, button: type === "mouseMoved" ? "none" : "left", clickCount: 1,
                });
            }
            if (point.waitMs) await new Promise((resolve) => setTimeout(resolve, point.waitMs));
        }
    } finally {
        await chrome.debugger.detach(target).catch(() => {});
    }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    (async () => {
        if (request.action === "whoami") {
            const state = await getState();
            const isBotTab = Boolean(state.isRunning && sender.tab && sender.tab.id === state.currentTabId);
            sendResponse({ isBotTab, venue: isBotTab ? state.currentVenue : null });
            return;
        }
        if (request.action === "start") {
            await chrome.storage.local.set({ pausedForCaptcha: false });
            await setRunning(true);
            await fetchNextAndNavigate();
        } else if (request.action === "stop") {
            await setRunning(false);
            publishLog("Tarama durduruldu; kuyruk ilerlemesi korundu.");
        } else if (request.action === "save_result") {
            await finishAndContinue(request.payload);
        } else if (request.action === "fail_current") {
            await failCurrent(request.payload);
        } else if (request.action === "trusted_click") {
            const state = await getState();
            if (!sender.tab || sender.tab.id !== state.currentTabId) throw new Error("Bot sekmesi değil");
            await trustedClicks(sender.tab.id, request.points || []);
        } else if (request.action === "status") {
            sendResponse({ ok: true, data: await api("/status"), state: await getState() });
            return;
        }
        sendResponse({ ok: true });
    })().catch((error) => {
        publishLog(`HATA: ${error.message}`);
        sendResponse({ ok: false, error: error.message });
    });
    return true;
});

// Tarayıcı açılışında, eklenti yeniden yüklendiğinde veya güncellendiğinde kaldığı yerden devam et.
chrome.runtime.onStartup.addListener(resumeIfIdle);
chrome.runtime.onInstalled.addListener(resumeIfIdle);
