/* Klappt den Hinweis auf, der zum Info-Knopf gehört (siehe _hints.html).

   Delegation an document statt eines Listeners je Knopf: die Formulare, in
   denen die Knöpfe stehen — Entität-Konfiguration, die Einstellungs-Abschnitte
   — werden von htmx bei jeder Änderung komplett ausgetauscht, und die Dialoge
   des Energiedashboards rollt Alpine je Zeile neu aus. Angeheftete Listener
   wären danach weg, und der Fehler fiele erst beim zweiten Klick auf. */

/* `hint-warn` und `hint-status` sind ausdrücklich ausgenommen: eine Warnung
   und eine Statuszeile dürfen nicht wegklappen, auch nicht versehentlich. */
/* .settings-section-description kam mit dem Housekeeping dazu: der Satz
   zwischen Abschnittsüberschrift und Tabelle ist dieselbe Sorte Text wie ein
   .hint, nur eine Ebene höher — er erklärt, was der Abschnitt zeigt. Er behält
   seine eigene Klasse, weil er anders aussieht (--ink-muted, 13px statt
   --ink-faint, 12,5px). */
const HINWEIS =
  '.hint:not(.hint-warn):not(.hint-status),' +
  '.tbl-hint:not(.hint-warn):not(.hint-status),' +
  '.settings-section-description:not(.hint-warn):not(.hint-status),' +
  '.settings-compact-hint:not(.hint-warn):not(.hint-status)';

/* Zwei Formen, mehr gibt es nicht:

   1. Der Knopf steht im Label eines Formularfeldes — dann ist der Hinweis der
      aufklappbare Absatz in demselben `.field`. (Er steht dort HINTER dem
      Bedienelement, nicht direkt hinter dem Label, deshalb reicht hier kein
      Geschwister-Verhältnis.)
   2. Der Knopf steht in einer Überschrift — dann ist der Hinweis der Absatz
      direkt dahinter. */
function hinweisZu(knopf) {
  const feld = knopf.closest('.field');
  if (feld) return feld.querySelector(HINWEIS);

  const traeger = knopf.parentElement;
  const dahinter = traeger && traeger.nextElementSibling;
  return dahinter && dahinter.matches(HINWEIS) ? dahinter : null;
}

/* Ausgeklappt am Schreibtisch, eingeklappt auf dem Telefon.
   Bis 0.88.0 war der Zustand auf allen Breiten gleich (immer zu) — die
   Begründung dafür war „ein Text für alle Bildschirme". Der Platzdruck, der
   das Wegklappen überhaupt nötig macht, besteht aber nur auf schmalen
   Geräten: auf der Entität-Konfiguration waren es 557 von 2.806 px, am
   Schreibtisch fällt derselbe Text nicht ins Gewicht. Dieselbe Aufteilung wie
   bei den beiden anderen einklappbaren Blöcken der App (Protokollierungs-
   Karte in logs.js, Import-Anleitungen in pages/import.js), inklusive
   desselben Breakpoints.

   Der Knopf bleibt auf beiden Breiten bedienbar: am Schreibtisch klappt er
   einen Hinweis wieder zu, wenn er stört. Beim Wechsel schmal→breit fällt der
   selbst gewählte Zustand weg und der Standard der neuen Breite gilt — sonst
   käme ein auf dem Telefon aufgeklappter Hinweis am Schreibtisch zugeklappt
   wieder. */
const SCHMAL = window.matchMedia('(max-width:700px)');

function standardZustand() {
  document.querySelectorAll(HINWEIS).forEach(function (ziel) {
    ziel.hidden = SCHMAL.matches;
  });
  document.querySelectorAll('.hint-toggle').forEach(function (knopf) {
    const ziel = hinweisZu(knopf);
    if (ziel) knopf.setAttribute('aria-expanded', String(!ziel.hidden));
  });
}

SCHMAL.addEventListener('change', standardZustand);
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', standardZustand);
else standardZustand();
/* htmx tauscht die Formulare komplett aus — die frisch gerenderten Absätze
   kommen mit dem serverseitigen hidden und müssten sonst am Schreibtisch von
   Hand wieder aufgeklappt werden. */
document.addEventListener('htmx:afterSwap', standardZustand);

document.addEventListener('click', function (event) {
  const knopf = event.target.closest('.hint-toggle');
  if (!knopf) return;
  const ziel = hinweisZu(knopf);
  if (!ziel) return;
  const aufklappen = ziel.hidden;
  ziel.hidden = !aufklappen;
  knopf.setAttribute('aria-expanded', String(aufklappen));
});
