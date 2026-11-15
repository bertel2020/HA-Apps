// Räume der weiteren Sensoren im Wetterdashboard (Roadmap 1.51): welche Räume die Vergleichsdiagramme kennen und welche
// Linien nach der Auswahl bleiben. Reine Funktionen über die Antwort von wetterdashboard/temperaturen: jede Reihe
// hat key, label, room und floor; die Außenwerte (key „temperature“, „humidity“) haben keinen Raum und bleiben immer.
(function (root) {
  const isExtra = (row) => String(row.key).startsWith('temperature:');

  // Namen nach einer gewählten Reihenfolge: die genannten zuerst, die übrigen danach in ihrer bisherigen Reihenfolge,
  // der leere Name ('' = ohne Etage / ohne Raum) bleibt zuletzt. Ohne Reihenfolge bleibt alles, wie es ist.
  function orderBy(names, order) {
    const wanted = Array.isArray(order) ? order : [];
    const rank = (name) => { const i = wanted.indexOf(name); return i < 0 ? wanted.length : i; };
    const named = names.filter(Boolean).map((name, index) => ({name, index})).sort((a, b) => rank(a.name) - rank(b.name) || a.index - b.index);
    return named.map((entry) => entry.name).concat(names.includes('') ? [''] : []);
  }

  // Räume in der Reihenfolge, in der sie zuerst vorkommen (Temperatur vor Feuchte), „Ohne Raum“ ('') zuletzt;
  // order ({floors, rooms}) legt eine eigene Reihenfolge fest.
  function roomsOf(cmp, order) {
    const names = [];
    for (const row of [...(cmp.series || []), ...(cmp.humidity || [])]) {
      if (!isExtra(row)) continue;
      const room = row.room || '';
      if (!names.includes(room)) names.push(room);
    }
    return orderBy(names, (order || {}).rooms);
  }

  // Etagen in der Reihenfolge des ersten Vorkommens, „ohne Etage“ ('') zuletzt, je Etage ihre Räume.
  function floorsOf(cmp, order) {
    const floors = new Map();
    for (const row of [...(cmp.series || []), ...(cmp.humidity || [])]) {
      if (!isExtra(row)) continue;
      const floor = row.floor || '';
      const room = row.room || '';
      if (!floors.has(floor)) floors.set(floor, []);
      if (!floors.get(floor).includes(room)) floors.get(floor).push(room);
    }
    const roomOrder = (order || {}).rooms;
    const sorted = (rooms) => orderBy(rooms, roomOrder);
    const named = orderBy([...floors.keys()], (order || {}).floors).map((floor) => ({floor, rooms: sorted(floors.get(floor))}));
    return named;
  }

  // Die Reihen, die nach der Auswahl bleiben; _i ist die Stelle in der ganzen Liste (die Farbe bleibt, wenn andere
  // Linien ausgeblendet werden). off: Namen der abgewählten Räume.
  function visible(list, off) {
    return list.map((row, index) => ({...row, _i: index})).filter((row) => !isExtra(row) || !off.includes(row.room || ''));
  }

  const api = {roomsOf, floorsOf, visible, orderBy};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WetterRooms = api;
})(typeof window !== 'undefined' ? window : globalThis);
