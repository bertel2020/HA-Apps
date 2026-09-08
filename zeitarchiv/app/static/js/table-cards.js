// Zeilenkarten für schmale Bildschirme (ZG-02 Stufe 2).
//
// Unter 640px wird aus jeder Tabellenzeile eine Karte, in der jeder Wert seine
// Spaltenüberschrift als Etikett trägt. Nötig ist das, weil von einer 1.028px
// breiten Tabelle in 356px sichtbarer Breite nur die Namensspalte übrig
// bleibt — die übrigen Werte sind zwar da, aber ohne Bezug zu ihrer Spalte
// nicht zuzuordnen.
//
// Die Etiketten kommen von hier (data-label je Zelle), die Darstellung aus
// app.css. Zwei Teile, weil nur das CSS weiß, ab welcher Breite Karten gelten,
// und nur JS an die Kopfzeile herankommt.
(function () {
  'use strict';

  // Unter drei Spalten bringt die Kartenform nichts — zwei Spalten passen auch
  // quer auf jedes Telefon.
  const MIN_COLUMNS = 3;

  // Die App setzt ihre Kopfzeile nicht überall in ein <thead> (Entitätenliste,
  // Export, Backup und Import haben eine reine <th>-Zeile am Tabellenanfang).
  // Beides wird hier akzeptiert; mehrstufige Köpfe dagegen nicht, denn dann
  // gäbe es je Wert zwei konkurrierende Etiketten.
  function headerRow(table) {
    const head = table.tHead;
    if (head) return head.rows.length === 1 ? head.rows[0] : null;
    const first = table.rows[0];
    if (!first || !first.cells.length) return null;
    return Array.from(first.cells).every(cell => cell.tagName === 'TH') ? first : null;
  }

  function labelOf(th) {
    // Sortierpfeile gehören zur Bedienung, nicht zum Spaltennamen.
    return th.textContent.replace(/[↑↓]/g, '').replace(/\s+/g, ' ').trim();
  }

  function enhance(table) {
    if (table.dataset.cards === 'off') return;
    // Chart-Legenden sind keine Listen, sondern eine Beschriftung neben dem
    // Diagramm — als Karten gestapelt wären sie länger als das Diagramm selbst.
    // Namentlich ausgenommen statt per data-Attribut, weil sie aus drei Quellen
    // kommen (chart_editor.html, entity_detail.html, dashboard-tiles.js).
    if (table.classList.contains('chart-legend-table')) return;
    // colspan heißt in dieser App: Trennzeile oder Summenzeile, also eine
    // Tabelle, deren Raster selbst die Aussage ist (Vergleichstabellen).
    // Die als Karten zu zerlegen würde sie zerstören.
    if (table.querySelector('[colspan],[rowspan]')) return;

    const head = headerRow(table);
    if (!head || head.cells.length < MIN_COLUMNS) return;

    const labels = Array.from(head.cells, labelOf);
    // Spalten ohne Überschrift (Favoriten-Stern, Aktionsknöpfe) bekommen in der
    // Sortierleiste keine leere Schaltfläche.
    labels.forEach((text, index) => {
      if (text === '') head.cells[index].classList.add('dt-cards-label-empty');
    });
    let titleDone = false;
    for (const row of table.rows) {
      if (row === head) continue;
      if (row.cells.length !== labels.length) return;  // unerwartete Struktur
      Array.from(row.cells).forEach((cell, index) => {
        cell.dataset.label = labels[index];
      });
    }
    // Erste Spalte mit echtem Namen wird zur Kartenüberschrift; davorliegende
    // namenlose Spalten (Favoriten-Stern, Auswahlkästchen) bleiben schlicht.
    const titleIndex = labels.findIndex(text => text !== '');
    if (titleIndex >= 0) {
      for (const row of table.rows) {
        if (row === head || !row.cells[titleIndex]) continue;
        row.cells[titleIndex].classList.add('dt-card-title');
      }
      titleDone = true;
    }

    if (titleIndex >= 0) addCollapsing(table, head, titleIndex, labels);

    // Die Kopfzeile bleibt als Sortierleiste stehen, wenn sie eine Bedienung
    // trägt — sonst wäre Sortieren auf dem Telefon nicht mehr erreichbar.
    // Ohne Sortierung ist sie in der Kartenform überflüssig: die Spaltennamen
    // stehen dann an jedem Wert.
    const sortable = head.querySelector('a') || head.querySelector('.dt-sortable-col') ||
      head.classList.contains('dt-sortable-col') ||
      Array.from(head.cells).some(cell => cell.classList.contains('dt-sortable-col'));
    // Die Kopfzeile verschwindet in der Kartenform in jedem Fall — die
    // Spaltennamen stehen als Etikett an jedem Wert. Trägt sie eine
    // Sortierung, wandert die als Menü über die Tabelle.
    head.classList.add('dt-cards-head-hidden');
    if (sortable) addSortMenu(head, table);
    table.classList.toggle('dt-cards-titled', titleDone);
    table.classList.add('dt-cards');
  }

  // Eine Karte trägt so viele Zeilen, wie die Tabelle Spalten hat — in der
  // Entitätenliste acht, auf der Housekeeping-Seite fünf. Gemessen sind das
  // 289px bzw. 207px je Karte: keine drei passen auf eine Schirmfüllung, und
  // wer eine Zeile SUCHT, liest davon den Namen. Eingeklappt bleiben deshalb
  // die Überschrift und ein Leitwert stehen, der Rest kommt auf Antippen.
  //
  // Leitwert ist die erste Spalte nach der Namensspalte. Das ist keine
  // Verlegenheitswahl: die Listen dieser App stellen die wichtigste Angabe
  // ohnehin nach vorn (Housekeeping "Letzter Wert", Statistik "Entitäten"),
  // und eine Regel je Seite wäre genau die Inkonsistenz, die vermieden werden
  // soll. Spalten VOR dem Namen (Favoriten-Stern, Auswahlkästchen) bleiben
  // sichtbar — sie sind Bedienung, kein Wert.
  const MIN_HIDDEN = 2;  // darunter lohnt der zusätzliche Griff nicht

  function addCollapsing(table, head, titleIndex, labels) {
    // Erste beschriftete Spalte nach dem Namen. Ohne sie (zwei Spalten, davon
    // eine namenlos) bliebe eingeklappt nur die Überschrift — dann lieber gar
    // nicht einklappen.
    //
    // data-card-lead nennt stattdessen eine Spalte beim Namen. Gedacht für die
    // Fälle, in denen die erste Spalte nach dem Namen eine Einordnung ist und
    // kein Wert: in der Entitätenliste steht dort "Typ", gesucht wird auf dem
    // Telefon aber nach dem letzten Wert. Passt der Name auf keine Spalte,
    // gilt wieder die erste — ein Tippfehler im Template darf das Einklappen
    // nicht abschalten.
    const gewuenscht = table.dataset.cardLead;
    const benannt = gewuenscht ? labels.findIndex((text, i) => i > titleIndex && text === gewuenscht) : -1;
    const leadIndex = benannt >= 0 ? benannt : labels.findIndex((text, i) => i > titleIndex && text !== '');
    if (leadIndex < 0) return;
    const versteckt = labels.length - titleIndex - 2;  // Name und Leitwert bleiben
    if (versteckt < MIN_HIDDEN) return;

    for (const row of table.rows) {
      // Summenzeilen tragen keine Details, die sich verstecken ließen.
      if (row === head || row.parentElement.tagName === 'TFOOT') continue;
      if (row.classList.contains('dt-card-collapsible')) continue;
      const title = row.cells[titleIndex];
      if (!title) continue;
      Array.from(row.cells).forEach((cell, index) => {
        if (index < titleIndex) cell.classList.add('dt-card-pre');
      });
      row.cells[leadIndex].classList.add('dt-card-lead');
      row.classList.add('dt-card-collapsible');
      // VOR den Titeltext, nicht dahinter: ein Float sitzt auf der Höhe der
      // Zeile, in der er im Fluss steht — angehängt landete der Pfeil unten
      // neben der Entity-ID statt oben neben dem Namen.
      title.insertBefore(toggleButton(row), title.firstChild);
    }
  }

  function toggleButton(row) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'dt-card-toggle';
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', 'Weitere Werte anzeigen');
    button.textContent = '▾';
    button.addEventListener('click', event => {
      // Die Überschrift ist auf manchen Seiten selbst ein Link (Entitätenliste)
      // — ohne das hier folgte der Klick auf den Pfeil ihm mit.
      event.preventDefault();
      event.stopPropagation();
      const offen = row.classList.toggle('is-open');
      button.setAttribute('aria-expanded', String(offen));
      button.setAttribute('aria-label', offen ? 'Weitere Werte verbergen' : 'Weitere Werte anzeigen');
    });
    return button;
  }

  // Sortieren gehört auf dem Telefon in dieselbe Reihe wie "Typ", "Einheit" und
  // "Spalten": es ist eine Einstellung der Liste, keine Tabellenkopfzeile. Als
  // Chip-Leiste standen hier je nach Tabelle vier bis sieben Bedienelemente
  // über der ersten Karte — mehr Höhe, als die Karte selbst braucht.
  //
  // Das Menü übernimmt die vorhandene Optik der Filter-Dropdowns
  // (.dd-picker-wrap/.dd-picker-popover aus app.css), steuert sein Auf und Zu
  // aber selbst: dd-picker.js ist nicht auf allen Seiten geladen, die
  // Listentabellen haben.
  function activeCell(head) {
    return head.querySelector('.dt-sort-asc, .dt-sort-desc') ||
      (head.querySelector('a.active') || {}).closest?.('th') || null;
  }

  // Serverseitig sortierte Köpfe tragen den Pfeil im Text ("Entität ↓"),
  // clientseitige die Klasse. Beides führt auf dasselbe Zeichen.
  function directionOf(cell) {
    if (!cell) return '';
    if (cell.classList.contains('dt-sort-asc')) return '↑';
    if (cell.classList.contains('dt-sort-desc')) return '↓';
    const arrow = cell.textContent.match(/[↑↓]/);
    return arrow ? arrow[0] : '';
  }

  function setMenuLabel(button, head) {
    const cell = activeCell(head);
    const name = cell ? labelOf(cell) : '';
    button.textContent = name ? `Sortieren: ${name} ${directionOf(cell)}`.trim() : 'Sortieren ▾';
    button.classList.toggle('dt-cards-sort-active', Boolean(name));
  }

  // Ein Klick im Menü löst genau das aus, was ein Klick auf den Spaltenkopf
  // auslösen würde — der Sortiermechanismus der Seite bleibt unangetastet.
  // Serverseitig ist das ein Link (der bei erneutem Aufruf die Richtung
  // dreht), clientseitig der Klick auf das <th> selbst.
  function triggerSort(cell) {
    const link = cell.querySelector('a[href]');
    if (!link) {
      cell.click();  // clientseitig sortierte Köpfe (sortable-table.js)
      return;
    }
    // Die serverseitigen Sortierlinks laden per htmx nach. Ein synthetisches
    // link.click() erreicht htmx nicht — dessen Auslöser hängt an einem eigenen
    // Ereignisweg. htmx.trigger() ist der dafür vorgesehene Weg; ohne htmx
    // (oder ohne hx-get am Link) bleibt der gewöhnliche Klick.
    if (link.hasAttribute('hx-get') && typeof window.htmx !== 'undefined') {
      window.htmx.trigger(link, 'click');
      return;
    }
    link.click();
  }

  // Wohin das Sortiermenü gehört: normalerweise über die Tabelle. Gibt es auf
  // der Seite das Ansicht-Menü (list-settings-menu.js), zieht es dort ein —
  // Sortieren ist eine Einstellung der Liste wie Filter und Spalten, und auf
  // dem Telefon soll es genau eine Stelle dafür geben.
  //
  // Nur bei genau EINER Listentabelle auf der Seite: bei mehreren wäre im
  // Ansicht-Menü nicht mehr erkennbar, welche Tabelle gemeint ist.
  function sortHost(table) {
    // Eine Seite kann für eine einzelne Liste einen eigenen Platz vorgeben:
    // auf Housekeeping steht in „Inaktive Entitäten“ schon eine Auswahl
    // („seit 3 Tage“) über der Tabelle, und Sortieren gehört in dieselbe
    // Zeile statt in eine zweite darunter. Das Ziel liegt bewusst AUSSERHALB
    // des per htmx getauschten Bereichs — dort überlebt es den Austausch,
    // während das Menü selbst neu gebaut wird (siehe verwaisteSortmenues).
    const abschnitt = table.closest('section');
    const eigener = abschnitt && abschnitt.querySelector('[data-sort-host]');
    if (eigener) return eigener;
    const menu = document.getElementById('list-settings-popover');
    if (menu && document.querySelectorAll('table.dt').length === 1) return menu;
    return table.closest('.tbl-wrap') || table.parentElement;
  }

  // Ein Menü im Ansicht-Menü überlebt den htmx-Austausch der Liste, seine
  // Zeilen zeigen danach aber auf Spaltenköpfe, die nicht mehr im Dokument
  // stehen — ein Klick sortierte dann ins Leere. Deshalb vor jedem Durchlauf
  // die Menüs wegräumen, deren Kopfzeile weg ist; sie werden gleich neu
  // gebaut.
  function verwaisteSortmenues() {
    document.querySelectorAll('.dt-cards-sort').forEach(menu => {
      if (!menu._head || !menu._head.isConnected) menu.remove();
    });
  }

  function addSortMenu(head, table) {
    const host = sortHost(table);
    if (!host || host.querySelector(':scope > .dt-cards-sort')) return;

    const menu = document.createElement('div');
    menu.className = 'dd-picker-wrap dt-cards-sort';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn';
    const popover = document.createElement('div');
    popover.className = 'dd-picker-popover dt-cards-sort-popover';

    Array.from(head.cells).forEach(cell => {
      const name = labelOf(cell);
      if (!name) return;  // Favoriten-Stern und Aktionsspalten
      const row = document.createElement('div');
      row.className = 'dd-picker-row';
      row.textContent = name;
      const arrow = document.createElement('span');
      arrow.className = 'dt-cards-sort-dir';
      row.appendChild(arrow);
      row.addEventListener('click', () => {
        popover.classList.remove('open');
        triggerSort(cell);
        // Serverseitig sortierte Tabellen werden nachgeladen und das Menü dabei
        // neu gebaut. Clientseitig (sortable-table.js) passiert das nicht — dort
        // muss die Beschriftung von Hand nachziehen, sonst zeigt der Knopf noch
        // die vorige Sortierung.
        setTimeout(mark, 0);
      });
      row.dataset.column = name;
      popover.appendChild(row);
    });
    if (!popover.children.length) return;

    function mark() {
      const current = activeCell(head);
      const name = current ? labelOf(current) : null;
      popover.querySelectorAll('.dd-picker-row').forEach(row => {
        const on = row.dataset.column === name;
        row.classList.toggle('active', on);
        row.querySelector('.dt-cards-sort-dir').textContent = on ? directionOf(current) : '';
      });
      setMenuLabel(button, head);
    }

    button.addEventListener('click', event => {
      event.stopPropagation();
      const opening = !popover.classList.contains('open');
      if (opening) mark();
      popover.classList.toggle('open', opening);
      button.setAttribute('aria-expanded', String(opening));
    });
    popover.addEventListener('click', event => event.stopPropagation());
    document.addEventListener('click', () => popover.classList.remove('open'));

    menu.append(button, popover);
    menu._head = head;
    host.insertBefore(menu, host.firstChild);
    mark();
  }

  // Der Scroll-Hinweis an den Rändern von .tbl-wrap (app.css) soll nur da
  // stehen, wo tatsächlich etwas außerhalb liegt. Rein in CSS geht das nicht:
  // ob ein Container überläuft, weiß nur das Layout.
  function scrollHinweis() {
    document.querySelectorAll('.tbl-wrap').forEach(wrap => {
      // +1px Toleranz: Bruchteile aus Zoom und Skalierung machen sonst aus
      // einer bündig passenden Tabelle eine scrollende.
      wrap.classList.toggle('is-scrollable', wrap.scrollWidth > wrap.clientWidth + 1);
    });
  }

  function scan() {
    verwaisteSortmenues();
    document.querySelectorAll('table.dt').forEach(enhance);
    scrollHinweis();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
  // Gleiche Nachrüst-Punkte wie in resizable-tables.js — Listen und Vorschauen
  // werden per htmx nachgeladen.
  document.addEventListener('htmx:afterSwap', scan);
  // Ob eine Tabelle überläuft, hängt an der Fensterbreite — und daran, ob eine
  // Spalte gerade breiter gezogen wurde (resizable-tables.js).
  window.addEventListener('resize', scrollHinweis);
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
