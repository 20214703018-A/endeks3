// Arka plan servis çalışanı (Manifest V3). Kuyruğu yerel sunucudan çeker, bot pencerelerini yönlendirir,
// içerik betiğinden gelen sonucu kaydeder ve bir sonraki mekana geçer.
//
// Paralel tarama (2.4): 1–4 "çalışan" (yuva) aynı anda tarar; her yuvanın kendi penceresi, kiralaması ve
// bekçisi vardır. Pencereler üst üste kaydırılarak açılır: arka plan sekmeleri Chrome'da yavaşlatıldığı ve
// görselleri yüklenmediği için her çalışan görünür (küçültülmemiş) bir pencerede çalışır.
//
// Dayanıklılık kuralları:
//  - Her yönlendirmede yuvaya özel bir bekçi alarmı kurulur; sayfa süresi içinde cevap vermezse mekan
//    "zaman aşımı" ile kuyruğa geri verilir ve o yuva devam eder (sessiz takılma yok).
//  - Google CAPTCHA (/sorry/) görülürse BÜTÜN yuvalar duraklar (engel IP düzeyinde); kullanıcı doğrulamayı
//    çözünce devam edilir ve otomatik fren paralel çalışan sayısını bir azaltır.
//  - Kiralama süresi dolmuş/geçersiz sonuçlar taramayı durdurmaz, bir sonraki mekana geçilir.
//  - Servis çalışanı uyuyup uyansa, eklenti yeniden yüklense de isRunning=true ise devam eder.
const API_BASE = "http://127.0.0.1:5050";
const MIN_DELAY_MS = 800;    // Bir yuvada iki mekan arası en az bekleme (Google'ı yormamak için)
const MAX_DELAY_MS = 2000;   // ... ve en çok bekleme; arada rastgele seçilir
const MAX_WORKERS = 4;
const DEFAULT_WORKERS = 2;
const WATCHDOG_PREFIX = "geoprop-watchdog-";
// İçerik betiği bu süre içinde ne sonuç ne de "heartbeat" gönderirse mekan bırakılır. Betik her
// adımda (sayfa açıldı, popüler saatler, menü) heartbeat gönderip süreyi yeniler.
const WATCHDOG_MINUTES = 1;
const RESUME_ALARM = "geoprop-resume";
// Otomatik fren: CAPTCHA geçildikten sonra çalışan sayısı bir azalır; bu süre CAPTCHA'sız geçerse bir artar
// (kullanıcının seçtiği sayıyı aşmadan).
const RAMP_UP_MS = 30 * 60 * 1000;
const RENAVIGATE_GRACE_MS = 90_000; // Sunucu aynı mekanı "resumed" verirse ve sayfa yeni açıldıysa tekrar arama yapma

const inFlight = new Set();   // Aynı yuva için aynı anda iki kez /next çağrılmasını engeller
const nextTimers = new Map(); // yuva → bekleyen "sıradakine geç" zamanlayıcısı
const slotKey = (slot) => `slot_${slot}`;

async function getState() {
    const state = await chrome.storage.local.get([
        "isRunning", "pausedForCaptcha", "workerCount", "activeWorkers", "lastCaptchaAt",
    ]);
    const workerCount = Math.min(MAX_WORKERS, Math.max(1, Number(state.workerCount) || DEFAULT_WORKERS));
    let activeWorkers = Math.min(workerCount, Math.max(1, Number(state.activeWorkers) || workerCount));
    if (activeWorkers < workerCount && state.lastCaptchaAt && Date.now() - state.lastCaptchaAt > RAMP_UP_MS) {
        activeWorkers += 1;
        await chrome.storage.local.set({ activeWorkers, lastCaptchaAt: Date.now() });
        publishLog(`30 dk CAPTCHA'sız geçti: paralel pencere ${activeWorkers}'e çıkarıldı.`);
    }
    return {
        isRunning: Boolean(state.isRunning),
        pausedForCaptcha: Boolean(state.pausedForCaptcha),
        workerCount, activeWorkers,
    };
}

