const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../collector/extension_bot/parsers.js"), "utf8");
const sandbox = { module: { exports: {} }, exports: {}, globalThis: {} };
vm.runInNewContext(source, sandbox);
const parsers = sandbox.module.exports;

assert.equal(parsers.parseTurkishPrice("1.350 TL"), 1350);
assert.equal(parsers.parseTurkishPrice("1.350,50 ₺"), 1350.5);
assert.equal(parsers.parseTurkishPrice("850 TL"), 850);
assert.equal(parsers.parseTurkishPrice("12,50 TL"), 12.5);

const provider = parsers.detectProvider("Menü\nSağlayan: Yemeksepeti\nKebap 350 TL");
assert.equal(provider.provider, "Yemeksepeti");

const items = parsers.extractMenuItems("Kebap\n350 TL\nAyran 1.350 TL", provider);
assert.equal(
    JSON.stringify(items.map((item) => [item.urun, item.fiyat])),
    JSON.stringify([["Kebap", 350], ["Ayran", 1350]])
);
assert.equal(parsers.parseReviewCount("1.774 kullanıcı yorumu"), 1774);
// Bilgi panelinde puan ile yorum sayısı birleşince ("3,8" + "5.413 Yorum") 85.413 okunmamalı
assert.equal(parsers.parseReviewCount("3,85.413 Yorum"), null);
assert.equal(parsers.parseReviewCount("5.413 Yorum"), 5413);
assert.equal(parsers.parseReviewCount("(39) kullanıcı yorumu"), 39);
// Panel seçimi: "tl" harfleri sıradan kelimelerde geçse de fiyat sayılmaz
assert.equal(parsers.countPriceMentions("Yanıtlarınız Google Arama deneyimini iyileştirir"), 0);
assert.equal(parsers.countPriceMentions("Köfte Bun\n₺815,00\nBizim Köfte\n₺1.050,00\nAyran 45 TL"), 3);

console.log("extension parser tests: ok");
