/* Mitlaufende Uhr IM Knopf, für Anfragen, die lange genug dauern.

   Vorgeschichte: Bis 0.85.0 stand diese Uhr in einem eigenen Chip neben dem
   Knopf. Der Chip ist wieder weg — er sagte im Kern dasselbe wie der Knopf,
   hielt daneben dauerhaft Platz frei, und weil er per hx-indicator benannt
   war, wanderte htmx' .htmx-request-Klasse an ihn statt an den Knopf, der
   dadurch gar keinen Laufzustand mehr zeigte. Übrig bleibt der eine Teil,
   den der Knopf allein nicht kann: die Sekunden zählen. Deshalb hier, und
   für nichts sonst.

   Ohne Auszeichnung in den Vorlagen: Der Auslöser einer htmx-Anfrage ist
   entweder ein .btn oder nicht. Damit gibt es keine Liste von Knöpfen, die
   jemand beim Anlegen eines neuen vergessen könnte — und keine zweite
   Stelle, an der ein Tippfehler die Anzeige lautlos abschaltet (genau das
   war beim Chip über seinen hx-indicator-Selektor möglich).

   Selbstbegrenzend statt konfiguriert: Die Uhr erscheint erst nach
   UHR_AB_MS. Kurze Anfragen erreichen das nie, also braucht es keine
   Unterscheidung zwischen "langsamen" und "schnellen" Knöpfen. Darunter wäre
   eine Sekundenanzeige ohnehin keine Information, sondern Unruhe: Dass etwas
   läuft, sagt der Knopf schon durch Dimmung und Ring.

   Delegation an document statt eines Listeners je Knopf: Die Formulare, in
   denen diese Knöpfe stehen, werden von htmx nach jeder Antwort komplett
   ausgetauscht; angeheftete Listener wären danach weg. */

const UHR_AB_MS = 3000;

/* Schlüssel ist der Knopf-Knoten selbst. Nach einem Austausch ist der Knopf
   im Dokument ein anderer — der abgehängte alte ist aber genau der, mit dem
   htmx auch htmx:afterRequest meldet, sodass der Timer sicher wieder
   gefunden wird. */
const laufendeUhren = new Map();

/* .navbtn ist ausgenommen: 28x28 mit fester Größe (dieselbe Ausnahme wie
   beim Ring in app.css) — eine Uhr darin würde das Glyph hinausdrücken. */
function knopfZu(ausloeser) {
  if (!ausloeser || !ausloeser.classList) return null;
  if (!ausloeser.classList.contains('btn')) return null;
  if (ausloeser.classList.contains('navbtn')) return null;
  return ausloeser;
}

function zeitText(millisekunden) {
  const gesamt = Math.floor(millisekunden / 1000);
  const minuten = String(Math.floor(gesamt / 60)).padStart(2, '0');
  const sekunden = String(gesamt % 60).padStart(2, '0');
  return minuten + ':' + sekunden;
}

function stoppe(knopf) {
  const timer = laufendeUhren.get(knopf);
  if (timer === undefined) return;
  window.clearInterval(timer);
  laufendeUhren.delete(knopf);
  const anzeige = knopf.querySelector('.btn-elapsed');
  if (anzeige) anzeige.remove();
}

function starte(knopf) {
  stoppe(knopf);
  const begonnen = Date.now();
  laufendeUhren.set(knopf, window.setInterval(function () {
    const vergangen = Date.now() - begonnen;
    /* Reißleine, falls eines der Endereignisse ausbleibt (abgebrochene
       Navigation, ausgetauschter Knopf ohne afterRequest): Ein Timer, der
       auf einen abgehängten Knoten schreibt, liefe sonst bis zum nächsten
       Seitenwechsel weiter. */
    if (!knopf.isConnected) {
      stoppe(knopf);
      return;
    }
    if (vergangen < UHR_AB_MS) return;
    let anzeige = knopf.querySelector('.btn-elapsed');
    if (!anzeige) {
      anzeige = document.createElement('span');
      anzeige.className = 'btn-elapsed';
      /* aria-hidden: Der Wert ändert sich im Sekundentakt. Vorgelesen wäre
         das eine Dauerunterbrechung — dass der Knopf beschäftigt ist, sagt
         Screenreadern bereits sein disabled-Zustand. */
      anzeige.setAttribute('aria-hidden', 'true');
      knopf.appendChild(anzeige);
    }
    anzeige.textContent = zeitText(vergangen);
  }, 250));
}

document.addEventListener('htmx:beforeRequest', function (event) {
  const knopf = knopfZu(event.detail && event.detail.elt);
  if (knopf) starte(knopf);
});

/* sendError/timeout/sendAbort sind ausdrücklich dabei: Gerade eine
   abgebrochene lange Anfrage darf keine ewig tickende Uhr hinterlassen. */
['htmx:afterRequest', 'htmx:sendError', 'htmx:timeout', 'htmx:sendAbort'].forEach(function (name) {
  document.addEventListener(name, function (event) {
    const knopf = knopfZu(event.detail && event.detail.elt);
    if (knopf) stoppe(knopf);
  });
});