async function getSlot(slot) {
    return (await chrome.storage.local.get(slotKey(slot)))[slotKey(slot)] || {};
}

async function setSlot(slot, patch) {
    const current = await getSlot(slot);
    await chrome.storage.local.set({ [slotKey(slot)]: { ...current, ...patch } });
}

async function slotOfTab(tabId) {
    for (let slot = 1; slot <= MAX_WORKERS; slot += 1) {
        if ((await getSlot(slot)).tabId === tabId) return slot;
    }
    return null;
}

async function setRunning(isRunning) {
    await chrome.storage.local.set({ isRunning });
    if (!isRunning) {
        for (let slot = 1; slot <= MAX_WORKERS; slot += 1) await chrome.alarms.clear(WATCHDOG_PREFIX + slot);
        for (const timer of nextTimers.values()) clearTimeout(timer);
        nextTimers.clear();
    }
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

async function armWatchdog(slot) {
    await chrome.alarms.create(WATCHDOG_PREFIX + slot, { delayInMinutes: WATCHDOG_MINUTES });
}

async function openVenue(slot, venue) {
    const url = venueGoogleSearchUrl(venue);
    const current = await getSlot(slot);
    await setSlot(slot, { venue, navigatedAt: Date.now() });
    await armWatchdog(slot);
    if (current.tabId) {
        try {
            await chrome.tabs.get(current.tabId);
            await chrome.tabs.update(current.tabId, { url });
            return;
        } catch (_) {
            await setSlot(slot, { tabId: null });
        }
    }
    // Her yuva kendi penceresinde; pencereler 48 px kaydırılarak açılır ki hiçbiri tamamen örtülmesin
    // (tamamen örtülen/küçültülen pencere Chrome'da "gizli" sayılır ve yavaşlatılır).
    const offset = (slot - 1) * 48;
    const win = await chrome.windows.create({
        url, focused: slot === 1, left: 20 + offset, top: 20 + offset, width: 1150, height: 860,
    });
    await setSlot(slot, { tabId: win.tabs[0].id, windowId: win.id });
}

async function fetchNextAndNavigate(slot) {
    if (inFlight.has(slot)) return;
    inFlight.add(slot);
    try {
        const state = await getState();
        if (!state.isRunning || state.pausedForCaptcha) return;
        if (slot > state.activeWorkers) {
            await parkSlot(slot);
            return;
        }
        const venue = await api(`/next?worker=${slot}`);
        if ((await getState()).pausedForCaptcha) return; // istek sürerken CAPTCHA çıktı; devamda aynı kiralama geri gelir
        if (venue.status === "done") {
            await setSlot(slot, { venue: null });
            const others = await Promise.all(
                Array.from({ length: MAX_WORKERS }, (_, i) => getSlot(i + 1)),
            );
            if (!others.some((s) => s.venue)) {
                await setRunning(false);
                publishLog("Tüm uygun mekanlar tamamlandı.", { queue: venue.queue });
            }
            return;
        }
        // Aynı kiralama zaten bu yuvada açıksa (ör. iki tetikleme üst üste geldi) ikinci kez arama yapma.
        const stored = await getSlot(slot);
        if (venue.resumed && stored.venue?.lease_token === venue.lease_token
            && Date.now() - (stored.navigatedAt || 0) < RENAVIGATE_GRACE_MS) {
            return;
        }
        publishLog(`[${slot}] ${venue.resumed ? "Devam" : "Taranıyor"}: ${venue.adi}`, { queue: venue.queue });
        await openVenue(slot, venue);
    } catch (error) {
        // Sunucu yoksa devam etmenin anlamı yok; durdur ve nedenini göster.
        await setRunning(false);
        publishLog(`HATA: ${error.message}`);
    } finally {
        inFlight.delete(slot);
    }
}

// Otomatik fren ya da kullanıcı çalışan sayısını azalttıysa fazla yuvanın penceresi kapatılır.
async function parkSlot(slot) {
    await chrome.alarms.clear(WATCHDOG_PREFIX + slot);
    const current = await getSlot(slot);
    await chrome.storage.local.set({ [slotKey(slot)]: {} });
    if (current.windowId) await chrome.windows.remove(current.windowId).catch(() => {});
}

async function scheduleNext(slot, reason) {
    await chrome.alarms.clear(WATCHDOG_PREFIX + slot);
    await setSlot(slot, { venue: null });
    if (!(await getState()).isRunning) return;
    const delay = randomDelay();
    if (reason) publishLog(`[${slot}] ${reason}`);
    // setTimeout servis çalışanı uyanıkken yeterli; alarm ise uyursa yedek olarak devreye girer.
    if (nextTimers.has(slot)) clearTimeout(nextTimers.get(slot));
    nextTimers.set(slot, setTimeout(() => { nextTimers.delete(slot); fetchNextAndNavigate(slot); }, delay));
    await chrome.alarms.create(RESUME_ALARM, { delayInMinutes: 0.5 });
}

// Sunucu yanıt verdi ama bu mekanın sonucunu kabul etmedi (kiralama süresi dolmuş, doğrulama hatası
// vb.): bu bir "taramayı durdur" nedeni değildir, sadece sıradakine geç. Tarama yalnız sunucuya hiç
// ulaşılamazsa (status yok) durur; eskiden her 400 yanıtı bütün pencereleri durduruyordu.
function isLeaseError(error) {
    return Boolean(error.status);
}

async function finishAndContinue(slot, payload) {
    try {
        const result = await api("/save", { method: "POST", body: JSON.stringify(payload) });
        await scheduleNext(slot, result.status === "failed"
            ? `Atlandı: ${result.reason}` : `Kaydedildi: ${result.prices} fiyat, ${result.images} görsel`);
    } catch (error) {
        if (isLeaseError(error)) {
            await scheduleNext(slot, `Sunucu kaydı kabul etmedi, atlandı (${error.message})`);
            return;
        }
        await setRunning(false);
        publishLog(`HATA (kayıt): ${error.message}`);
    }
}

async function failCurrent(slot, payload) {
    try {
        const result = await api("/fail", { method: "POST", body: JSON.stringify(payload) });
        if (payload.blocked) {
            await pauseForCaptcha(slot);
            return;
        }
        const label = result.status === "failed" ? "Deneme hakkı bitti, başarısız" : "Tekrar kuyruğa alındı";
        await scheduleNext(slot, `${label}: ${payload.error.split("|")[0].trim()}`);
    } catch (error) {
        if (isLeaseError(error)) {
            await scheduleNext(slot, `Kiralama eşleşmedi, atlandı (${error.message})`);
            return;
        }
        await setRunning(false);
        publishLog(`HATA (hata bildirimi): ${error.message}`);
    }
}

async function pauseForCaptcha(slot) {
    const state = await getState();
    for (let slot = 1; slot <= MAX_WORKERS; slot += 1) await chrome.alarms.clear(WATCHDOG_PREFIX + slot);
    for (const timer of nextTimers.values()) clearTimeout(timer);
    nextTimers.clear();
    if (state.pausedForCaptcha) return;
    // Otomatik fren: CAPTCHA'dan sonra bir pencere eksik devam edilir.
    const activeWorkers = Math.max(1, state.activeWorkers - 1);
    await chrome.storage.local.set({ pausedForCaptcha: true, captchaSlot: slot, activeWorkers, lastCaptchaAt: Date.now() });
    publishLog(`[${slot}] Google CAPTCHA algılandı. O penceredeki doğrulamayı tamamlayın; tarama ${activeWorkers} pencereyle kendiliğinden sürer.`);
}

async function resumeAfterCaptcha() {
    const { captchaSlot } = await chrome.storage.local.get("captchaSlot");
    await chrome.storage.local.set({ pausedForCaptcha: false, captchaSlot: null });
    publishLog("CAPTCHA geçildi, tarama devam ediyor.");
    const state = await getState();
    for (let slot = 1; slot <= MAX_WORKERS; slot += 1) {
        if (slot === captchaSlot) await setSlot(slot, { venue: null });
        const current = await getSlot(slot);
        if (slot > state.activeWorkers) await parkSlot(slot);
        else if (current.venue) await armWatchdog(slot);
        else fetchNextAndNavigate(slot);
    }
}

// Bekçi: sayfa belirlenen süre içinde ne kaydetti ne de hata bildirdi.
async function watchdogFired(slot) {
    const state = await getState();
    const current = await getSlot(slot);
    if (!state.isRunning || state.pausedForCaptcha || !current.venue) return;
    let tabUrl = "";
    try {
        tabUrl = (await chrome.tabs.get(current.tabId)).url || "";
    } catch (_) {
        tabUrl = "";
    }
    if (tabUrl.includes("google.com/sorry")) {
        await pauseForCaptcha(slot);
        return;
    }
    const venue = current.venue;
    publishLog(`[${slot}] Zaman aşımı: "${venue.adi}" için sayfa yanıt vermedi (${tabUrl.slice(0, 60) || "sekme kapalı"})`);
    await failCurrent(slot, {
        id: venue.id, lease_token: venue.lease_token, blocked: false,
        error: `Zaman aşımı: içerik betiği ${WATCHDOG_MINUTES} dk içinde yanıt vermedi | url: ${tabUrl.slice(0, 200)}`,
    });
}

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name.startsWith(WATCHDOG_PREFIX)) watchdogFired(Number(alarm.name.slice(WATCHDOG_PREFIX.length)));
    else if (alarm.name === RESUME_ALARM) resumeIfIdle();
});

