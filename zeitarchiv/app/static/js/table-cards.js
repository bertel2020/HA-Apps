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

    // Die Kopfzeile bleibt als Sortierleiste stehen, wenn sie eine Bedienung
    // trägt — sonst wäre Sortieren auf dem Telefon nicht mehr erreichbar.
    // Ohne Sortierung ist sie in der Kartenform überflüssig: die Spaltennamen
    // stehen dann an jedem Wert.
    const sortable = head.querySelector('a') || head.querySelector('.dt-sortable-col') ||
      head.classList.contains('dt-sortable-col') ||
      Array.from(head.cells).some(cell => cell.classList.contains('dt-sortable-col'));
    head.classList.add(sortable ? 'dt-cards-sortbar' : 'dt-cards-head-hidden');
    table.classList.toggle('dt-cards-titled', titleDone);
    table.classList.add('dt-cards');
  }

  function scan() {
    document.querySelectorAll('table.dt').forEach(enhance);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  else scan();
  // Gleiche Nachrüst-Punkte wie in resizable-tables.js — Listen und Vorschauen
  // werden per htmx nachgeladen.
  document.addEventListener('htmx:afterSwap', scan);
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
