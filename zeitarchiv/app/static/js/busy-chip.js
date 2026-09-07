/* Mitlaufende Uhr im "Läuft"-Chip (siehe .busy-chip in app.css).

   Auftauchen und Verschwinden des Chips macht htmx allein: hx-indicator am
   Button benennt den Chip, htmx setzt für die Dauer der Anfrage
   .htmx-request darauf, das CSS blendet ihn dadurch ein. Nur die Sekunden
   kann CSS nicht zählen — dafür ist diese Datei da, und für nichts sonst.

   Delegation an document statt eines Listeners je Chip: die Formulare, in
   denen die Chips stehen (Bereinigung, Retention, Speicher-Index, Import),
   werden von htmx nach jeder Antwort komplett ausgetauscht; angeheftete
   Listener wären danach weg.

   Der Zähler hängt aus demselben Grund nicht am Element, sondern in einer
   Map über dem Selektor: Nach einem Austausch ist der Chip-Knoten ein
   anderer, der laufende Intervall-Timer aber derselbe — ohne die Map schriebe
   er bis zum nächsten Seitenwechsel auf einen abgehängten Knoten weiter. */

/* Erst ab hier wird die Uhr eingeblendet. Darunter ist eine Sekundenanzeige
   keine Information, sondern Unruhe: der Chip selbst sagt bereits "läuft". */
const UHR_AB_MS = 3000;

const laufendeUhren = new Map();

/* Der Auslöser trägt hx-indicator in der Regel selbst; closest() deckt den
   Fall mit ab, dass es weiter oben am Container steht (htmx vererbt das
   Attribut ebenso). Nur .busy-chip zählt — ein hx-indicator, der auf etwas
   anderes zeigt (z. B. der Textchip der HA-Verfügbarkeit), geht uns nichts an. */
function chipZu(ausloeser) {
  if (!ausloeser || typeof ausloeser.closest !== 'function') return null;
  const traeger = ausloeser.closest('[hx-indicator]');
  if (!traeger) return null;
  const selektor = traeger.getAttribute('hx-indicator');
  if (!selektor) return null;
  let chip = null;
  try {
    chip = document.querySelector(selektor);
  } catch (fehler) {
    return null;
  }
  return chip && chip.classList.contains('busy-chip') ? { selektor: selektor, chip: chip } : null;
}

function zeitText(millisekunden) {
  const gesamt = Math.floor(millisekunden / 1000);
  const minuten = String(Math.floor(gesamt / 60)).padStart(2, '0');
  const sekunden = String(gesamt % 60).padStart(2, '0');
  return minuten + ':' + sekunden;
}

function stoppe(selektor) {
  const timer = laufendeUhren.get(selektor);
  if (timer === undefined) return;
  window.clearInterval(timer);
  laufendeUhren.delete(selektor);
}

function starte(selektor, chip) {
  stoppe(selektor);
  const anzeige = chip.querySelector('.busy-chip-elapsed');
  if (!anzeige) return;
  anzeige.hidden = true;
  anzeige.textContent = zeitText(0);
  const begonnen = Date.now();
  laufendeUhren.set(selektor, window.setInterval(function () {
    const vergangen = Date.now() - begonnen;
    /* Jedes Mal frisch suchen: Läuft parallel ein Polling auf denselben
       Bereich, kann der Chip zwischendurch ersetzt worden sein. */
    const aktuell = document.querySelector(selektor);
    const feld = aktuell && aktuell.querySelector('.busy-chip-elapsed');
    if (!feld) return;
    if (vergangen >= UHR_AB_MS) feld.hidden = false;
    feld.textContent = zeitText(vergangen);
  }, 250));
}

document.addEventListener('htmx:beforeRequest', function (event) {
  const treffer = chipZu(event.detail && event.detail.elt);
  if (treffer) starte(treffer.selektor, treffer.chip);
});

/* htmx nimmt .htmx-request in allen vier Fällen selbst wieder weg (der Chip
   ist dann schon unsichtbar) — hier bleibt nur, den Timer nicht weiterlaufen
   zu lassen. sendError/timeout/sendAbort sind ausdrücklich dabei: gerade eine
   abgebrochene lange Anfrage darf keine ewig tickende Uhr hinterlassen. */
['htmx:afterRequest', 'htmx:sendError', 'htmx:timeout', 'htmx:sendAbort'].forEach(function (name) {
  document.addEventListener(name, function (event) {
    const treffer = chipZu(event.detail && event.detail.elt);
    if (treffer) stoppe(treffer.selektor);
  });
});
