// Art des Niederschlags in der Wetterlage (Wetterdashboard): Regen, Schnee, Schneeregen, Hagel oder neutral.
// Reine Funktionen ohne Texte; die Sätze stehen in wetter.js, damit jedes deutsche Literal direkt in t(...) bleibt.
//
// Die Art kommt vom Server aus dem Wetterzustand (rain.kind, rain.last_kind). Fehlt sie, sagt ein reiner
// Ein/Aus-Sensor nicht, ob es regnet oder schneit: Bei Frost steht dann „Niederschlag“, sonst „Regen“.
(function (root) {
  // Bis zu dieser Außentemperatur (°C) gilt der neutrale Text. Bei 1 °C regnet es noch, bei −1 °C gibt es auch
  // gefrierenden Regen: die Schwelle rät deshalb nie „Schnee“.
  const COLD_MAX_C = 1;

  function isCold(temp) {
    return !!(temp && temp.unit === '°C' && temp.value != null && temp.value <= COLD_MAX_C);
  }

  // 'rain' | 'snow' | 'sleet' | 'hail' | 'precip' (neutral)
  function kindOf(rain, cold) {
    if (!rain) return null;
    const kind = rain.raining ? rain.kind : rain.last_kind;
    if (kind) return kind;
    return cold ? 'precip' : 'rain';
  }

  // Dauer des laufenden Niederschlags: {over} (länger als das Fenster), {n} (Minuten) oder {h, m} ab 120 Minuten.
  function sinceParts(minutes) {
    if (minutes == null) return {over: 48};
    const n = Math.round(minutes);
    if (n < 120) return {n};
    return {h: Math.floor(n / 60), m: String(n % 60).padStart(2, '0')};
  }

  const api = {COLD_MAX_C, isCold, kindOf, sinceParts};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterRain = api;
})(typeof window !== 'undefined' ? window : globalThis);
