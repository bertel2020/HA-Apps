/* Klappt den Hinweis auf, der zum Info-Knopf gehört (siehe _hints.html).

   Delegation an document statt eines Listeners je Knopf: die Formulare, in
   denen die Knöpfe stehen — Entität-Konfiguration, die Einstellungs-Abschnitte
   — werden von htmx bei jeder Änderung komplett ausgetauscht, und die Dialoge
   des Energiedashboards rollt Alpine je Zeile neu aus. Angeheftete Listener
   wären danach weg, und der Fehler fiele erst beim zweiten Klick auf. */

/* `hint-warn` und `hint-status` sind ausdrücklich ausgenommen: eine Warnung
   und eine Statuszeile dürfen nicht wegklappen, auch nicht versehentlich. */
const HINWEIS =
  '.hint:not(.hint-warn):not(.hint-status),' +
  '.tbl-hint:not(.hint-warn):not(.hint-status),' +
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

document.addEventListener('click', function (event) {
  const knopf = event.target.closest('.hint-toggle');
  if (!knopf) return;
  const ziel = hinweisZu(knopf);
  if (!ziel) return;
  const aufklappen = ziel.hidden;
  ziel.hidden = !aufklappen;
  knopf.setAttribute('aria-expanded', String(aufklappen));
});
