// Räume der weiteren Sensoren im Wetterdashboard (Roadmap 1.51): welche Räume die Vergleichsdiagramme kennen und welche
// Linien nach der Auswahl bleiben. Reine Funktionen über die Antwort von wetterdashboard/temperaturen: jede Reihe
// hat key, label, room und floor; die Außenwerte (key „temperature“, „humidity“) haben keinen Raum und bleiben, bis der
// Chip „Außen“ (OUTDOOR) sie abwählt.
(function (root) {
  const isExtra = (row) => String(row.key).startsWith('temperature:');
  // Name des Chips „Außen“ in der Liste der abgewählten Einträge; kein Raumname kann so beginnen („@“ ist kein Teil davon).
  const OUTDOOR = '@außen';

  // Räume in der Reihenfolge, in der sie zuerst vorkommen (Temperatur vor Feuchte), „Ohne Raum“ ('') zuletzt.
  function roomsOf(cmp) {
    const names = [];
    for (const row of [...(cmp.series || []), ...(cmp.humidity || [])]) {
      if (!isExtra(row)) continue;
      const room = row.room || '';
      if (!names.includes(room)) names.push(room);
    }
    return names.filter(Boolean).concat(names.includes('') ? [''] : []);
  }

  // Etagen in der Reihenfolge des ersten Vorkommens, „ohne Etage“ ('') zuletzt, je Etage ihre Räume.
  function floorsOf(cmp) {
    const floors = new Map();
    for (const row of [...(cmp.series || []), ...(cmp.humidity || [])]) {
      if (!isExtra(row)) continue;
      const floor = row.floor || '';
      const room = row.room || '';
      if (!floors.has(floor)) floors.set(floor, []);
      if (!floors.get(floor).includes(room)) floors.get(floor).push(room);
    }
    const named = [...floors.keys()].filter(Boolean).map((floor) => ({floor, rooms: floors.get(floor)}));
    return floors.has('') ? named.concat([{floor: '', rooms: floors.get('')}]) : named;
  }

  // Die Reihen, die nach der Auswahl bleiben; _i ist die Stelle in der ganzen Liste (die Farbe bleibt, wenn andere
  // Linien ausgeblendet werden). off: Namen der abgewählten Räume; mit OUTDOOR fehlen auch die Außenwerte.
  function visible(list, off) {
    return list.map((row, index) => ({...row, _i: index})).filter((row) => (isExtra(row) ? !off.includes(row.room || '') : !off.includes(OUTDOOR)));
  }

  const api = {roomsOf, floorsOf, visible, OUTDOOR};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterRooms = api;
})(typeof window !== 'undefined' ? window : globalThis);
