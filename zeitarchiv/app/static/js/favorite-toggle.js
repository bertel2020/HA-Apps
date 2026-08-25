// Favoriten-Stern (Konzept-Erweiterung) — von der Entitäten-/Charts-/
// Vergleichstabellen-Liste sowie der Entität-eigenen Chart- und
// Bearbeitungs-Seite gemeinsam genutzt (fünf Stellen, dieselbe Handvoll
// Zeilen an jeder — ein gemeinsames kleines Modul statt fünf Kopien, analog
// zu table-compute.js). url ist der jeweilige "/favorite"-Toggle-Endpunkt
// (main.py: entity_favorite_toggle()/charts_favorite_toggle()/
// tables_favorite_toggle(), alle mit identischer Antwortform
// {is_favorite: bool}), btn der geklickte Stern-Button selbst — dessen
// "active"-Klasse wird direkt aus der Server-Antwort gesetzt, nicht optimistisch
// vorweggenommen, damit ein fehlgeschlagener Request nie einen falschen
// Zustand anzeigt.
async function toggleFavorite(url, btn) {
  btn.disabled = true;
  try {
    const res = await fetch(url, {method: 'POST'});
    if (!res.ok) return;
    const data = await res.json();
    btn.classList.toggle('active', !!data.is_favorite);
    btn.setAttribute('aria-pressed', String(!!data.is_favorite));
    // bubbles: true, damit eine Seite mit sortierter/gefilterter Liste (z. B.
    // "Nur Favoriten") auf diesem Weg reagieren kann (neu laden/neu sortieren),
    // ohne dass toggleFavorite() selbst wissen muss, wie diese Seite aufgebaut ist.
    btn.dispatchEvent(new CustomEvent('favorite-changed', {bubbles: true, detail: data}));
  } catch (e) {
    // Netzwerkfehler: Stern bleibt im zuletzt bekannten (unveränderten) Zustand,
    // kein stiller falscher Erfolg.
  } finally {
    btn.disabled = false;
  }
}
