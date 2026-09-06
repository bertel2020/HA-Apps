// Werkzeugleiste einer Liste als ein Menü, auf schmalen Bildschirmen.
//
// Über der Entitätenliste stehen sechs Bedienelemente: Suche, "Nur
// Favoriten", Typ, Einheit, Spalten und (aus table-cards.js) Sortieren. Am
// Schreibtisch ist das eine Reihe, auf 375px sind es vier — gemessen 161px
// Bedienung über einer Liste, deren erste Karte dann bei 403px beginnt, also
// knapp unter der Hälfte des Schirms.
//
// Sie sind alle dasselbe: Einstellungen der Liste. Nur die Suche ist eine
// laufende Eingabe und bleibt deshalb sichtbar; der Rest zieht in ein Menü.
//
// Verschoben werden die vorhandenen Elemente selbst, und zwar INNERHALB von
// #controls. Damit bleiben Feldnamen, hx-include und die Auswertung im Server
// unberührt — es gibt keine zweite Fassung des Formulars, die auseinander
// laufen könnte. Wird der Bildschirm breit, wandern sie an ihren alten Platz
// zurück; ein Anker je Element merkt sich, wo der war.
(function () {
  'use strict';

  const SCHMAL = window.matchMedia('(max-width:640px)');
  const PREFIX = 'list-settings';
  // Darunter lohnt das Menü nicht: ein einzelnes Element ist im Menü schlechter
  // erreichbar als daneben.
  const MIN_ELEMENTE = 2;

  let menu = null;
  let popover = null;
  let button = null;
  const anker = new Map();

  // Zwei Bauformen von Werkzeugleiste gibt es in dieser App: #controls über
  // den serverseitig gefilterten Listen (Entitäten) und .card-browser über den
  // Kachel-Übersichten (Dashboards, Charts, Tabellen). Beide tragen dasselbe:
  // ein Suchfeld und daneben Einstellungen der Liste.
  function leiste() {
    return document.getElementById('controls') || document.querySelector('.card-browser');
  }

  // Alles außer der Suche und den verdeckten Feldern (Seitenzahl,
  // columns_submitted) — Letztere sind Formular-Zustand, keine Bedienung, und
  // dürfen ihren Platz behalten.
  function beweglich(bar) {
    return Array.from(bar.children).filter(el => {
      if (el === menu) return false;
      if (el.tagName === 'INPUT') return false;  // Suche und hidden
      // "Kein Dashboard passt zur Suche." gehört zur Liste, nicht zu ihren
      // Einstellungen — im Menü versteckt wäre die Meldung wertlos.
      if (el.classList.contains('card-browser-empty')) return false;
      return true;
    });
  }

  // Wie viele Filter vom Normalzustand abweichen. Drei Formen kommen in dieser
  // App vor: eine angehakte Filter-Pille ("Nur Favoriten"), ein
  // Einfachauswahl-Picker mit einem anderen Wert als "all" (Einheit) und ein
  // Mehrfachauswahl-Picker, bei dem "Alle" abgewählt ist (Typ). Die
  // Spaltenauswahl zählt bewusst nicht mit: sie filtert nichts weg.
  function aktiveFilter(bar) {
    let n = 0;
    bar.querySelectorAll('.filter-chip input:checked').forEach(() => { n += 1; });
    bar.querySelectorAll('.dd-picker-wrap input[type="hidden"]').forEach(feld => {
      if (feld.value && feld.value !== 'all') n += 1;
    });
    bar.querySelectorAll('.dd-picker-wrap input[type="checkbox"][value="all"]').forEach(feld => {
      if (!feld.checked) n += 1;
    });
    return n;
  }

  function beschriften() {
    const bar = leiste();
    if (!bar || !button) return;
    const n = aktiveFilter(bar);
    const text = n ? `Ansicht (${n}) ▾` : 'Ansicht ▾';
    // Nur schreiben, wenn sich etwas ändert: der MutationObserver unten
    // beobachtet childList, und ein neu gesetzter Textknoten wäre eine
    // Änderung — die Beschriftung würde sich selbst endlos neu auslösen.
    if (button.textContent !== text) button.textContent = text;
    button.classList.toggle('is-active', n > 0);
  }

  function aufbauen() {
    const bar = leiste();
    if (!bar || menu) return;
    // Ohne eigenes Suchfeld ist es keine Werkzeugleiste dieser Bauart, sondern
    // ein Container, der zufällig #controls heißt — auf der Bereinigungs-Seite
    // etwa steckt darin die Zeitraum-Leiste, also die Hauptbedienung der
    // Seite. Die gehört nicht in ein Menü.
    const suche = bar.querySelector(':scope > input[type="search"]');
    if (!suche) return;
    const teile = beweglich(bar);
    if (teile.length < MIN_ELEMENTE) return;

    menu = document.createElement('span');
    menu.className = 'dd-picker-wrap list-settings';
    button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn';
    button.id = `${PREFIX}-btn`;
    button.setAttribute('aria-expanded', 'false');
    popover = document.createElement('div');
    popover.className = 'dd-picker-popover list-settings-popover';
    popover.id = `${PREFIX}-popover`;
    menu.append(button, popover);

    // Direkt hinter die Suche, damit beide zusammen eine Reihe bilden.
    bar.insertBefore(menu, suche.nextSibling);

    teile.forEach((el, i) => {
      const platz = document.createComment(`list-settings-${i}`);
      el.parentNode.insertBefore(platz, el);
      anker.set(el, platz);
      popover.appendChild(el);
    });

    button.addEventListener('click', event => {
      event.stopPropagation();
      const oeffnen = !popover.classList.contains('open');
      popover.classList.toggle('open', oeffnen);
      button.setAttribute('aria-expanded', String(oeffnen));
    });
    // Ein Klick im Menü darf es nicht schließen — die Auswahl findet ja darin
    // statt. Das Schließen von außen erledigt dd-picker.js für jedes
    // .dd-picker-wrap, also auch für dieses.
    popover.addEventListener('click', event => event.stopPropagation());
    bar.addEventListener('change', beschriften);
    beschriften();
  }

  function abbauen() {
    if (!menu) return;
    anker.forEach((platz, el) => {
      if (platz.parentNode) platz.parentNode.insertBefore(el, platz);
      platz.remove();
    });
    anker.clear();
    menu.remove();
    menu = popover = button = null;
  }

  // Das Sortiermenü der Kartenform kommt nicht von hier: table-cards.js baut es
  // gleich an der richtigen Stelle auf, sobald dieses Menü existiert (siehe
  // sortHost() dort). Andernfalls hätten sich beide Skripte gegenseitig
  // aufgeschaukelt — dieses verschiebt es weg, jenes vermisst es und baut ein
  // neues.
  function pruefen() {
    if (SCHMAL.matches) {
      aufbauen();
      beschriften();
    } else {
      abbauen();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pruefen);
  else pruefen();
  SCHMAL.addEventListener('change', pruefen);
  // Zusätzlich am resize, für den Fall, dass ein Fenster über die Grenze
  // gezogen wird. pruefen() ist billig, wenn sich nichts ändert: beide Zweige
  // steigen sofort wieder aus.
  //
  // Ungeprüft geblieben: die Geräte-Emulation des Testbrowsers setzt die
  // Viewport-Größe von außen und löst dabei WEDER das change-Ereignis der
  // Media Query NOCH resize NOCH einen ResizeObserver aus (alle drei gemessen,
  // Zähler blieben auf 0). Der Aufbau beim Laden ist geprüft, das Umschalten
  // im laufenden Fenster nicht.
  window.addEventListener('resize', pruefen);
  // Anders als table-cards.js KEIN MutationObserver: die Werkzeugleiste steht
  // beim Laden da und wird von htmx nie ersetzt (nachgeladen wird nur die
  // Liste). Ein Beobachter auf dem ganzen Dokument liefe hier gegen den in
  // table-cards.js — dieses Skript verschiebt Elemente, das ist eine Änderung,
  // die den anderen Beobachter weckt, dessen Arbeit wiederum diesen hier. Die
  // Seite blieb dabei stehen.
  document.addEventListener('htmx:afterSwap', pruefen);
})();