// Servis çalışanı setTimeout'u kaçırdıysa (uyuduysa) kaldığı yerden toparla: bekçisi olmayan her
// etkin yuva sıradaki mekanı ister, fazla yuvalar kapanır.
async function resumeIfIdle() {
    const state = await getState();
    if (!state.isRunning || state.pausedForCaptcha) return;
    for (let slot = 1; slot <= MAX_WORKERS; slot += 1) {
        if (slot > state.activeWorkers) {
            if ((await getSlot(slot)).windowId) await parkSlot(slot);
            continue;
        }
        const alarm = await chrome.alarms.get(WATCHDOG_PREFIX + slot);
        if (!alarm && !nextTimers.has(slot)) fetchNextAndNavigate(slot);
    }
}

// CAPTCHA'yı fark etmenin en erken yolu: bot sekmesinin adresi /sorry/ olursa.
// Kullanıcı çözünce Google orijinal aramaya döner; duraklamayı kaldır, yuvaları sürdür.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
    if (!changeInfo.url) return;
    const state = await getState();
    const slot = await slotOfTab(tabId);
    if (!state.isRunning || slot === null) return;
    if (changeInfo.url.includes("google.com/sorry")) {
        if (!state.pausedForCaptcha) await pauseForCaptcha(slot);
    } else if (state.pausedForCaptcha && changeInfo.url.includes("google.com/search")) {
        // Yalnız CAPTCHA'nın çıktığı pencere aramaya dönünce devam edilir (diğer pencerelerin sayfa
        // değişimi doğrulamanın geçildiği anlamına gelmez).
        const { captchaSlot } = await chrome.storage.local.get("captchaSlot");
        if (!captchaSlot || captchaSlot === slot) await resumeAfterCaptcha();
    }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
    const slot = await slotOfTab(tabId);
    if (slot === null) return;
    await setSlot(slot, { tabId: null, windowId: null });
    const state = await getState();
    if (state.isRunning && slot <= state.activeWorkers) {
        publishLog(`[${slot}] Bot penceresi kapatıldı; yeni pencerede devam ediliyor.`);
        await chrome.alarms.clear(WATCHDOG_PREFIX + slot);
        setTimeout(() => fetchNextAndNavigate(slot), 1000);
    }
});

