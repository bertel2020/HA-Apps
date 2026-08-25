# Changelog

## Unreleased

### Neu

- Der Symcon-Import liest Einheiten aus `settings.json`, zeigt sie in der
  Vorschau und vergleicht sie mit der Einheit der gewählten
  Home-Assistant-Entität.
- Bei abweichenden Einheiten kann ein Umrechnungsfaktor angegeben werden;
  bekannte Umrechnungen wie `klx` nach `lx` werden vorgeschlagen.

### Geändert

- Liniencharts stellen Messwerte als Stufen dar: Der letzte Wert bleibt bis
  zum nächsten Messpunkt gültig, auch über den Beginn und das Ende des
  sichtbaren Zeitfensters hinweg.

### Behoben

- Beim Import und bei der laufenden Übernahme werden vorhandene Messpunkte
  derselben Entität und desselben Zeitstempels unabhängig von ihrer Event-ID
  als Duplikat erkannt.
