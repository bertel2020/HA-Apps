/* Hält das linke Abzeichen an der Glocke und den Abschnitt "Läuft gerade" im
   Glocken-Menü aktuell (siehe _activity_block.html, .notice-badge.is-activity
   in app.css).

   Warum überhaupt: Seit Symcon-Vorschau, CSV-Import, Home-Assistant-Import und
   Bereinigung in Hintergrund-Threads laufen, überleben sie den Seitenwechsel.
   Die Kopfleiste ist das einzige Bauteil, das auf JEDER Seite steht — also der
   einzige Ort, an dem "es arbeitet gerade etwas" überhaupt stehen kann.

   Der Server liefert ein fertiges HTML-Fragment statt JSON, dasselbe Muster
   wie refreshNoticePanel() in _topnav.html: So gibt es das Markup nur einmal
   (in der Vorlage), und die serverseitig gerenderte erste Fassung und jede
   spätere Auffrischung sind zwangsläufig gleich.

   Das Blinken entsteht hier und nicht auf dem Server: Der Endpunkt meldet nur,
   WAS läuft. Verschwindet eine data-job-id aus der Antwort, ist der Vorgang
   fertig — das merkt jeder Tab für sich, ohne dass der Server einen
   Abschlusszustand vorhalten müsste und ohne Wettrennen zwischen zwei Tabs. */

(function () {
  'use strict';

  /* Zwei Takte statt eines festen: Solange etwas läuft, will man die Zahlen
     wandern sehen; solange nichts läuft, ist jede Anfrage eine zu viel. Der
     Endpunkt liest nur Felder im Arbeitsspeicher, aber er läuft auf jeder Seite
     und in jedem offenen Tab. */
  const TAKT_AKTIV_MS = 2000;
  const TAKT_RUHE_MS = 8000;

  /* Rund eine Sekunde: drei Blitze zu je 340 ms (siehe .is-changed in app.css).
     Danach muss die Klasse weg, sonst löst ein zweites Ereignis keine neue
     Animation mehr aus. */
  const BLINK_MS = 1100;

  let bekannteVorgaenge = null;
  let blinkTimer = null;
  let naechsterLauf = null;

  function glocke() {
    return document.querySelector('.notice-btn');
  }

  function kennungen(wurzel) {
    return Array.prototype.map.call(
      wurzel.querySelectorAll('.activity-item'),
      (zeile) => zeile.dataset.jobId,
    );
  }

  function blitze() {
    const knopf = glocke();
    if (!knopf) return;
    knopf.classList.remove('is-changed');
    /* Reflow erzwingen: Ohne das setzt der Browser dieselbe Animation nicht neu
       auf, wenn zwei Ereignisse kurz hintereinander kommen. */
    void knopf.offsetWidth;
    knopf.classList.add('is-changed');
    window.clearTimeout(blinkTimer);
    blinkTimer = window.setTimeout(function () {
      knopf.classList.remove('is-changed');
    }, BLINK_MS);
  }

  /* Das Abzeichen liegt AUSSERHALB des ausgetauschten Fragments (es sitzt im
     Knopf, nicht im Menü) — dieselbe Lage wie beim Meldungs-Badge, und deshalb
     hier dieselbe Behandlung: aus dem frisch eingesetzten Markup zählen, statt
     eine zweite Zählung vom Server zu holen. */
  function zeichneAbzeichen(anzahl) {
    const knopf = glocke();
    if (!knopf) return;
    const vorhanden = knopf.querySelector('.notice-badge.is-activity');
    if (anzahl === 0) {
      if (vorhanden) vorhanden.remove();
    } else {
      const abzeichen = vorhanden || knopf.insertBefore(
        document.createElement('span'), knopf.firstChild,
      );
      abzeichen.className = 'notice-badge is-activity';
      /* Ziffer erst ab zwei: Die meisten Vorgänge nehmen die globale
         Wartungssperre und können gar nicht gleichzeitig laufen — eine
         dauerhafte "1" wäre eine Ziffer ohne Information. */
      abzeichen.textContent = anzahl > 1 ? String(anzahl) : '';
    }
    const label = anzahl === 0
      ? 'Meldungen'
      : 'Meldungen — ' + anzahl + (anzahl === 1 ? ' Vorgang läuft' : ' Vorgänge laufen');
    knopf.setAttribute('aria-label', label);
  }

  function plane(verzoegerung) {
    window.clearTimeout(naechsterLauf);
    naechsterLauf = window.setTimeout(hole, verzoegerung);
  }

  async function hole() {
    const ziel = document.getElementById('notice-activity');
    if (!ziel) return;
    let aktiv = bekannteVorgaenge ? bekannteVorgaenge.length > 0 : false;
    try {
      const res = await fetch((ziel.dataset.appRoot || '') + '/notices/activity');
      if (res.ok) {
        ziel.innerHTML = await res.text();
        const jetzt = kennungen(ziel);
        aktiv = jetzt.length > 0;
        zeichneAbzeichen(jetzt.length);
        /* Beim allerersten Durchlauf NICHT blinken: Der Seitenaufbau hat den
           Stand bereits serverseitig gerendert: was hier ankommt, ist derselbe
           und keine Änderung. Sonst blinkte die Glocke bei jedem Seitenwechsel
           während eines langen Imports erneut. */
        if (bekannteVorgaenge !== null) {
          const geaendert =
            jetzt.length !== bekannteVorgaenge.length ||
            jetzt.some((id) => bekannteVorgaenge.indexOf(id) === -1);
          if (geaendert) blitze();
        }
        bekannteVorgaenge = jetzt;
      }
    } catch (fehler) {
      /* Netzwerkfehler: Anzeige bleibt auf dem letzten Stand, der Takt läuft
         weiter. Eine kaputte Kopfleiste wäre schlimmer als eine veraltete. */
    }
    plane(aktiv ? TAKT_AKTIV_MS : TAKT_RUHE_MS);
  }

  /* Der serverseitig gerenderte Stand ist die Ausgangslage — daraus die erste
     Liste ziehen, damit der allererste Abruf nicht als Änderung zählt. */
  (function start() {
    const ziel = document.getElementById('notice-activity');
    if (!ziel) return;
    bekannteVorgaenge = kennungen(ziel);
    plane(bekannteVorgaenge.length > 0 ? TAKT_AKTIV_MS : TAKT_RUHE_MS);
  })();
})();