// Google, betikle üretilen (isTrusted=false) tıklamaları yok sayar; "popüler saatler" grafiğinde
// gün değiştirmek için DevTools protokolüyle gerçek fare olayı üretilir. Sekmede kısa süreli
// "GEOPROP ... bu tarayıcıda hata ayıklıyor" şeridi görünmesi normaldir.
// Her sekmeye tek bağlantı; son tıklamadan DEBUGGER_IDLE_MS sonra ayrılır.
const DEBUGGER_IDLE_MS = 4000;
const debuggerTimers = new Map(); // tabId → ayrılma zamanlayıcısı (kayıt varsa bağlı)

async function detachDebugger(tabId) {
    if (debuggerTimers.has(tabId)) clearTimeout(debuggerTimers.get(tabId));
    debuggerTimers.delete(tabId);
    await chrome.debugger.detach({ tabId }).catch(() => {});
}

async function ensureDebugger(tabId) {
    if (!debuggerTimers.has(tabId)) {
        try {
            await chrome.debugger.attach({ tabId }, "1.3");
        } catch (error) {
            // Servis çalışanı uyuyup uyanınca kayıt sıfırlanır ama bağlantı sürebilir.
            if (!/already attached/i.test(error.message || "")) throw error;
        }
    } else {
        clearTimeout(debuggerTimers.get(tabId));
    }
    debuggerTimers.set(tabId, setTimeout(() => detachDebugger(tabId), DEBUGGER_IDLE_MS));
}

