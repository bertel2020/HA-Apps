// Suche + Sortierung für die Kachel-Übersichten (Dashboards, Charts,
// Tabellen). Bewusst rein im Browser statt wie bei der Entitäten-Übersicht
// per htmx auf dem Server: diese drei Listen umfassen typischerweise ein paar
// Dutzend Einträge, stehen ohnehin vollständig im DOM und brauchen für
// Tippen/Umsortieren keinen Roundtrip.
//
// Erwartetes Markup (siehe dashboards.html/charts.html/tables.html):
//   <div class="card-browser" data-storage-key="…">   Suche, Sortierung, Favoriten
//   <div class="…-grid" data-card-grid>               Container
//     <div class="card …-card" data-name="…" data-created="…" data-favorite="0|1">
//     <a class="card …-grid-add">                     bleibt immer letzte
//
// "Favoriten zuerst" ist bewusst KEINE der Sortierungen, sondern ein eigener
// Schalter darüber: er zieht die Favoriten nach oben, INNERHALB der Favoriten
// und darunter gilt weiterhin die gewählte Sortierung. Beides ist dadurch frei
// kombinierbar (z. B. "Favoriten zuerst" + "Name A–Z").
//
// data-created ist bei Charts/Tabellen der Anlagezeitpunkt, bei Dashboards die
// position — die vergibt create_dashboard() als fortlaufenden Zähler und
// niemand ändert sie später (es gibt keine Umsortierfunktion für Dashboards),
// sie ist damit ebenfalls die Anlagereihenfolge.
(function () {
  const SORTIERER = {
    neueste: (a, b) => b.created - a.created,
    aelteste: (a, b) => a.created - b.created,
    name_asc: (a, b) => a.name.localeCompare(b.name, 'de'),
    name_desc: (a, b) => b.name.localeCompare(a.name, 'de'),
  };
  const STANDARD_SORTIERUNG = 'neueste';

  // Umlaute und ß mit abbilden, damit "Ubersicht"/"Uebersicht" die "Übersicht"
  // findet — toLowerCase() allein macht daraus nur "übersicht".
  function suchform(text) {
    return (text || '')
      .toLowerCase()
      .replace(/ä/g, 'a').replace(/ö/g, 'o').replace(/ü/g, 'u').replace(/ß/g, 'ss')
      .replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u');
  }

  function init(wurzel) {
    const grid = wurzel.querySelector('[data-card-grid]');
    const steuerung = wurzel.querySelector('.card-browser');
    if (!grid || !steuerung) return;

    const suchfeld = steuerung.querySelector('.card-browser-search');
    // Kein <select>, sondern der .dd-picker aus dd-picker.js (App-Optik statt
    // des vom Betriebssystem gezeichneten Auswahlfelds): sichtbarer Button +
    // verdecktes Feld, das wie ein <select> ein bubbelndes change-Event
    // schickt. Deshalb hier .value/change wie gehabt.
    const sortierung = steuerung.querySelector('.card-browser-sort');
    const sortierWrap = sortierung.closest('.dd-picker-wrap');
    const favSchalter = steuerung.querySelector('.card-browser-fav');
    const treffermeldung = steuerung.querySelector('.card-browser-empty');
    const addKachel = grid.querySelector('.card-browser-add');
    const speicherschluessel = steuerung.dataset.storageKey;
    const favSchluessel = speicherschluessel + '.fav';

    // Button-Beschriftung und aktive Zeile an den aktuellen Wert angleichen —
    // selectDDOption() macht das beim Klicken selbst, beim Wiederherstellen
    // aus localStorage muss es hier nachgeholt werden.
    function pickerSpiegeln() {
      const zeilen = [...sortierWrap.querySelectorAll('.dd-picker-row')];
      const aktiv = zeilen.find(z => z.dataset.value === sortierung.value) || zeilen[0];
      zeilen.forEach(z => z.classList.toggle('active', z === aktiv));
      const btn = sortierWrap.querySelector('.btn');
      if (btn && aktiv) btn.textContent = aktiv.textContent.trim() + ' ▾';
    }

    const karten = [...grid.querySelectorAll('[data-name]')].map(el => ({
      el,
      name: el.dataset.name || '',
      suchtext: suchform(el.dataset.name),
      created: Number(el.dataset.created || 0),
      favorit: el.dataset.favorite === '1' ? 1 : 0,
    }));

    // Zuletzt gewählte Sortierung und Favoriten-Schalter merken (nur diese
    // Ansicht, nur dieser Browser). Ein privates Fenster oder blockierte
    // Website-Daten lassen localStorage werfen — dann bleibt es bei den
    // Vorgaben (neueste zuerst, Favoriten oben).
    try {
      const gemerkt = localStorage.getItem(speicherschluessel);
      if (gemerkt && SORTIERER[gemerkt]) sortierung.value = gemerkt;
      // Nur ein ausdrückliches "0" schaltet ab: ohne gemerkten Wert bleibt es
      // bei der bisherigen Darstellung mit Favoriten oben.
      if (localStorage.getItem(favSchluessel) === '0') favSchalter.dataset.aktiv = '0';
    } catch (e) { /* ohne gemerkte Auswahl weiter */ }
    pickerSpiegeln();
    favSpiegeln();

    function favAktiv() {
      return favSchalter.dataset.aktiv !== '0';
    }

    function favSpiegeln() {
      favSchalter.classList.toggle('active', favAktiv());
      favSchalter.setAttribute('aria-pressed', String(favAktiv()));
    }

    function anwenden() {
      const suche = suchform(suchfeld.value.trim());
      const vergleich = SORTIERER[sortierung.value] || SORTIERER[STANDARD_SORTIERUNG];
      const sichtbar = karten.filter(k => !suche || k.suchtext.includes(suche));
      // Favoriten als vorgelagertes Kriterium, die gewählte Sortierung gilt
      // darunter unverändert weiter.
      sichtbar.sort((a, b) => (favAktiv() ? b.favorit - a.favorit : 0) || vergleich(a, b));

      karten.forEach(k => { k.el.hidden = true; });
      // appendChild verschiebt ein bereits vorhandenes Element, fügt es also
      // nicht doppelt ein — die Karten werden hier real umsortiert.
      sichtbar.forEach(k => { k.el.hidden = false; grid.appendChild(k.el); });
      if (addKachel) grid.appendChild(addKachel);

      if (treffermeldung) {
        treffermeldung.hidden = sichtbar.length > 0 || !suche;
      }
      // Die "+"-Kachel wäre bei einer Suche ohne Treffer die einzige übrige
      // Kachel und sähe wie ein Ergebnis aus.
      if (addKachel) addKachel.hidden = Boolean(suche) && sichtbar.length === 0;
    }

    suchfeld.addEventListener('input', anwenden);
    sortierung.addEventListener('change', () => {
      try {
        localStorage.setItem(speicherschluessel, sortierung.value);
      } catch (e) { /* Auswahl gilt dann nur für diesen Seitenaufruf */ }
      pickerSpiegeln();
      anwenden();
    });
    favSchalter.addEventListener('click', () => {
      favSchalter.dataset.aktiv = favAktiv() ? '0' : '1';
      try {
        localStorage.setItem(favSchluessel, favSchalter.dataset.aktiv);
      } catch (e) { /* Auswahl gilt dann nur für diesen Seitenaufruf */ }
      favSpiegeln();
      anwenden();
    });
    anwenden();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-card-browser-root]').forEach(init);
  });
})();