chrome.debugger.onDetach.addListener((source) => {
    if (debuggerTimers.has(source.tabId)) {
        clearTimeout(debuggerTimers.get(source.tabId));
        debuggerTimers.delete(source.tabId);
    }
});

async function trustedClicks(tabId, points) {
    const target = { tabId };
    await ensureDebugger(tabId);
    for (const point of points) {
        for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
            await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
                type, x: point.x, y: point.y, button: type === "mouseMoved" ? "none" : "left", clickCount: 1,
            });
        }
        if (point.waitMs) await new Promise((resolve) => setTimeout(resolve, point.waitMs));
    }
    await ensureDebugger(tabId); // ayrılma zamanlayıcısını son tıklamadan itibaren yeniden başlat
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    (async () => {
        const senderSlot = sender.tab ? await slotOfTab(sender.tab.id) : null;
        if (request.action === "whoami") {
            const state = await getState();
            const venue = senderSlot !== null ? (await getSlot(senderSlot)).venue : null;
            const isBotTab = Boolean(state.isRunning && venue);
            sendResponse({ isBotTab, venue: isBotTab ? venue : null });
            return;
        }
        if (request.action === "start") {
            const workerCount = Math.min(MAX_WORKERS, Math.max(1, Number(request.workers) || DEFAULT_WORKERS));
            await chrome.storage.local.set({ pausedForCaptcha: false, workerCount, activeWorkers: workerCount, lastCaptchaAt: null });
            await setRunning(true);
            publishLog(`Tarama ${workerCount} paralel pencereyle başlıyor.`);
            for (let slot = 1; slot <= MAX_WORKERS; slot += 1) {
                if (slot > workerCount) await parkSlot(slot);
                else setTimeout(() => fetchNextAndNavigate(slot), (slot - 1) * 1500); // pencereleri aralıklı aç
            }
        } else if (request.action === "stop") {
            await setRunning(false);
            publishLog("Tarama durduruldu; kuyruk ilerlemesi korundu.");
        } else if (request.action === "save_result") {
            if (senderSlot !== null) await finishAndContinue(senderSlot, request.payload);
        } else if (request.action === "fail_current") {
            if (senderSlot !== null) await failCurrent(senderSlot, request.payload);
        } else if (request.action === "heartbeat") {
            const state = await getState();
            if (state.isRunning && !state.pausedForCaptcha && senderSlot !== null) await armWatchdog(senderSlot);
        } else if (request.action === "trusted_click") {
            if (senderSlot === null) throw new Error("Bot sekmesi değil");
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
