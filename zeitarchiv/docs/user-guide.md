# Benutzerhandbuch

Dieses Dokument ist die ausführliche Anleitung für Nutzer der App — Schritt
für Schritt, aufgabenorientiert, jede Seite im Detail. Für einen kurzen
Überblick (was die App ist, Kernfunktionen auf einen Blick) siehe die
[App-README](../README.md); technische Interna für Entwickler stehen in den
übrigen Dokumenten dieses Ordners (siehe [README.md](README.md)).

## Inhalt

- [Erste Schritte](#erste-schritte)
- [Die Übersichtsseite](#die-übersichtsseite)
- [Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)
- [Erklärungen zu einem Feld](#erklärungen-zu-einem-feld)
- [Auf dem Telefon](#auf-dem-telefon)
- [Dashboards](#dashboards)
- [Entitäten und Verläufe](#entitäten-und-verläufe)
- [Entität konfigurieren](#entität-konfigurieren)
- [Bereinigung](#bereinigung)
- [Datenhandling](#datenhandling)
- [Charts](#charts)
- [Vergleichstabellen](#vergleichstabellen)
- [Energiedashboard](#energiedashboard)
- [Statistik](#statistik)
- [Housekeeping](#housekeeping)
- [Import und Export](#import-und-export)
- [Backup / Restore](#backup--restore)
- [Einstellungen im Detail](#einstellungen-im-detail)
- [Typische Aufgaben](#typische-aufgaben)
- [Häufige Fragen](#häufige-fragen)

## Erste Schritte

1. **App installieren** — über den Add-on-Store (Repository
   `https://github.com/bertel2020/HA-Apps` hinzufügen) oder manuell. Details:
   [App-README → Installation](../README.md#installation).
2. **API-Token kopieren** — Zeitarchiv über die Home-Assistant-Seitenleiste
   öffnen, **Einstellungen → Verbindung**, Token kopieren. Der Token wird
   beim ersten Start automatisch erzeugt und ist nur für diese Zeitarchiv-
   Installation gültig.
3. **Integration installieren** — [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv),
   über HACS oder manuell. In Home Assistant unter **Einstellungen → Geräte
   & Dienste → Integration hinzufügen → Zeitarchiv** Host (`localhost`),
   Port (`8127`) und Token eintragen.
4. **Archivfilter festlegen** — auf der Integrationskachel **Konfigurieren →
   Archivfilter bearbeiten**: Domains, einzelne Entitäten, Bereiche oder
   Geräte auswählen. Ohne Filter kommen keine Daten an; die App wartet dann
   untätig, ohne Fehler anzuzeigen.
5. Nach dem ersten empfangenen Wert erscheint die Entität automatisch in
   **Entitäten** — mit den globalen Standardwerten aus **Einstellungen →
   Archivierung**. Diese Standards lassen sich pro Entität jederzeit
   individuell überschreiben (siehe [Entität konfigurieren](#entität-konfigurieren)).
6. Filter, Token oder Standards lassen sich jederzeit nachträglich ändern —
   bereits archivierte Werte bleiben davon unberührt, nur künftige Werte
   folgen den neuen Einstellungen.

**Woran erkenne ich, dass Daten ankommen?** Unter **Einstellungen →
Verbindung** zeigt "Letzter empfangener Wert" den Zeitpunkt des zuletzt
verarbeiteten Schreibvorgangs. Bleibt dieser Wert dauerhaft leer oder alt,
liegt es entweder an fehlenden Archivfiltern (Schritt 4) oder an einem
falschen Token/Host in der Integration (Schritt 3).

## Die Übersichtsseite

Die Übersicht (im Menü über das Haus-Symbol erreichbar, `/uebersicht`)
zeigt oben eine Kennzahlenübersicht (Anzahl Entitäten, Datensätze,
Speicherbedarf) und darunter das **Standard-Dashboard** — dieselbe
Kachel-Ansicht wie unter **Dashboards**, nur fest der Übersicht zugeordnet
und nicht umbenennbar oder löschbar. Es lässt sich wie jedes andere
Dashboard mit Kacheln bestücken, umsortieren und fixieren (siehe unten).

Was beim Öffnen von Zeitarchiv über die HA-Sidebar erscheint (Übersicht
oder direkt das Energiedashboard), legt **Einstellungen → Darstellung →
Startseite** fest. Der „Übersicht"-Eintrag in der Kopfzeile führt davon
unabhängig immer zur Übersicht selbst.

Die Glocke in der Kopfzeile (auf jeder Seite sichtbar) zeigt an, was gerade
im Hintergrund arbeitet (siehe [Was gerade läuft](#was-gerade-läuft)), und
aktuelle Systemmeldungen — z. B. eine empfohlene Index-Optimierung, einen
fehlgeschlagenen Backup- oder Aufbewahrungslauf, oder ein verfügbares
Update. Nicht-kritische Meldungen (Info/Warnung) lassen sich einzeln für
1 Stunde, 1 Tag, 7 Tage, 30 Tage oder dauerhaft stummschalten; Fehler nie.
Bereits stummgeschaltete Meldungen bleiben unter **Einstellungen →
Meldungen** einsehbar und lassen sich dort vorzeitig wieder aktivieren.

## Übersichten durchsuchen und sortieren

Die drei Übersichten **Dashboards**, **Charts** und **Tabellen** funktionieren
gleich: eine Kachel je Eintrag, darüber eine Zeile zum Suchen und Sortieren.

- **Suche** filtert nach dem Namen, ohne auf Groß- und Kleinschreibung zu
  achten. Umlaute lassen sich auch umschrieben eingeben: „ubersicht“ oder
  „uebersicht“ findet ebenso „Übersicht“.
- **Sortierung**: neueste zuerst (Vorgabe), älteste zuerst, Name A–Z oder
  Name Z–A.
- **Favoriten zuerst** ist ein eigener Schalter neben der Sortierung, kein
  eigener Sortiermodus. Eingeschaltet stehen favorisierte Einträge oben,
  innerhalb der Favoriten und darunter gilt die gewählte Sortierung
  unverändert weiter — beides ist also frei kombinierbar. Der Schalter ist
  standardmäßig aktiv.
- Suchbegriffe wirken nur für den Moment; die gewählte Sortierung und der
  Favoriten-Schalter werden je Übersicht im Browser gemerkt und gelten beim
  nächsten Aufruf wieder.

Ein Klick auf eine Kachel öffnet den Eintrag. Alles Weitere — Bearbeiten,
Duplizieren, Löschen — steht im Kachelmenü (⋮) oben rechts, der Stern daneben
schaltet den Favoriten um.

**Namen** von Dashboards, Charts und Tabellen sind jeweils innerhalb ihrer
Gattung eindeutig und höchstens 50 Zeichen lang. Beim Vergleich spielen Groß-
und Kleinschreibung sowie Leerzeichen am Rand keine Rolle: neben einem Chart
„Wind“ lässt sich kein zweites „wind“ anlegen. Ein bereits vergebener Name
wird beim Speichern mit einem Hinweis abgelehnt. Duplikate zählen selbst
hoch — „Wind (Kopie)“, danach „Wind (Kopie 2)“ und so weiter.

## Erklärungen zu einem Feld

Wo ein Feld oder ein Abschnitt eine Erklärung hat, steht ein kleines „i" neben
seiner Beschriftung. Ein Klick darauf klappt den Text auf, ein weiterer wieder
zu — so steht die Erklärung beim Einrichten zur Verfügung, ohne danach
dauerhaft Platz zu belegen. Das gilt auf allen Bildschirmbreiten gleich.

Nicht alles klappt weg: **Warnungen** („entfernt markierte Datensätze
endgültig") und **Statuszeilen** („Liste wird beim Öffnen geladen …", „12
doppelte Zeitstempel in den letzten 30 Tagen") stehen ungefragt da. Wo eine
Warnung einen nicht umkehrbaren Verlust ankündigt, trägt sie zusätzlich eine
farbige Kante am linken Rand.

## Auf dem Telefon

Zeitarchiv ist dieselbe Anwendung, ob im Browser am Schreibtisch oder auf dem
Telefon — nur zeigen Listen dort weniger auf einmal. Zwei Dinge sehen deshalb
auf schmalen Bildschirmen anders aus.

**Listen werden zu Karten.** Statt einer Tabelle, die seitwärts geschoben
werden muss, bekommt jede Zeile eine eigene Karte, in der jeder Wert seine
Spaltenüberschrift bei sich trägt. Die Karte ist zunächst **eingeklappt** und
zeigt den Namen und einen Leitwert — in der Entitätenliste den letzten Wert,
auf der Housekeeping-Seite ebenso. Der Pfeil rechts oben klappt den Rest auf
und wieder zu; der Stern daneben schaltet den Favoriten um. Zugeklappt passen
so rund sieben Einträge auf einen Bildschirm statt drei.

**Alle Einstellungen einer Liste stehen im Menü „Ansicht".** Filter,
Spaltenauswahl und Sortierung ziehen dort zusammen, neben dem Suchfeld. Die
Suche bleibt sichtbar, weil sie die häufigste Handlung ist. Ist mindestens ein
Filter gesetzt, steht die Zahl im Knopf: „Ansicht (2)". Das gilt für die
Entitätenliste, den CSV-Export und die Übersichten für Dashboards, Charts und
Tabellen.

**Am Schreibtisch ändert sich nichts.** Beide Punkte gelten unterhalb von
640 Pixeln Fensterbreite. Wer ein Browserfenster schmal zieht, sieht dieselbe
Ansicht wie auf dem Telefon; beim Aufziehen kommt die gewohnte Tabelle mit
ihrer Werkzeugleiste zurück.

Zwei Kleinigkeiten am Rande: Lange Entitäts-IDs enden auf Karten mit
Auslassungspunkten statt in einer zweiten Zeile umzubrechen — der vollständige
Name steht darüber, die ganze ID auf der Detailseite der Entität. Und ein
schmaler Verlauf am Rand einer Tabelle zeigt an, dass dort seitwärts noch
etwas kommt; er erscheint nur, wenn es tatsächlich etwas zu scrollen gibt.

## Dashboards

- **Dashboards**-Menüpunkt (Hauptnavigation) klappt eine Liste aller
  vorhandenen Dashboards auf. Von dort: neues Dashboard anlegen, ein
  bestehendes öffnen, umbenennen, favorisieren, duplizieren, als
  **Standard-Dashboard** festlegen (dieses erscheint dann auf der
  Übersichtsseite und steht in der Dashboard-Übersicht immer an erster Stelle,
  unabhängig von Sortierung oder „Favoriten zuerst“) oder löschen.
  Es gibt keine Obergrenze für die Anzahl der Dashboards. Das
  Standard-Dashboard trägt einen farbigen Kopfstreifen zur leichteren
  Wiedererkennung (dieselbe Idee wie bei der Energiedashboard-Kachel oben in
  der Liste); steht **Einstellungen → Darstellung → Startseite** auf
  Energiedashboard, zeigt dessen Kachel zusätzlich ein kleines Haus-Symbol.
- Ein Klick auf die Kachel öffnet das Dashboard; „Bearbeiten“, „Duplizieren“,
  „Als Standard festlegen“ und „Löschen“ stehen im Kachelmenü (⋮).
  Suchfeld, Sortierung und der Schalter „Favoriten zuerst“ über den Kacheln
  funktionieren wie bei Charts und Tabellen (siehe
  [Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)).
- Jedes Dashboard zeigt bis zu 18 Kacheln — Charts, Vergleichstabellen und
  **Werte-Kacheln** gemischt — in frei wählbarer Größe (1×1 bis 3×3, im
  Präzisen Modus bis 6×6). Per Drag-and-drop anordnen; über das Kachelmenü
  (⋮) Größe ändern, duplizieren (Charts/Tabellen) oder entfernen. Das
  Entfernen einer Kachel löscht nur die Platzierung, nicht das zugrunde
  liegende Chart oder die Tabelle.
- **Werte-Kachel:** pinnt den aktuellen Wert einer einzelnen Entität direkt
  aufs Dashboard, ohne dafür ein Chart anzulegen. Nach dem Anheften öffnet
  sich sofort die Konfiguration. Bei einer Entität vom Typ **Zähler** ist der
  große Wert nicht der Zählerstand, sondern der **Zuwachs** im gewählten
  Zeitraum (Kürzel „+") — der Stand seit Inbetriebnahme lässt sich über
  „Aktuell" weiterhin einstellen. Das gilt für neu angeheftete Kacheln;
  bestehende bleiben, wie sie eingestellt sind. Die Sparkline ist standardmäßig aktiv und
  zeigt die im Zeitarchiv gespeicherten Rohpunkte der letzten 24 Stunden;
  alternativ lässt sie sich auf einen Punkt je 5, 15 oder 30 Minuten oder je
  Stunde verdichten. Entität, Anzeige der letzten Aktualisierung,
  Nachkommastellen und Titel sind direkt in der Kachel bearbeitbar. Ist der
  letzte Wert älter als 15
  Minuten bzw. eine Stunde, hebt sich der Kartenrahmen gelb bzw. rot
  hervor. Alle Einstellungen einer Werte-Kachel liegen in einem eigenen,
  größeren Einstellungs-Popup (⋮), da hier deutlich mehr Optionen als bei
  Chart-/Tabellen-Kacheln zusammenkommen.
- **Kachel hinzufügen:** die "+"-Kachel öffnet ein Popup mit Registerkarten
  für Charts, Tabellen und Werte-Kacheln, jeweils mit Suchfeld. Charts und
  Tabellen lassen sich direkt aus der Liste anheften oder über "+ Neuer
  Chart"/"+ Neue Tabelle" neu anlegen (landet nach dem Speichern
  automatisch auf diesem Dashboard); Werte-Kacheln werden über die
  Entitäten-Suche ausgewählt. Ein Chart oder eine Tabelle kann gleichzeitig
  auf mehreren Dashboards angeheftet sein.
- In der geöffneten Ansicht eines gespeicherten Charts oder einer Tabelle
  zeigt **Verwendet in**, auf welchen Dashboards der Eintrag liegt. Ein
  Dashboard ist direkt verlinkt; bei mehreren öffnet der Zähler eine kompakte
  Liste mit Links. Die Zuordnung wird weiterhin im jeweiligen Dashboard
  geändert.
- Chart-Kacheln ab Größe 2×2 (im Präzisen Modus ab 3×3) können über das
  Kachelmenü (⋮) eine Legende einblenden ("Legende anzeigen") — Aussehen
  und Inhalt entsprechen dabei exakt der Legende des zugrundeliegenden
  Charts, inklusive dessen "Werte anzeigen"- und Nachkommastellen-
  Einstellung; ein Klick auf die Legende blendet die jeweilige Reihe
  ein/aus, ohne zum Chart zu navigieren.
- **Präziser Modus** (Dashboard-Editor): verdoppelt das Kachelraster von 3
  auf 6 Spalten bei halber Zeilenhöhe — bestehende Kacheln behalten dabei
  ihre optische Größe, weil ihre Größenangabe automatisch mitverdoppelt
  wird. **Lücken auffüllen** lässt spätere, kleinere Kacheln freie Lücken
  im Raster füllen statt strikt der Anheft-Reihenfolge zu folgen. Beide
  Schalter sind unabhängig voneinander kombinierbar.
  Auf schmalen Displays bleibt die Darstellung auch im Präzisen Modus bewusst
  einspaltig, damit Kacheln lesbar und bedienbar bleiben.
- **Dashboard fixieren** (Editor, Schalter "Fixiert"): sperrt Umsortieren,
  Größenändern und Entfernen von Kacheln auf der Ansicht selbst — schützt
  vor versehentlichem Verschieben auf einem z. B. dauerhaft angezeigten
  Wandtablet. Umbenennen und Löschen des Dashboards bleiben im Editor
  weiterhin möglich; nur die Kachel-Ansicht selbst ist gesperrt.
- **Dashboard löschen** entfernt nur die Kachel-Anordnung dieses
  Dashboards — die zugrunde liegenden Charts/Tabellen bleiben erhalten und
  lassen sich über "+ Kachel hinzufügen" anderswo neu anheften.
- Die Ein-/Ausblend-Animation der Kachel-Charts (kurzes Auf- statt
  Sofort-Erscheinen) gilt zentral für alle Kacheln auf allen Dashboards und
  lässt sich unter **Einstellungen → Darstellung** abschalten.

## Entitäten und Verläufe

**Entitäten** listet alle bekannten Entitäten, durchsuchbar über den Namen
oder die Entity-ID und filterbar (z. B. nach Domain). Ein Klick auf eine
Zeile öffnet die **Verlaufsansicht** dieser einen Entität.

Jede Entität hat drei gleichrangige Ansichten, erreichbar über die Reiterzeile
unter dem Namen: **Verlauf** (das Diagramm), **Werte bearbeiten** (siehe
[Bereinigung](#bereinigung)) und **Konfiguration** (siehe
[Entität konfigurieren](#entität-konfigurieren)). Alle drei tragen denselben
Kopf — Anzeigename, Favoriten-Stern, darunter Entity-ID, Typ und der Weg
zurück zur Liste —, sodass ein Wechsel nur den Inhalt darunter austauscht. Das
Optionen-Menü der Verlaufsansicht enthält deshalb nur noch Aktionen und
Darstellungs-Schalter, keine Navigation mehr.

### Zeitraum-Navigation

- Auswahl von Stunde, Tag, Woche, Monat, Jahr oder Dekade als Basiseinheit;
  Vor-/Zurück-Pfeile blättern jeweils um eine Einheit.
- **Ein erneuter Klick auf die bereits gewählte Einheit springt zurück in die
  laufende Periode** — „Tag" auf heute, „Monat" auf den aktuellen Monat.
  Dieselbe Geste wie im Energiedashboard.
- **Laufend** ("bis heute") zeigt den aktuellen, noch nicht abgeschlossenen
  Zeitraum, z. B. "diese Woche bis jetzt" — auch bevor für die restliche
  Periode überhaupt Daten vorliegen, reicht die Achse bis zur vollen
  Kalendergrenze (z. B. bis Sonntag bei "Woche").
- **Rollierend** (Schalter im Optionen-Menü) zeigt stattdessen ein festes
  Zeitfenster relativ zu jetzt, z. B. "letzte 24 Stunden" oder "letzte
  30 Tage", unabhängig von Kalendergrenzen. Bei "Monat" sind das genau
  30 Tage, kein Kalendermonat — "ein Monat vor dem 31. Januar" wäre
  nicht eindeutig.

### Darstellung

- **Diagrammtyp:** Linie oder Balken; bei Schalter-Entitäten (`switch`,
  `binary_sensor` u. Ä.) zusätzlich ein Zeitstrahl, der die AN-Intervalle
  als durchgehende Balken zeigt statt einzelner Punkte.
- Linien lassen sich glätten (Optionen-Menü), was kurzfristiges Rauschen
  visuell unterdrückt, ohne die zugrunde liegenden Werte zu verändern.
- **Rohwerte** zeigt statt aggregierter Punkte jeden einzelnen
  gespeicherten Messwert im Zeitraum — sinnvoll bei genauerer Prüfung
  kurzer Zeiträume, bei sehr langen Zeiträumen begrenzt durch das
  Abfragelimit.
- Das Optionen-Menü zeigt nur, was für die aktuelle Darstellung überhaupt
  wirkt: **Punkte** und **Rohwerte** gehören zu Liniencharts, **Werte
  anzeigen** zu Balken. Beim Umschalten des Diagrammtyps wechseln die
  angebotenen Optionen entsprechend mit.
- **Dynamische Y-Achse** skaliert die Achse auf die tatsächliche
  Wertespanne des angezeigten Zeitraums statt bei 0 zu beginnen — macht
  kleine Schwankungen sichtbarer, kann die visuelle Größe von Änderungen
  aber auch überzeichnen.
- **Werte anzeigen** blendet die Zahlenwerte direkt neben den Datenpunkten
  ein.
- **Durchschnittslinie** legt eine gestrichelte waagerechte Linie beim
  Durchschnitt der angezeigten Werte über den Chart, mit dem Wert rechts
  daneben. Sie ist unabhängig von der Legende: die Legende nennt die Zahl,
  die Linie zeigt, wo sie im Bild liegt. Der Zoom ändert sie nicht — sie
  gehört zum geladenen Zeitraum, nicht zum gerade sichtbaren Ausschnitt.
- **Nachkommastellen** übersteuert für diese Ansicht die globale Anzeige-
  Einstellung der Entität (Automatisch oder fest 0–3).

### In einen Ausschnitt hineinzoomen

Wenn ein Zeitraum viele Messwerte enthält — Rohwerte eines häufig meldenden
Sensors etwa —, liegen die Punkte dichter, als der Bildschirm sie
auseinanderhalten kann. Dann lässt sich ein Ausschnitt vergrößern:

- **Am Rechner:** **Strg** gedrückt halten und das Mausrad drehen. Das Rad
  allein scrollt weiter die Seite, es passiert also nichts versehentlich. Mit
  gedrückter Strg-Taste ziehen verschiebt den Ausschnitt.
- **Einen Bereich direkt aufziehen:** **Umschalt** gedrückt halten und mit der
  Maus über den gewünschten Zeitraum ziehen. Beim Loslassen zeigt das Diagramm
  genau diesen Ausschnitt.
- **Am Trackpad und auf dem Telefon:** mit zwei Fingern auseinander- bzw.
  zusammenziehen. Ein Wisch mit einem Finger scrollt wie überall sonst die
  Seite.

Ist ein Ausschnitt aktiv, zeigt der Knopf **Ausschnitt** in der Werkzeugleiste
dessen Zeitspanne (z. B. "14:20 – 16:05"); ein Klick darauf zeigt wieder den
ganzen Zeitraum. Der Ausschnitt ist bewusst flüchtig: er wird nicht
gespeichert, und ein Wechsel des Zeitraums oder eine Änderung im
Optionen-Menü stellt die volle Ansicht wieder her.

Unter dem Diagramm steht immer eine Zeile, die sagt, woran man ist: wie viele
Datenpunkte gerade gezeichnet sind und ob sich davon ein Ausschnitt vergrößern
lässt.

Angeboten wird das nur, wo es etwas aufzudecken gibt — bei wenigen Punkten
(etwa zwölf Monatsbalken im Jahr) bleibt der Knopf grau. Beim **Zeitstrahl**
gibt es ihn dagegen immer: Schaltvorgänge von wenigen Minuten sind in einer
Monatsansicht schmaler als ein Bildpunkt und werden erst beim Hineinzoomen
sichtbar.

### Markierte Werte sehen

Wer auf der Bereinigungsseite Werte „löscht", markiert sie zunächst nur — weg
sind sie erst nach dem endgültigen Bereinigen unter **Housekeeping →
Speicherplatz → Endgültige Bereinigung**. Bis dahin verschwinden sie zwar aus
dem Verlauf, sind aber noch da.

**Markierte Werte** (Optionen-Menü) zeigt, wo: die betroffenen Zeitabschnitte
werden im Diagramm hinterlegt. Aufeinanderfolgende Markierungen bilden dabei
ein zusammenhängendes Feld, weit auseinanderliegende bleiben getrennt. So
lässt sich vor dem endgültigen Löschen noch einmal ansehen, ob wirklich nur
die gemeinten Stellen erwischt wurden. Die Einstellung gilt pro Entität und
ist standardmäßig aus.

Gibt es im gezeigten Zeitraum Markierungen, erscheint ein Knopf mit ihrer
Anzahl in der Werkzeugleiste. Er führt wahlweise zur Bereinigungsseite (dort
lassen sich einzelne Markierungen zurücknehmen) oder direkt zur Vorschau
„Rückgängig", die zeigt, was die zuletzt markierte Löschung wiederherstellen
würde.

Beides sind Verweise, keine Aktionen — mit Absicht: die zuletzt markierte
Löschung kann sehr viel mehr Werte umfassen, als der Knopf anzeigt (er zählt
nur den gerade gezeigten Zeitraum), und sie kann vollständig außerhalb davon
liegen. Die Vorschau zeigt die betroffenen Zeilen mit Anzahl, bevor irgendetwas
passiert.

In der Tabelle unter **Housekeeping → Speicherplatz → Endgültige
Bereinigung** führt jeder Entitätsname direkt hierher, mit bereits
eingeschalteter Anzeige.

### Vergleich

Über das Optionen-Menü lässt sich die aktuelle Ansicht mit der Vorperiode
oder dem Vorjahr überlagern. Die Beschriftung passt sich automatisch an den
gewählten Zeitraum an (z. B. "Vortag" bei Tagesansicht, "Vorjahrestag" beim
Jahresvergleich einer Tagesansicht) und erscheint direkt im Button, sodass
die aktive Vergleichsoption ohne Menüaufruf erkennbar bleibt.

Bei den Zeiträumen **Jahr** und **Dekade** gibt es nur die Vorperiode: bei
"Jahr" ist sie das Vorjahr, ein zweiter Eintrag hieße dort genauso; bei
"Dekade" würde ein Vorjahresvergleich das Jahrzehnt nur um ein Jahr
verschieben und sich damit fast vollständig mit dem gezeigten überschneiden.

### Kennzahlen und Legende

Aktuell/Min/Max/Durchschnitt/Summe des angezeigten Zeitraums lassen sich
wahlweise direkt einblenden — als kompakte Chips oder als kleine Tabelle
(einstellbar über den Legenden-Stil). Beide Darstellungen sind anklickbar,
um einzelne Reihen ein- oder auszublenden, ohne den Zeitraum zu verlassen.

### Ansicht sichern

**Als Chart speichern** (Optionen-Menü) legt die aktuelle Ansicht —
inklusive aller gewählten Optionen und ggf. bereits hinzugefügter weiterer
Entitäten — als eigenständiges Chart ab, das sich danach wie jedes andere
Chart bearbeiten und auf ein Dashboard anheften lässt.

### Gespeicherte Optionen

Alle Optionen im Optionen-Menü (Rollierend, Rohwerte, Markierte Werte,
Diagrammtyp, Punkte anzeigen, Werte anzeigen, Dynamische Y-Achse,
Durchschnittslinie, Legenden-Statistik und -Kennzahlen, Legenden-Stil)
werden **pro Entität**
dauerhaft gespeichert und beim nächsten Aufruf automatisch wieder
angewendet. Die Startwerte für neu geöffnete Entitäten lassen sich unter
**Einstellungen → Darstellung** ändern; "Optionen auf Standard
zurücksetzen" (im Optionen-Menü der Entität) wirft nur diese eine Entität
wieder auf diese Startwerte zurück. Nicht dazu gehört der gezoomte
Ausschnitt: er beschreibt keine Eigenschaft der Entität, sondern nur den
gerade betrachteten Bildausschnitt, und gilt deshalb nur bis zur nächsten
Änderung.

## Entität konfigurieren

Über das Zahnrad-Symbol in der Entitätenliste oder über den Reiter
**Konfiguration** einer geöffneten Entität. Jede Einstellung gilt **nur für
diese eine Entität** und
überschreibt den globalen Standard aus **Einstellungen → Archivierung**. Neue
globale Standards wirken nie rückwirkend auf bereits angelegte Entitäten.

### Womit fange ich an?

Die acht Felder greifen an sehr unterschiedlichen Stellen an. Diese Einteilung
ist beim Einstellen wichtiger als die Reihenfolge im Formular:

| Wirkt auf … | Felder | Rückgängig? |
| --- | --- | --- |
| **Was überhaupt gespeichert wird** | Auflösung, Wertänderungsfilter | Nein — was nicht gespeichert wurde, ist weg |
| **Wie lange es bleibt** | Aufbewahrung | Bis zur nächsten Löschung ja |
| **Was gezeigt wird** | Anzeigename, Nachkommastellen, Anzeigemodus | Jederzeit |
| **Was die Bereinigung markiert** | Lücken-Erkennung, Ausreißer-Erkennung | Jederzeit |

Die beiden ersten Felder verwerfen Messwerte beim Eintreffen. Alles darunter
lässt sich beliebig oft ändern, ohne etwas zu verlieren.

Eine Ausnahme von dieser sauberen Trennung: **Nachkommastellen** ist nicht rein
optisch — der Wertänderungsfilter benutzt dieselbe Rundung, um zu entscheiden,
ob zwei Werte „gleich" sind (siehe unten).

### App-Anzeigename

Optional, bis 40 Zeichen. Überschreibt die Darstellung **nur in Zeitarchiv**
(Listen, Auswahlfelder, Diagramme, Tabellen) — Home Assistants eigener
`friendly_name` und die Entitäts-ID bleiben unangetastet. Ein Tag-Symbol
markiert überall, wo ein eigener Name aktiv ist. Leer lassen stellt den
HA-Namen wieder her.

### Auflösung

**Mindestabstand zwischen zwei gespeicherten Werten.** Wählbar: Rohdaten,
30 Sekunden, 1, 5, 15 Minuten, 1 Stunde.

Zwei Dinge, die man leicht falsch erwartet:

- **Zu dichte Werte werden verworfen, nicht zusammengefasst.** Bei „5 Min."
  wird ein Wert, der 30 Sekunden nach dem letzten gespeicherten eintrifft,
  weggeworfen — es entsteht kein Mittelwert daraus. Wer verdichtete Werte
  will, lässt die Auflösung fein und nutzt in Charts und Tabellen die dortige
  Aggregation.
- **Der Abstand läuft ab dem zuletzt gespeicherten Wert**, nicht ab festen
  Uhrzeit-Rastern. Nach einer Pause wird der erste wieder eintreffende Wert
  also sofort gespeichert, nicht erst zur nächsten vollen Fünf-Minuten-Marke.

„Rohdaten" speichert jede eintreffende Zustandsänderung und ist die
Voreinstellung für neu erkannte Entitäten. Die Einstellung gilt nur für neu
eintreffende Werte; bereits archivierte bleiben unverändert.

### Aufbewahrung

Wie lange Werte behalten werden: Unbegrenzt, 30 Tage, 90 Tage, 365 Tage,
2 Jahre, 5 Jahre. Voreinstellung für neue Entitäten ist **Unbegrenzt**.

**Das Feld allein löscht nichts.** Es legt nur fest, was als „zu alt" gilt.
Ob und wann tatsächlich gelöscht wird, steuert **Einstellungen →
Aufbewahrung**: Ist die automatische Durchsetzung dort aus, sammeln sich die
Daten weiter an, egal was hier steht. Ein Blick auf **Housekeeping →
Aufbewahrung** zeigt, wie viel bei der nächsten Durchsetzung wegfiele.

### Nachkommastellen

„Automatisch" zeigt bis zu drei Stellen und lässt Nullen am Ende weg (4 statt
4,000). Eine feste Auswahl (0–3) rundet auf genau diese Stellen und füllt auf
(4,00 bei zwei Stellen).

**Die Einstellung wirkt über die Anzeige hinaus:** Der Wertänderungsfilter
entscheidet anhand derselben Rundung, ob ein neuer Wert dem vorherigen gleicht.
Wer die Nachkommastellen von 3 auf 1 stellt und den Filter aktiv hat, wirft
damit auch mehr Werte weg — aus 21,04 °C und 21,03 °C wird zweimal 21,0 °C,
also ein gefilterter Wert. „Automatisch" verhält sich dabei wie 3.

### Wertänderungsfilter

Überspringt Werte, die nach der Nachkommastellen-Regel dem zuletzt
gespeicherten gleichen — spart bei trägen Sensoren erheblich Platz.

**Spätestens alle 6 Stunden wird trotzdem ein Wert gespeichert**, auch wenn
sich nichts geändert hat. Dieses Lebenszeichen ist der Unterschied zwischen
„der Sensor meldet unverändert 21,0 °C" und „der Sensor meldet gar nichts
mehr" — ohne es wäre beides in den Daten nicht zu unterscheiden, und die
Inaktivitäts-Meldung würde falsch anschlagen.

Bei neu erkannten Entitäten standardmäßig aktiv.

### Lücken-Erkennung

Ab welcher Pause zwischen zwei Werten die Bereinigungs-Seite eine **Lücke**
markiert: 1, 5, 15, 30 Minuten, 1, 6, 12 Stunden, 1 Tag — oder Aus. Die
Markierung ist reine Analyse; an den Daten ändert sie nichts.

**Der Wert wird automatisch angehoben, wenn er nicht zutreffen kann.** Eine
Auflösung von 1 Stunde erzwingt selbst schon einen Mindestabstand von einer
Stunde zwischen Werten; eine Lücken-Erkennung von 5 Minuten würde dann bei
*jedem* normalen Zyklus anschlagen. Dasselbe gilt für den aktivierten
Wertänderungsfilter mit seinen 6 Stunden Lebenszeichen. Zeitarchiv hebt die
Schwelle beim Ändern von Auflösung oder Filter deshalb auf die nächste Stufe,
die noch sinnvoll ist, und sagt es dazu. Danach lässt sie sich jederzeit wieder
manuell verkleinern. Bestehende Entitäten mit einer solchen Kombination listet
**Housekeeping → Konfiguration**.

### Ausreißer-Erkennung

Markiert **unplausible Einzelwerte** auf der Bereinigungs-Seite: verrutschte
Ziffern, Übertragungsfehler, Sensoraussetzer. Nicht gemeint sind hohe Werte —
ein heißer Tag oder eine Waschmaschine, die gerade läuft, sind keine Ausreißer.
Wie die Lücken-Erkennung reine Analyse, ohne Eingriff in die Daten.

Einstellbar ist ein **Vielfaches**: 10, 20, 50, 100 — oder Aus. Voreingestellt
ist 50×. Kleinere Zahl heißt empfindlicher.

**Vielfaches wovon? Von dem, was für diese Entität üblich ist.** Das ist der
Kern der Sache, und es hängt vom Typ ab:

**Zähler** (Verbrauch, Erzeugung, alles mit stetig steigendem Stand) — Bezug
ist der **übliche Zuwachs**: der Median der letzten 50 Zuwächse. Markiert wird
ein Zuwachs, der ein Vielfaches davon beträgt. Der Zählerstand selbst spielt
keine Rolle.

> Ein Stromzähler wächst üblicherweise um 0,0016 kWh je Messwert. Der größte
> *echte* Zuwachs über einen Monat lag beim 10-fachen davon. Rutscht dagegen
> eine Ziffer — 10.123 wird zu 101.230 —, ist dieser eine Zuwachs das
> 56.941.875-fache. Zwischen normalem Betrieb und echtem Fehler liegen sechs
> Größenordnungen; jede Schwelle dazwischen trifft nur den Fehler.

Ein gleichmäßig laufender Zähler löst nie aus. Rückgänge werden hier **nicht**
markiert — dafür gibt es die eigene Markierung **Zählerrückgang**.

**Alle anderen Sensoren** (Temperatur, Leistung, Luftfeuchte …) — Bezug ist die
**übliche Schwankung der letzten fünfzehn Werte**: wie weit ein Wert typischer­
weise von der Mitte dieses Fensters entfernt liegt. Markiert wird, wer ein
Vielfaches weiter entfernt liegt als das.

> Eine Raumtemperatur pendelt um 21 °C, üblicherweise ±0,3 °C. Bei 20× wird
> markiert, was mehr als 6 °C daneben liegt — ein einzelner Messwert von 60 °C
> also, das normale Auf und Ab nicht.

Fünfzehn Werte als Fenster bedeutet: ein langsam driftendes Signal fällt
**nicht** auf, weil der Bezug mitwandert. Auffällig ist nur, wer aus seiner
unmittelbaren Nachbarschaft ausbricht.

**Warum ein Vielfaches und kein Prozentsatz?** Weil ein Prozentsatz auf zwei
Sensoren derselben Art Verschiedenes bedeutet. 5 % sind bei einem frisch
angeschlossenen Zähler (Stand 12) ganze 0,6 und bei einem alten (Stand
1.200.000) volle 60.000 — dieselbe Einstellung wäre beim einen streng und beim
anderen wirkungslos, obwohl beide denselben Verbrauch messen. Dasselbe gilt für
die Skala: ein Sprung von 20 auf 60 sind in Grad Celsius 200 %, in Kelvin (293
auf 333) nur 13,6 %. Ein Vielfaches des Üblichen kennt diese Abhängigkeit
nicht: dieselbe Einstellung bedeutet auf jedem Sensor dasselbe.

**Zwei Grenzen, die man kennen sollte:**

- **Die ersten Werte eines Zeitraums werden nicht geprüft.** Erst wenn genug
  Vorgeschichte da ist (fünf Werte bzw. fünf Zuwächse), gibt es einen Bezug.
- **Ein völlig konstantes Signal wird übersprungen.** Sind alle Werte im
  Fenster gleich, gibt es keine „übliche Schwankung", an der sich ein
  Vielfaches messen ließe — dann wird nichts markiert, statt zu raten. In der
  Praxis selten, weil der Wertänderungsfilter konstante Reihen ohnehin
  ausdünnt.

**Für Schalter ist die Einstellung nicht verfügbar.** Bei Werten, die nur 0
oder 1 sein können, ist die übliche Schwankung rechnerisch immer null — die
Erkennung würde nie etwas markieren. Ein Regler, der nachweislich nichts tut,
wäre eine Falschauskunft; das Feld ist deshalb ausgegraut und nennt den Grund.

**Was die Schwelle tatsächlich bewirkt, steht unter dem Feld:** der Anteil der
Werte, den sie über die komplette Historie markiert, mit Balken und absoluter
Zahl. Ist die Quote noch nicht berechnet oder gehört sie zu einer anderen
Schwelle, erscheint stattdessen **Jetzt prüfen**; steht sie schon da, lässt sie
sich mit **Neu berechnen** auffrischen. Die Berechnung liest den gesamten
Bestand der Entität und läuft deshalb nur auf Klick.

> **Nach dem Update von einer älteren Version:** Die Schwelle war früher ein
> Prozentsatz (5, 10, 25, 50, 100 %). Gespeicherte Einstellungen werden einmalig
> auf die neue Leiter übernommen, und zwar nach ihrem Platz darauf — die
> empfindlichste alte Stufe wird die empfindlichste neue (5 % und 10 % → 10×,
> 25 % → 20×, 50 % → 50×, 100 % → 100×). „Aus" bleibt aus. Weil die Zahlen jetzt
> etwas anderes bedeuten, lohnt ein Blick auf die Quote unter dem Feld.

### Anzeigemodus

Nur bei Schaltern (`binary_sensor`, `switch`, `input_boolean`):

- **AN/AUS (Rohwert)** zeigt den Zustand als 0/1.
- **Zeit (Dauer)** zeigt stattdessen die kumulierte Einschaltdauer je Zeitraum,
  lesbar formatiert (z. B. `1h 29m`) — sinnvoll bei Anwesenheits-, Tür- und
  Bewegungssensoren.

Als Schalter gelten die Domains `binary_sensor`, `switch` und `input_boolean`.

### Was rückwirkend wirkt — und was nicht

Auflösung, Aufbewahrung und Nachkommastellen wirken **nur auf künftig
eintreffende bzw. künftig berechnete Werte**, nie rückwirkend auf bereits
archivierte Daten. Eine gröbere Auflösung verdünnt also keinen Altbestand, und
eine feinere holt nichts zurück, was nie gespeichert wurde.

Lücken- und Ausreißer-Erkennung wirken dagegen **immer sofort auf den ganzen
Bestand**: Sie werden bei jedem Aufruf der Bereinigungs-Seite aus den Rohwerten
des angezeigten Zeitraums neu gerechnet, nichts davon wird gespeichert, und die
Daten selbst bleiben unberührt. Eine geänderte Schwelle gilt deshalb sofort und
rückwirkend — es gibt keinen Bestand alter Markierungen, der nachgezogen werden
müsste. (Einzige Ausnahme ist die Quote unter dem Schwellenfeld: die gehört zu
einem Vollscan über die ganze Historie und wird nur auf Klick aufgefrischt.)

### Datenverwaltung

Am Seitenende stehen zwei endgültige Aktionen:

- **Alle Werte löschen** entfernt sämtliche Daten dieser Entität (laufender
  Monat, Archiv, Rollups), behält aber die individuelle Konfiguration
  (Auflösung, Aufbewahrung usw.) bei. Sinnvoll, um bei einer fehlerhaft
  konfigurierten Quelle noch einmal bei null anzufangen, ohne die
  Einstellungen neu setzen zu müssen.
- **Entität entfernen** löscht zusätzlich auch die Konfiguration. Sendet
  Home Assistant die Entität weiter, wird sie beim nächsten empfangenen
  Wert automatisch wieder mit den aktuellen globalen Standards neu
  angelegt.

Beide Aktionen verlangen vor der Ausführung eine eindeutige Bestätigung
(Eingabe des Entitätsnamens) und sind danach nicht rückgängig zu machen.
Charts oder Tabellen, die diese Entität verwenden, zeigen ab diesem
Zeitpunkt schlicht keine Daten mehr für sie.

## Bereinigung

Über den Reiter **Werte bearbeiten** einer geöffneten Entität erreichbar,
darin drei Bereiche:

### 1. Bereinigen

Erkannte Ausreißer, Lücken, Duplikate und gerundet gleiche Wiederholungen
werden als Liste angezeigt, je mit einer kurzen Begründung — sichtbar, wenn
der Mauszeiger auf der roten Markierung steht. Sie nennt die Regel, an der
die Markierung hängt, und den unmittelbar vorhergehenden Wert mit Zeitpunkt:

> `3 Std. 50 Min. seit vorherigem Wert 21,2 °C um 08:10`
> `21,56 liegt 23× weiter vom Median der letzten 15 Werte (24,27) entfernt`
> `als üblich (±0,12) — Vorwert 22,06 um 11.08.2026 00:01:41` Einzelne Einträge oder alle
zusammen auswählen und löschen — das ist zunächst ein **Soft-Delete**: Die
Werte verschwinden sofort aus jeder Anzeige (Charts, Tabellen, Rohwerte),
sind aber über "Rückgängig (letzte Löschung)" wiederherstellbar, solange
noch keine endgültige Bereinigung stattgefunden hat (siehe unten).

Bei steigenden Zählern (Home-Assistant-`state_class` `total_increasing`,
z. B. Energiezähler) werden niedrigere Folgewerte gesondert als mögliche
Zähler-Resets protokolliert und unter "Zählerrückgänge" markiert; sie
bleiben standardmäßig gespeichert, da ein Reset (z. B. Zählertausch) ein
gültiges Ereignis sein kann und nicht automatisch als Fehler gilt.

Wiederholungen (gerundet gleiche Folgewerte) lassen sich mit derselben
Sechs-Stunden-Lebenszeichenregel wie der laufende Wertänderungsfilter auch
nachträglich verdichten — nützlich, wenn der Filter erst später aktiviert
wurde und ältere Daten noch unverdichtet vorliegen.

### 2. Korrigieren

Einzelne Werte direkt bearbeiten (Klick auf die Wert-Zelle in der
Rohwert-Tabelle) statt zu löschen — etwa um einen erkennbaren
Sensor-Ausreißer auf einen plausiblen Wert zu setzen, statt an dieser
Stelle eine Lücke offenzulassen. Die Rohwert-Tabelle zeigt dabei die
Einheit direkt neben jedem Wert.

### 3. Hinzufügen

Einen fehlenden Messpunkt manuell mit Zeitstempel und Wert ergänzen —
Zahlen im deutschen Format mit Komma als Dezimaltrennzeichen (z. B. `21,5`).

### Kopfzeile und endgültiges Entfernen

Die Kopfzeile des Bereinigungsbereichs zeigt sowohl die Datensatzanzahl im
aktuell gewählten Zeitraum als auch den sichtbaren Gesamtbestand samt
Ausreißern/Lücken/Duplikaten/Wiederholungen über die **komplette** Historie
der Entität — unabhängig vom gerade angezeigten Ausschnitt.

Soft-gelöschte Werte belegen weiterhin Speicherplatz, bis sie unter
**Housekeeping → Speicherplatz** physisch bereinigt werden. Dort zeigt
eine Vorschau vorab, wie viele Zeilen tatsächlich entfernbar sind
(inklusive Aufschlüsselung nach laufendem Monat und Archiv), bevor der
Schritt tatsächlich ausgeführt wird. Dieser Schritt ist endgültig — danach
ist "Rückgängig" nicht mehr möglich.

## Datenhandling

Dieser Abschnitt erklärt genauer, was hinter den Kulissen passiert, wenn
Werte gelöscht, geändert, hinzugefügt oder automatisch aufgeräumt werden —
und wie sich das jeweils auf Charts, Tabellen, Speicherplatz und
Wiederherstellbarkeit auswirkt.

### Der Weg eines Werts

Jeder eingehende Wert durchläuft dieselben Stationen, unabhängig davon, ob
er von der Integration kommt oder manuell hinzugefügt wurde:

```text
Eingehender Wert
      │
      ▼
Hot Buffer (laufender Monat, unkomprimiert)
      │
      │  Rotation: automatisch beim ersten Wert eines neuen Kalendermonats,
      │  oder manuell nachgeholt (z. B. bei einer länger stillen Entität)
      ▼
Archiv (abgeschlossene Monate, komprimiert)  ──►  Rollups (Stunde/Tag · Monat · Jahr)
      │                                                     │
      │              Aufbewahrung (Retention)                │
      │     entfernt ganze überfällige Monate aus Archiv     │
      │              UND den zugehörigen Rollups             │
      ▼                                                     ▼
              endgültig entfernt — kein "Rückgängig"
```

Charts und Tabellen greifen für kurze, aktuelle Zeiträume auf den Hot
Buffer zu und für längere/vergangene Zeiträume auf Archiv und Rollups —
diese Umschaltung geschieht automatisch und ist beim Ansehen nicht
sichtbar. Die Rotation selbst verändert an den Werten nichts, sie
verschiebt nur den laufenden Monat vom Hot Buffer ins Archiv, sobald er
abgeschlossen ist.

### Aufbewahrung (Retention)

Die je Entität konfigurierte Aufbewahrungsfrist (Zahnrad-Symbol → **Aufbewahrung**,
siehe [Entität konfigurieren](#entität-konfigurieren)) wird nicht laufend
angewendet, sondern nur, wenn die Aufbewahrungs-Durchsetzung tatsächlich
läuft:

- **Manuell** über **Housekeeping → Aufbewahrung** — mit einer Vorschau,
  die zeigt, was ein Lauf entfernen würde, bevor er tatsächlich ausgeführt
  wird.
- **Automatisch**, wenn unter **Housekeeping → Aufbewahrung** ein Zeitplan
  (täglich oder wöchentlich, mit Uhrzeit) hinterlegt ist. Ein einzelner
  Wartungsplaner prüft im Hintergrund regelmäßig, ob der nächste geplante
  Lauf fällig ist; war die App zum geplanten Zeitpunkt nicht aktiv, wird
  **höchstens ein** verpasster Lauf nachgeholt, nie mehrere auf einmal.
- Ein Lauf betrifft immer **nur ganze, abgeschlossene Kalendermonate** — ein
  Monat wird komplett entfernt, sobald sein Ende älter als die
  Aufbewahrungsfrist ist, niemals teilweise. Entfernt werden dabei
  gleichzeitig der Archiv-Monat und die dazugehörigen Rollup-Zeilen
  (Stunde/Tag, Monat, Jahr), damit beide nie auseinanderlaufen; im laufenden
  Monat (Hot Buffer) werden überfällige Zeilen direkt entfernt.
- Als **Unbegrenzt** markierte Entitäten werden dabei komplett übersprungen.
- Aufbewahrung, Backup, Import und Rotation greifen nie gleichzeitig auf den
  Datenbestand zu — läuft bereits einer dieser Vorgänge, wartet ein
  zeitgleich fälliger automatischer Aufbewahrungslauf nicht, sondern wird
  für diesen Termin übersprungen (der nächste reguläre Termin läuft normal
  weiter).

**Wichtig:** Anders als eine geänderte Auflösung (wirkt nur auf künftig
eintreffende Werte, siehe [Häufige Fragen](#häufige-fragen)) wirkt eine
verkürzte Aufbewahrungsfrist beim nächsten Durchlauf **rückwirkend** auf
bereits gespeicherte, abgeschlossene Monate. Eine Frist heraufzusetzen oder
auf Unbegrenzt zu stellen ist dagegen jederzeit gefahrlos — dadurch wird nie
etwas gelöscht, das bereits entfernt wurde, ist es endgültig weg.

Diese Löschung ist **nicht** das Soft-Delete aus dem Bereinigen-Tab, sondern
sofort endgültig — es gibt keine Vorstufe und kein "Rückgängig". Ein Backup
vor einer erstmalig aktivierten oder deutlich verkürzten Aufbewahrungsfrist
ist deshalb empfehlenswert.

### Werte löschen: drei Stufen

Werte, die über **Bereinigen** entfernt werden (einzeln oder als Auswahl),
durchlaufen drei klar getrennte Stufen — nur die letzte davon ist
endgültig:

```text
Wert vorhanden
      │
      │  Bereinigen → "Löschen"
      ▼
Als gelöscht markiert (Soft-Delete)
  · verschwindet sofort aus Charts, Tabellen, Rohwert-Listen, Export, Statistik
  · die Datei auf der Festplatte bleibt dabei unverändert
  · zählt weiterhin zum belegten Speicherplatz
      │                                    │
      │  Bereinigen →                      │  Housekeeping → Speicherplatz →
      │  "Rückgängig (letzte Löschung)"    │  "Bereinigen" (mit Vorschau)
      ▼                                    ▼
Wert wieder sichtbar                Physisch entfernt
                                       · Archiv- bzw. Hot-Buffer-Datei neu geschrieben
                                       · betroffene Rollups neu berechnet
                                       · endgültig, kein "Rückgängig" mehr möglich
```

Zur Markierung ("Löschen"):

- Es wird nichts aus einer Datei entfernt — lediglich vermerkt, dass dieses
  Vorkommen (Entität + Zeitstempel) ab sofort überall ausgeblendet werden
  soll. Bei zwei Werten mit exakt demselben Zeitstempel (Duplikat) lässt
  sich so gezielt nur einer der beiden löschen, ohne den anderen
  mitzunehmen.
- Jeder Löschvorgang (ein Klick auf "Löschen", egal ob ein Wert oder eine
  ganze Auswahl) bildet einen eigenen **Stapel**.

Zu "Rückgängig (letzte Löschung)":

- Macht **immer nur den zuletzt ausgeführten Stapel** rückgängig — es gibt
  keinen längeren Verlauf und kein Zurückspringen über mehrere
  Löschvorgänge hinweg. Ein zweiter Klick auf "Rückgängig" ohne
  zwischenzeitliches erneutes Löschen bewirkt nichts mehr.
- Funktioniert nur, solange der Stapel noch nicht physisch bereinigt wurde
  (siehe unten) — danach existiert die Markierung nicht mehr, es gibt
  nichts mehr rückgängig zu machen.

Zu "Bereinigen" unter **Housekeeping → Speicherplatz**:

- Das ist der einzige Schritt in diesem Kapitel, der Dateien auf der
  Festplatte tatsächlich verändert: Die betroffene Archiv- bzw.
  Hot-Buffer-Datei wird ohne die markierten Zeilen neu geschrieben, die
  davon abhängigen Rollup-Werte werden neu berechnet. Sind in einem
  gesamten Archiv-Monat alle Werte als gelöscht markiert, wird die
  Monatsdatei (samt Rollup-Zeilen) komplett entfernt statt leer neu
  geschrieben.
- Eine Vorschau zeigt vorab, wie viele Zeilen tatsächlich entfernbar sind
  (aufgeschlüsselt nach laufendem Monat und Archiv), bevor der Schritt
  bestätigt wird.
- Danach sind die betroffenen Werte unwiederbringlich weg — auch mit einem
  neuen Backup lässt sich das nicht mehr innerhalb der App rückgängig
  machen (nur eine Wiederherstellung aus einem **älteren** Backup, das vor
  diesem Schritt erstellt wurde, brächte die Werte zurück).

### Werte ändern und hinzufügen

**Korrigieren** (einen bestehenden Wert bearbeiten) und **Hinzufügen**
(einen fehlenden Messpunkt manuell ergänzen) laufen technisch **anders**
als Löschen: Es handelt sich um direkte Schreibvorgänge, nicht um das oben
beschriebene Markieren-Modell.

> ⚠️ **Für Korrigieren und Hinzufügen gibt es kein "Rückgängig".** Anders
> als beim Löschen wird keine Markierung gesetzt, sondern der Wert sofort
> direkt in der Hot-Buffer- bzw. Archiv-Datei überschrieben bzw. ergänzt.
> Ein versehentlich falsch korrigierter oder falsch eingetragener Wert lässt
> sich nur durch eine erneute manuelle Korrektur beheben — oder, falls
> schon zu spät bemerkt, durch die Wiederherstellung eines vorherigen
> Backups. Vor umfangreicheren manuellen Korrekturen lohnt sich deshalb ein
> kurzer Blick auf **System → Backup / Restore**.

Bei "Korrigieren" wird bei mehreren Werten mit demselben Zeitstempel
(Duplikat) gezielt nur der erste zu diesem Zeitstempel gefundene Wert
angepasst, die übrigen bleiben unverändert. Ändert sich der Wert zwischen
Laden der Seite und Bestätigen des Korrigieren-Dialogs (z. B. weil er
zwischenzeitlich schon gelöscht wurde), passiert schlicht nichts — ohne
Fehlermeldung.

### Lücken, Duplikate, Wiederholungen und Zählerrückgänge im Detail

Die vier zusätzlichen Markierungen im Bereinigen-Tab (neben Ausreißern)
folgen jeweils einer eigenen, festen Regel:

| Kategorie | Regel | Was "Bereinigen" konkret tut |
| --- | --- | --- |
| **Lücke** | Der zeitliche Abstand zweier aufeinanderfolgender Werte überschreitet die je Entität eingestellte Schwelle (**Lücken-Erkennung**, in Minuten; "Aus" deaktiviert die Erkennung komplett) | Nur eine Markierung, keine automatische Aktion — Lücken werden angezeigt, nicht gelöscht |
| **Duplikat** | Zwei oder mehr Werte teilen sich exakt denselben Zeitstempel (nicht nur einen ähnlichen) | Der chronologisch zuerst gespeicherte Wert bleibt erhalten, alle weiteren zum selben Zeitstempel werden zum Löschen vorgeschlagen |
| **Wiederholung** | Ein Wert ist (nach Rundung auf die eingestellten Nachkommastellen) identisch zum zuletzt *behaltenen* Wert — dieselbe Regel, die auch beim Eintreffen neuer Werte laufend zur Verdichtung verwendet wird (siehe [Wertänderungsfilter](#entität-konfigurieren)) | Der erste Wert einer Serie gleicher Werte bleibt erhalten, alle folgenden werden vorgeschlagen — **außer** seit dem letzten behaltenen Wert sind bereits 6 Stunden vergangen (Lebenszeichenregel), dann bleibt auch ein unveränderter Wert erhalten |
| **Zählerrückgang** | Nur bei Zähler-Entitäten: ein Wert liegt niedriger als der unmittelbar vorherige behaltene Wert | Nur eine Markierung, **niemals** automatisch gelöscht — ein Rückgang kann ein echtes Ereignis sein (z. B. Zählertausch, Reset nach Neustart) |

Wichtig: Diese Markierungen schließen sich **nicht gegenseitig aus**. Ein
und derselbe Wert kann z. B. gleichzeitig als Ausreißer **und** als Teil
eines Duplikats markiert sein — die Auswahl **Markierung** über der Liste
wählt jeweils nur aus, welche Werte eine bestimmte Markierung tragen, sie
teilt die Liste nicht in getrennte, überschneidungsfreie Gruppen auf. Jeder
Eintrag der Auswahl nennt seine Trefferzahl im gewählten Zeitraum; Kategorien
ohne Treffer sind nicht wählbar.

## Charts

Die Chart-Übersicht listet alle gespeicherten Charts als Kacheln mit Suche,
Sortierung und Favoriten-Schalter (siehe
[Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)).
Jede Kachel nennt den Diagrammtyp, die Anzahl der Entitäten und den Zeitraum.
Enthält ein Chart Linien- und Balkenreihen zugleich — etwa eine Temperatur
neben einem Zähler —, werden beide Typen genannt.

Eigener Editor, erreichbar über **Charts** → neues Chart oder Bearbeiten
eines bestehenden (Kachelmenü ⋮):

- Beliebig viele Entitäten überlagern; unterschiedliche Einheiten erhalten
  automatisch getrennte Y-Achsen, sodass z. B. Temperatur und Luftfeuchte
  in einem Chart sinnvoll lesbar bleiben.
- **Auflösung** wählbar, inklusive "Automatisch" — dabei zeigt ein kleiner
  Hinweis direkt an, welche Auflösung das für den aktuell gewählten
  Zeitraum tatsächlich bedeutet (z. B. "≈ 1 Stunde"). Bei Zeitraum "Tag"
  steht zusätzlich die Auflösung "Tag" zur Verfügung: sie fasst den ganzen
  Tag zu einem einzigen Balken je Entität zusammen — praktisch, um z. B.
  Tages-Einspeisung und -Bezug als zwei nebeneinanderstehende Balken direkt
  zu vergleichen. Vergleichen, Rollierend und Dynamische Y-Achse sind
  bei dieser Auflösung deaktiviert, da sie für einen einzelnen
  Tages-Balken keine sinnvolle zusätzliche Aussage liefern.
- Punkte an/aus, Rohwerte, dynamische Y-Achse, Werte anzeigen,
  Nachkommastellen, Legenden-Statistik — dieselben Optionen wie in der
  Verlaufsansicht einer einzelnen Entität, hier aber je Chart konfiguriert
  statt je Entität; Nachkommastellen gilt dabei einheitlich für alle
  Entitäten des Charts. Alle Einstellungen werden mit dem Chart gespeichert
  und gelten dann auch für dessen Vorschau auf Dashboards.
- Bei ausschließlich Schalter-Entitäten (`switch`, `binary_sensor` u. Ä.)
  steht wie in der Verlaufsansicht ein **Zeitstrahl** zur Verfügung — hier
  als mehrzeilige Darstellung mit einer Zeile je Entität, sodass sich
  AN-Intervalle mehrerer Schalter direkt untereinander vergleichen lassen.
- Mehrere Entitäten lassen sich per Ziehen oder über Pfeil-Buttons neu
  anordnen — das bestimmt die Reihenfolge in Legende, Statistik-Anzeige und
  Farbzuordnung.
- Ein gespeichertes Chart zeigt beim Ansehen immer die aktuell verfügbaren
  Daten, kein eingefrorener Schnappschuss zum Speicherzeitpunkt.
- Die geöffnete Ansicht zeigt unter **Verwendet in** die Dashboards, auf denen
  das gespeicherte Chart als Kachel liegt, und verlinkt direkt dorthin.

## Vergleichstabellen

Die Tabellen-Übersicht listet alle gespeicherten Tabellen als Kacheln mit
Suche, Sortierung und Favoriten-Schalter (siehe
[Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren));
jede Kachel nennt die Anzahl ihrer Zeilen und Spalten.

Eigener Editor, erreichbar über **Tabellen** → neue Tabelle oder Bearbeiten
einer bestehenden (Kachelmenü ⋮):

- **Zeilen** sind Größen: eine einzelne Entität, eine Gruppe mehrerer
  Entitäten (wird zu einem Summenwert zusammengefasst), eine Formel, oder
  eine rein optische Trennlinie ohne eigene Daten.
- **Spalten** sind Zeiträume: frei benannt (z. B. "Heute", "Aug Vorjahr",
  "2026"), jeweils mit einem Zeitraum-Typ (Tag, Woche, Monat, Jahr …) und
  einem Versatz relativ zu heute (0 = aktuell, −1 = vorheriger, usw.). So
  lässt sich z. B. derselbe Monat über zwölf aufeinanderfolgende Jahre in
  zwölf Spalten nebeneinanderstellen. Die Beschriftung kann Platzhalter
  wie `{jahr}`, `{monat}`, `{quartal}` oder `{woche}` enthalten, die sich
  automatisch auf den jeweiligen Zeitraum der Spalte auflösen (Einfüge-
  Hilfe direkt im Beschriftungsfeld, mit Live-Vorschau des aufgelösten
  Werts). **Vorjahresvergleich** setzt den Versatz einer Spalte automatisch
  auf denselben Zeitraum ein Jahr zuvor (schaltjahrsicher). Steht neben einer
  vergangenen Spalte (Vortag, Vormonat, Vorjahr …) eine Spalte mit Versatz 0
  desselben Zeitraum-Typs, vergleicht die vergangene Spalte automatisch nur
  den bislang vergangenen Teil ihres Zeitraums ("Gleicher Zeitpunkt"-
  Vergleich) — ein noch laufender Tag wird so fair gegen "Vortag bis zur
  aktuellen Uhrzeit" statt gegen den kompletten Vortag verglichen.
- **Mehrstufige Kopfzeile:** Spalten mit derselben, nicht leeren
  Gruppen-Beschriftung (z. B. "2025" über mehreren Monatsspalten) bekommen
  automatisch eine gemeinsame, übergreifende Kopfzeile darüber.
- Spalten und Zeilen lassen sich über das jeweilige Kärtchen duplizieren
  (⧉) — Zeilen-Duplikate inklusive aller Optionen, Formel-Zeilen mit
  automatisch mitkorrigierten Buchstaben-Referenzen.

### Aggregation und Formatierung

- **Aggregation je Zeile:** Automatisch (bei Zählern die Summe, sonst der
  Durchschnitt), Ø Durchschnitt, Min, Max oder Σ Summe. Min/Max nutzen dabei
  die echten Extremwerte der zugrunde liegenden Rohdaten, nicht den
  Durchschnitt der kleinsten verfügbaren Zeitscheibe.
- **Nachkommastellen je Spalte:** Automatisch oder fest 0–3.
- **% Anteil** (Zeilen-Menü "Optionen"): zeigt statt des absoluten Werts den
  prozentualen Anteil an der Summe aller Entität-/Gruppen-Zeilen derselben
  Spalte seit der letzten Trennlinie.
- **Bei 0 ausblenden** (Zeilen-Menü "Optionen"): blendet eine Entität-/
  Gruppen-Zeile automatisch aus, sobald sie in allen sichtbaren Spalten
  entweder keinen Wert oder 0 hat — etwa ein stillgelegtes Gerät, ohne sie
  manuell aus- und wieder einblenden zu müssen.
- **Summenzeile** (eigener Zeilentyp): Summe oder Durchschnitt aller
  Entität-/Gruppen-Zeilen seit der letzten Trennlinie, aktualisiert sich
  automatisch, wenn darüber Zeilen hinzukommen oder wegfallen.
- **Farbskala** (Spalten-Option): färbt die Zellen einer Spalte nach ihrem
  Wert relativ zu den anderen Entität-/Gruppen-Zeilen im selben Abschnitt
  derselben Spalte ein — heller bei niedrigen, kräftiger bei hohen Werten.
  Formel-, Summen- und Trennzeilen werden dabei weder eingefärbt noch für
  die Skala berücksichtigt.

### Formeln

Formel-Zeilen referenzieren andere Zeilen über ihr Buchstaben-Kürzel (A, B,
C …), z. B. `A / B * 100`. Referenzierbar sind dabei nur Zeilen *oberhalb*
der Formel-Zeile. Beim Umsortieren von Zeilen (Ziehen oder Pfeil-Buttons)
werden die Buchstaben-Referenzen in bestehenden Formeln automatisch
mitkorrigiert, sodass eine Formel weiterhin dieselbe fachliche Zeile
referenziert wie vor dem Verschieben — nicht einfach dieselbe Position. Eine
Formel-Zeile übernimmt, sofern nicht eigens angegeben, automatisch die
Einheit der ersten referenzierten Zeile.

### Darstellung

Rein optische Einstellungen, wirken sich nie auf die berechneten Werte aus:

- **Hervorhebung:** Zebra-Streifen, erste Spalte hervorheben, Header
  hervorheben, Beschriftung fett.
- **Vergleich:** Vergleichsspalten (Vortag, Vormonat, Vorjahr …) optisch
  absetzen, prozentuale Abweichung zur zugehörigen Vergleichsspalte
  anzeigen.
- **Zahlen / Einheiten:** Einheiten ein-/ausblenden, in einer festen Spalte
  ausrichten oder kleiner darstellen, Dezimaltrennzeichen spaltenweise
  ausrichten, fehlende Werte als „Keine Daten“ statt als Gedankenstrich
  ausschreiben.
- **Layout:** Rahmen (horizontal/Gitter/ohne), Dichte (komfortabel/
  kompakt), Header-/Werte-Ausrichtung (linksbündig/zentriert/rechtsbündig,
  Vorgabe jeweils rechtsbündig), alle Werte-Spalten gleich breit
  ("Spalten gleichmäßig", die Beschriftungsspalte bleibt davon unberührt).
  **Erste Spalte fixieren** und **Header fixieren** halten Beschriftungsspalte
  bzw. Kopfzeile beim Scrollen sichtbar — Header fixieren begrenzt die
  Vorschau/Kachel dafür auf eine feste Höhe mit eigenem Scrollbalken.
  Spaltenbreiten lassen sich per Ziehgriff am rechten Rand jeder Kopfzelle
  anpassen (Doppelklick setzt eine Spalte auf automatische Breite zurück);
  ohne manuelle Breite richtet sich jede Spalte nach ihrem Inhalt.

Der Button **CSV** exportiert die aktuell sichtbaren Zeilen/Spalten (inkl.
% Anteil-/Einheiten-Einstellungen) als Semikolon-getrennte Datei.

Gespeicherte Tabellen zeigen beim Ansehen immer aktuelle Werte — wie
Charts, kein eingefrorener Schnappschuss zum Speicherzeitpunkt.
Unter **Verwendet in** sind die Dashboards, auf denen die Tabelle als Kachel
liegt, direkt erreichbar.

## Energiedashboard

Eigenständige Ansicht (kein Eintrag im normalen Dashboard-System), die den
Energiefluss eines Haushalts als Sankey-Diagramm zeigt: von Netzbezug und
Erzeugern über einen zentralen Knoten zu Verbrauchern, Speichern und
Einspeisung. Sie wird über eine feste Kachel oben auf der
Dashboard-Übersicht ein- und ausgeschaltet und ist danach auch im Menü
**Dashboards** erreichbar.

### Einrichtung

Beim ersten Aktivieren (und später jederzeit über den Stift neben dem Titel,
**„Rollen bearbeiten"**) zeigt die Rollenzuordnung jede mögliche Rolle als
eigene Kachel: Netzbezug, Einspeisung, beliebig viele Erzeuger, beliebig
viele Speicher, beliebig viele Verbraucher, Kosten, PV-Ertragsprognose und
CO₂. Ein Klick auf eine Kachel öffnet ein Popup mit den zugehörigen Feldern;
bei Erzeuger/Speicher/Verbraucher legt die **„+"**-Kachel eine neue Zeile an,
der Ziehgriff (⠿) sortiert bestehende Zeilen um. Eingaben in einem Popup
gelten erst nach Klick auf **„Übernehmen"** — ein versehentlich geöffnetes
Popup lässt sich also gefahrlos wieder schließen, ohne etwas zu verändern.
Endgültig gespeichert wird die gesamte Zuordnung erst mit **„Speichern"** am
Seitenende.

Der Bereich **„Allgemein"** legt zusätzlich den Namen des zentralen Knotens
fest (Standard „Haus"), die Schwelle für die Auffälligkeiten-Markierung
(siehe [unten](#status-datenqualität-und-auffälligkeiten)) sowie **„Sichtbare
Kacheln"** — welche der optionalen Karten (Autarkie & Speicher,
Verbraucheranteile, Kostenanalyse, CO₂-Bilanz, Tageslastprofil, Bilanz &
Datenqualität) überhaupt angezeigt werden. Der Energiefluss selbst lässt
sich nicht abschalten.

### Benötigte und sinnvolle Entitäten

Die Rollenzuordnung wählt ausschließlich aus bereits archivierten Entitäten
aus — für das Energiedashboard muss also vorher nichts zusätzlich
eingerichtet werden, was nicht ohnehin schon in Zeitarchiv ankommt.

| Rolle | Pflicht? | Erwarteter Wert |
| --- | --- | --- |
| Netzbezug | **ja** | Zählerstand Strombezug aus dem Netz (kWh, aufsteigend) |
| Einspeisung | nein | Zählerstand Netzeinspeisung (kWh, aufsteigend) |
| Erzeuger (beliebig viele) | nein | je ein Ertragszähler (kWh, aufsteigend) mit eigenem Namen — z. B. Dachanlage und Balkonkraftwerk getrennt geführt |
| Speicher: Laden / Entladen (beliebig viele Speicher) | nein | je zwei Zählerstände (kWh, aufsteigend) — Werte über mehrere Speicher hinweg werden addiert |
| Speicher: Ladezustand (SOC) | nein | Momentanwert in Prozent, kein Zähler — bei mehreren Speichern kapazitätsgewichtet gemittelt |
| Speicher: Kapazität | nein | Gesamtkapazität in kWh (Entität oder fester Wert; Wh-Entitäten werden automatisch umgerechnet) — nur nötig, damit der Ladezustand zusätzlich in kWh angezeigt und bei mehreren Speichern richtig gewichtet wird |
| Verbraucher (beliebig viele) | nein | je ein Verbrauchszähler (kWh, aufsteigend) mit eigenem Namen und optional einer frei benannten Gruppe — alles nicht einzeln zugeordnete bleibt automatisch als „Grundlast“ sichtbar |
| Strompreis (Bezug/Einspeisung) | nein | €/kWh-Entität; ohne passende Entität ersatzweise ein fester Cent-Betrag |
| CO₂-Intensität | nein | g/kWh-Entität; ohne passende Entität ersatzweise ein fester Wert |
| PV-Ertragsprognose | nein | kWh für „Rest heute“ und „morgen“, z. B. aus einer Forecast.Solar-Integration |

Einzig Netzbezug ist Pflicht — alle anderen Rollen schalten lediglich
zusätzliche Kacheln, Ringe oder Badges frei; ohne Speicher-Rolle bleiben
z. B. einfach die Speicher-Kacheln und der Wirkungsgrad-Ring ausgeblendet.
Die Auswahlfelder zeigen dabei von vornherein nur Entitäten mit passender
Einheit bzw. Zähler-Typ für die jeweilige Rolle.

Für Netzbezug, Einspeisung, Erzeuger, Speicher (Laden/Entladen) und
Verbraucher wird ein **kWh-Gesamtzähler** erwartet (Home-Assistant-Gerätetyp
`total_increasing`), keine Momentanleistung in Watt — viele Geräte-
Integrationen bieten beides parallel an, hier zählt jeweils die
kWh-Zähler-Entität, nicht die Watt-Entität. Speicher-SOC, Speicher-Kapazität,
Strompreis, CO₂-Intensität und PV-Prognose sind dagegen bewusst
Momentan-/Messwerte (`measurement`), keine Zähler.

### Energiefluss und Verbraucher-Gruppen

Der Sankey zeigt Quellen (Netzbezug, Erzeuger, Speicherentladung) links,
Senken (Verbraucher, Speicherladung, Einspeisung) rechts, dazwischen den
zentralen Knoten. Der Rest — Netzbezug plus Erzeugung minus Verbraucher
minus Einspeisung minus Speicherladung — erscheint automatisch als
**„Grundlast“**, ohne eigenen Sensor. Navigation läuft wie bei Charts über
Stunde/Tag/Monat/Jahr mit Vor-/Zurück.

Ein Verbraucher mit zugewiesener Gruppe hängt im Sankey zweistufig am
zentralen Knoten (Knoten → Gruppe → Gerät), ein ungruppierter direkt daran
wie ein Erzeuger — hält den Fluss bei vielen einzelnen Verbrauchern
übersichtlich. Gruppen entstehen direkt beim Zuordnen eines Verbrauchers
(bestehende auswählen oder per Freitext eine neue anlegen) oder lassen sich
über den eigenen **„Gruppen"**-Button neben der Verbraucher-Überschrift
zentral verwalten (umbenennen, löschen — betroffene Verbraucher werden dabei
nur wieder gruppenlos, ihre Werte bleiben unverändert).

### Kennzahlen, Ringe und Badges

Direkt unter dem Sankey stehen fünf KPI-Kacheln (Erzeugung, Verbrauch,
Netzbezug, Speicher, Einspeisung) für den gewählten Zeitraum; bei mehreren
Speichern oder Erzeugern zeigt ihr Tooltip zusätzlich die Aufschlüsselung je
Gerät. Ein Klick auf eine Kachel führt zum Chart der zugrundeliegenden
Entität, mit demselben Zeitraum, der gerade im Energiedashboard eingestellt
ist; steckt mehr als eine Entität dahinter (mehrere Erzeuger/Verbraucher,
oder ein Speicher mit getrennter Lade-/Entlade-Entität), öffnet sich
stattdessen ein kurzes Auswahlfenster. Der Link „← zurück zum
Energiedashboard“ auf der Entitätsseite (ebenso der aus dem Energiebericht,
siehe unten) führt wieder genau zu diesem Zeitraum zurück, nicht zur
Standardansicht. Die Karte **„Autarkie & Speicher"** darunter zeigt vier
Ringe — Autarkie, Eigenverbrauch, Speicher-Ladezustand und
Speicher-Wirkungsgrad (bei mehreren Speichern jeweils kapazitätsgewichtet
zusammengefasst, damit ein leerer und ein voller Speicher nicht fälschlich
als „50 %“ erscheinen). Ein Klick auf einen Ring öffnet dessen Monatstrend
der letzten drei Kalenderjahre.

Optionale Badges im Kopfbereich fassen die CO₂-Bilanz (🌱) und den
Kosten-Saldo (💰) zusammen — je ein Klick öffnet die Details, darin jeweils
zusätzlich eine dritte Kachel „Bilanz“ (CO₂-Ausstoß minus vermieden) bzw.
der bereits bekannte Saldo, beide farblich hervorgehoben: steht die Bilanz
im Plus (mehr vermieden bzw. erlöst als verursacht bzw. bezahlt), wird das
eigens mit Stern und kurzem Hinweistext gefeiert statt nur als Zahl gezeigt.
Eine Kennzahlen-Leiste über dem Sankey bündelt zusätzlich Autarkie,
vermiedenes CO₂, Kosten-Saldo und die PV-Ertragsprognose für „heute“ und
„morgen“ auf einen Blick.

### Status, Datenqualität und Auffälligkeiten

Der **„Status"**-Chip (✓ bzw. ! bei Problemen) öffnet ein Popup mit der
Bilanzprüfung sowie den übrigen Datenqualitäts-Checks: veraltete
Sensorwerte, Zählerrücksetzungen, falsche Einheit, falscher Zähler-Typ und
doppelt zugeordnete Entitäten. Dasselbe Popup listet Auffälligkeiten —
Verbraucher oder Gruppen, die deutlich über ihrem Schnitt der letzten
Perioden liegen. Die Schwelle dafür (Standard +50 %) lässt sich in der
Rollenzuordnung unter **„Allgemein"** anpassen oder ganz abschalten.

### Tageslastprofil

Zeigt bei Tag/Stunde den stündlichen Verbrauch der letzten 7 Kalendertage;
bei Monat/Jahr stattdessen den nach Wochentag gemittelten Verbrauch (Mo–So)
über den gewählten Zeitraum, sodass erkennbar wird, an welchen Wochentagen
typischerweise mehr verbraucht wird.

### Energiebericht

Ein Symbol neben der Zeitraum-Navigation (nur bei Monat/Jahr aktiv) öffnet
einen druckoptimierten Bericht für den gerade gewählten Zeitraum —
Kennzahlen samt Vorjahres-/Vormonatsvergleich, Kosten- und CO₂-Bilanz (inkl.
CO₂-Vergleich als Autofahrt-Strecke), Verbraucheranteile inklusive
Kosten je Verbraucher, bei Jahresberichten zusätzlich ein Monatsverlauf und
alle Auffälligkeiten des Jahres. Ein Link oben führt jederzeit zurück zur
normalen Ansicht. Die Seite selbst erzeugt
kein neues Dateiformat und keine Bibliothek läuft im Hintergrund — der
Button **„Drucken / Als PDF speichern"** ruft lediglich den Druckdialog des
Browsers auf, dort lässt sich wie gewohnt „Als PDF speichern“ statt eines
echten Druckers wählen. Es gibt keinen automatischen Versand per E-Mail —
der Bericht bleibt, wie alles in Zeitarchiv, ausschließlich lokal.

### Aufbewahrung richtig einstellen

Die je Entität eingestellte [Aufbewahrungsfrist](#aufbewahrung-retention)
wirkt sich unterschiedlich stark auf das Energiedashboard aus — nicht jede
Rolle braucht dieselbe Frist:

- **Netzbezug, Einspeisung, Erzeuger, Speicher (Laden/Entladen/SOC) und
  Verbraucher** sollten großzügig aufbewahrt werden — mindestens
  **2 Jahre**, im Zweifel **Unbegrenzt**. Die Autarkie-, Eigenverbrauchs-,
  SOC- und Wirkungsgrad-Trends im Ring-Popup werten jeweils die letzten drei
  Kalenderjahre aus (ebenso der Monatsverlauf im Energiebericht); eine
  kürzere Frist lässt diese Trends mit der Zeit lückenhaft werden.
- **Strompreis- und CO₂-Entitäten** (falls über eine Entität statt eines
  festen Werts eingebunden) werden je angezeigtem Zeitraum-Bucket
  eingerechnet. Fehlen dafür Werte, weil die Aufbewahrungsfrist sie
  inzwischen entfernt hat, fällt die Kosten-/CO₂-Bilanz für diesen
  vergangenen Zeitraum lediglich kleiner aus — kein Fehler, nur eine
  unvollständige Auswertung. Wer hauptsächlich aktuelle bis wenige Monate
  alte Auswertungen braucht, kommt hier mit **90 Tage** oder **365 Tage**
  aus und spart Speicherplatz: dynamische Tarife und CO₂-Signale
  aktualisieren sich oft im Minutentakt und wachsen entsprechend schnell.
- **Speicher-Kapazitäts- und PV-Ertragsprognose-Entitäten** werden
  ausschließlich als aktueller Wert gelesen — unabhängig vom gerade
  angezeigten Zeitraum wird nie ein archivierter, alter Wert benötigt. Hier
  genügt die kürzeste verfügbare Frist (**30 Tage**); mehr Aufbewahrung
  bringt für diese Rollen keinen Vorteil, kostet bei häufig aktualisierenden
  Quellen aber unnötig Speicherplatz.

## Statistik

Zeigt Entitätenzahl, Datensätze, Speicherbedarf und Wachstum über die Zeit,
sowie Aufschlüsselungen nach Typ, Auflösung und Aufbewahrung. Ein interner
Planer erfasst unabhängig von Seitenaufrufen höchstens stündlich einen
realen Bestandsschnappschuss, sodass die Wachstumsansicht auch ohne
regelmäßigen Besuch der Seite aussagekräftig bleibt.

Die Kachelreihe oben nennt neben dem Bestand auch den **Zuwachs**: „Neue
Datensätze" zählt die letzten 24 Stunden, „Ø/Stunde" und „Ø/Tag" geben
dieselbe Messung als Durchschnitt über 24 Stunden bzw. sieben Tage. „Neue
Datensätze" und „Ø/Tag" tragen dieselbe Einheit — liegt der eine deutlich
unter dem anderen, war der letzte Tag ruhiger als die Woche davor (oder
umgekehrt). Alle drei stammen aus denselben Bestandsschnappschüssen, es gibt
also kein eigenes Ereignisprotokoll dafür; solange weniger als 24 Stunden
Verlauf vorliegen, steht dort ein Strich.

Alle Tabellen lassen sich durch Anklicken ihrer Spaltenüberschriften wie die
Entitätenliste sortieren. Das Wachstumsdiagramm passt seine beiden Y-Achsen
dynamisch an den jeweils sichtbaren Wertebereich an.

In der Speichernutzung führt **Index** zu einer Detailseite. Sie schlüsselt
auf, welche SQLite-Tabellen Entitätsmetadaten, Schreibsicherheit und
Bereinigung, Charts/Tabellen/Dashboards, Statistikverläufe sowie Einstellungen
und Wartungshistorien enthalten. Pro Tabelle und Bereich werden Eintragszahl,
belegte Datenseiten, zugehörige SQLite-Indizes und deren Gesamtgröße angezeigt.
Interne Strukturen und freie SQLite-Seiten bleiben separat ausgewiesen. Die
eigentlichen Messreihen liegen weiterhin in Hot Buffer, Archiv und Rollups,
nicht im Index.

Die Indexdetailseite zeigt außerdem den vollständig freien, durch eine
Kompaktierung reclaimbaren Speicher. SQLite verwendet diese Seiten im
laufenden Betrieb automatisch wieder. Eine manuelle **Index optimieren**-
Aktion schreibt die Datenbankdatei kompakt neu; währenddessen pausieren
Schreibzugriffe kurzzeitig. Eine Empfehlung erscheint erst bei einer
Indexgröße ab 50 MB, mindestens 10 MB reclaimbarem Speicher und mindestens
25 % freien Seiten. In diesem Fall wird auch der Index in der
Speichernutzung mit **Optimierung empfohlen** markiert. Vor der Ausführung
prüft Zeitarchiv den freien Plattenplatz und danach die SQLite-Integrität;
eine automatische Optimierung findet nicht statt.

Während der Optimierung wartet Zeitarchiv zunächst, bis bereits laufende
Schreibvorgänge abgeschlossen sind. Neue Übertragungen der Home-Assistant-
Integration pausieren an der Wartungssperre. Dauert die Optimierung länger als
der HTTP-Timeout, behält die Integration den betroffenen Batch und versucht ihn
ohne festes Retry-Limit erneut. Stabile Ereignis-IDs sorgen dafür, dass ein
erneut gesendeter oder teilweise bereits verarbeiteter Batch keine doppelten
Messwerte erzeugt. Im normalen Betrieb gehen durch die Optimierung daher keine
Werte verloren.

Die Integrationswarteschlange liegt allerdings nur im Arbeitsspeicher und ist
auf 5.000 neue Ereignisse begrenzt. Wird sie während eines außergewöhnlich
langen Rückstaus voll, werden weitere neue Ereignisse verworfen; ein Neustart
von Home Assistant oder der Integration verwirft ebenfalls noch nicht
übertragene Werte. Queue-Größe und verworfene Ereignisse sind auf der
Geräteseite der Integration unter **Diagnose** sichtbar.

Die Speicherplatz-Aufschlüsselung verlinkt direkt zu Import-Reports und
Backups, da auch diese Speicherplatz belegen, aber in der reinen
Entitäten-Statistik nicht enthalten sind.

## Housekeeping

Eigener Menüpunkt unter **System**, unterhalb Statistik — sammelt an einer
Stelle, was sonst leicht übersehen wird, mit derselben seitlichen Navigation
wie die Einstellungen:

| Bereich | Zeigt |
| --- | --- |
| **Inaktive Entitäten** | Entitäten ohne neuen Wert seit einem wählbaren Schwellwert (1 bis 30 Tage). Nie empfangene Entitäten erscheinen unabhängig vom Schwellwert immer. Meist harmlos (Standby, seltener Sensor), aber ein früher Hinweis auf eine tote Integration oder eine umbenannte/entfernte HA-Entität. |
| **Duplikate** | Archivweit erkannte doppelte Zeitstempel der letzten 30 Tage, je Entität — derselbe stündliche Hintergrund-Schnappschuss, der auch die Meldung „Duplikate gefunden" auslöst. Entfernbar über „Duplikate automatisch entfernen" auf der jeweiligen Bereinigungs-Seite. |
| **Ausreißer** | Entitäten, bei denen die eingestellte Ausreißer-Schwelle mehr als 1 % ihrer Werte markiert — mit Schwelle, absoluter Zahl und Quote. Dann ist die Schwelle für dieses Signal zu eng: markiert wird nicht mehr das Unplausible, sondern normales Verhalten. Es sind dieselben Zahlen, die unter dem Schwellenfeld der jeweiligen Entität stehen (siehe [Entität konfigurieren](#entität-konfigurieren)); die Liste rechnet nichts eigenes. Keine Sammel-Korrektur — die passende Schwelle hängt am Signal. |
| **Konfiguration** | Entitäten, deren Lücken-Erkennung strukturell nie zutreffen kann, weil die gewählte Auflösung oder der aktive Wertänderungsfilter selbst schon einen größeren Mindestabstand zwischen Werten erzwingt (siehe [Entität konfigurieren](#entität-konfigurieren)) — mit Auflösung, aktueller und empfohlener Lücken-Erkennung je Entität. Rein informativ, keine Sammel-Korrektur: der passende Zielwert unterscheidet sich je Entität. |
| **Speicherplatz** | Freier Speicherplatz auf dem Host-Dateisystem (Kachel mit Auslastungsbalken — andere Frage als die Zahlen unten, nicht Zeitarchivs eigener Speicherverbrauch); Indexkonsistenz prüfen/reparieren; markierte Datensätze endgültig aus Hot Buffer und Archiv entfernen (siehe [Bereinigung](#bereinigung)). |
| **Aufbewahrung** | Übersicht aktuell fälliger und bereits gelöschter Datensätze; Vorschau fälliger Löschungen; Zeitplan für automatische Durchsetzung (täglich oder wöchentlich mit Wochentag); Lauf-Historie. |
| **Rotation** | Entitäten mit noch nicht archiviertem Vormonat (passiert normalerweise automatisch beim nächsten empfangenen Wert) — bei Bedarf manuell nachziehbar, z. B. wenn eine Entität längere Zeit keine Werte mehr gesendet hat. |
| **Ungenutzte Elemente** | Charts und Vergleichstabellen, die in keinem Dashboard angepinnt sind — direkt öffnen oder löschen. Verschwindet automatisch aus der Liste, sobald irgendwo angepinnt. |

Jeder Bereich verlinkt aus der passenden Systemmeldung (siehe unten), falls
gerade etwas ansteht — Housekeeping selbst muss dafür nicht regelmäßig
aufgesucht werden.

**Woher die Ausreißer-Quoten kommen:** Sie zu ermitteln heißt, die komplette
Historie einer Entität zu lesen — für alle Entitäten auf einmal wäre das bei
jedem Seitenaufruf zu teuer. Zeitarchiv rechnet deshalb im Hintergrund eine
Entität nach der anderen durch und frischt jede etwa alle sechs Stunden auf.
Unter der Liste steht, wie weit das ist („4 von 5 Entitäten … gemessen"); wer
nicht warten will, findet auf der Konfigurationsseite der Entität den Knopf
**Jetzt prüfen**. Eine leere Liste bei noch offenen Messungen heißt also „bis
hierhin nichts gefunden", nicht „alles geprüft" — deshalb nennt der Abschnitt
auch dann den höchsten bisher gemessenen Wert.

### Was gerade läuft

Manche Aktionen dauern spürbar: ein Symcon-Probelauf über ein paar hundert
Variablen braucht Minuten, eine Bereinigung über viele Jahre Historie
Sekunden bis Minuten. Damit dabei nie unklar ist, ob noch etwas passiert,
zeigen sie ihren Stand — je nachdem, wo sie ausgelöst werden — an bis zu drei
Stellen:

- **Am Knopf.** Er wird für die Dauer der Anfrage blass, bekommt einen kleinen
  rotierenden Ring und lässt sich nicht ein zweites Mal drücken. Dauert es
  länger als drei Sekunden, zählt im Knopf zusätzlich eine Uhr mit.
- **Unter dem Knopf.** Wo es eine ehrliche Zahl gibt (importierte Zeilen,
  geprüfte Variablen, bereinigte Monate), erscheint ein Fortschrittsbalken mit
  „X von Y". Gibt es keine verlässliche Gesamtzahl, zeigt die Anzeige bewusst
  nur die bisher erreichte Zahl, statt eine Prozentangabe zu schätzen — und wo
  es überhaupt nichts zu zählen gibt (Indexprüfung, Rotation), bleibt es beim
  Knopf.
- **An der Glocke.** Ein zweites, farbiges Abzeichen **links** an der Glocke
  (das rote rechts bleibt den Problemen vorbehalten), und im Panel darüber
  der Abschnitt **Läuft gerade** mit Vorgang, aktuellem Schritt und, wo
  vorhanden, Balken. Es blitzt kurz auf, wenn ein Vorgang beginnt oder fertig
  wird — nicht dauerhaft. Laufen mehrere gleichzeitig, steht ihre Anzahl im
  Abzeichen.

Die Glocke ist dabei die verlässlichste Stelle: Nur sie kennt alle zwölf
Vorgänge, und die laufen im Hintergrund weiter, auch wenn die Seite
gewechselt oder der Tab geschlossen wird. Und einige
von ihnen — Backup, Bereinigung, Aufbewahrung, Rotation, Index-Optimierung —
pausieren für ihre Dauer alle anderen Schreibzugriffe, einschließlich der
Aufnahme aus Home Assistant. Reagiert Zeitarchiv scheinbar grundlos träge,
steht die Erklärung dort.

Drei dieser Vorgänge starten von selbst, ohne dass jemand etwas gedrückt hat:
die Speicherindex-Prüfung kurz nach dem Start der App, der nachträgliche
Aufbau der Stunden-Auswertung nach einer Änderung im Energiedashboard, und
die Neuberechnung der Aggregate, wenn Home Assistant für eine Entität
plötzlich einen anderen Messwerttyp liefert. Auch sie stehen in der Liste.

### Systemmeldungen

Das Meldungs-Center (Glocke in der Kopfzeile) sammelt Hinweise, die
automatisch verschwinden, sobald ihre Ursache behoben ist — kein eigener
Erledigt-Status nötig. Neben Update-Verfügbarkeit, empfohlener
Index-Optimierung und fehlgeschlagenen Backup-/Aufbewahrung-Läufen prüft
Zeitarchiv unter anderem:

- Speicherindex-Prüfung unvollständig oder mit gefundenen (meist bereits
  automatisch reparierten) Abweichungen
- Wartungsplaner oder Speicherindex-Hintergrundabgleich reagiert länger
  als 5 Minuten nicht mehr (Selbstheilungs-Schutz)
- Kein automatischer Backup-Zeitplan aktiv
- Aufbewahrung für Entitäten konfiguriert, aber die automatische Durchsetzung
  ausgeschaltet
- Letzter Import fehlgeschlagen oder nur teilweise abgeschlossen
- Endgültige Bereinigung möglich, Duplikate gefunden, Rotation ausstehend
- Inaktive Entitäten, dreistufig nach Alter (1/3/7 Tage, mit steigendem
  Schweregrad)
- Lücken-Erkennung einer Entität kann durch ihre Auflösung oder den aktiven
  Wertänderungsfilter strukturell nie zutreffen (siehe
  [Housekeeping → Konfiguration](#housekeeping) und
  [Entität konfigurieren](#entität-konfigurieren))
- Ausreißer-Erkennung markiert bei mindestens einer Entität mehr als 1 % aller
  Werte. Eine einzige Meldung für die ganze Installation, auch bei hundert
  betroffenen Entitäten: sie nennt die Anzahl und den deutlichsten Fall und
  führt zur Liste (siehe [Housekeeping → Ausreißer](#housekeeping)). Reine
  Auskunft, deshalb stummschaltbar
- Tageslastprofil im Energiedashboard wird nach einer Konfigurationsänderung
  noch rückwirkend vervollständigt
- Freier Speicherplatz auf dem Host-Dateisystem wird knapp (zweistufig:
  Warnung/kritisch) — andere Frage als Zeitarchivs eigener Speicherverbrauch
- Entpackte Import-Quelldaten liegen noch im Datenverzeichnis (ab 100 MB und
  frühestens einen Tag nach der letzten Änderung). Sie bleiben absichtlich
  liegen, damit sich Zuordnung und Probelauf ohne erneuten Upload wiederholen
  lassen — nach einem abgeschlossenen Import lassen sie sich unter **Import**
  mit „Daten löschen" entfernen. Reine Auskunft, deshalb stummschaltbar
- Verbundene Home-Assistant-Integration ist veraltet oder eine neuere Version
  ist verfügbar (getrennt nach Bugfix/Funktionsupdate)

Alle Meldungen außer echten Fehlern lassen sich über das 🔕-Icon
stummschalten (1 Stunde bis dauerhaft) — einsehbar und vorzeitig
zurückholbar unter **Einstellungen → Meldungen**.

### Tipps

Im Meldungs-Center rotiert außerdem ein kurzer Praxis-Tipp zu Funktionen der
App — 30 Tipps insgesamt, täglich wechselnd. Unter **Einstellungen →
Meldungen** lässt sich die Tipp-Anzeige komplett abschalten oder ein Dialog
mit allen Tipps und ihrem aktuellen Status öffnen; darin lässt sich der
gerade aktuelle Tipp für den Rest des Tages ausblenden, ohne die Rotation zu
unterbrechen.

## Import und Export

Erreichbar über **Import**, vier Reiter:

### Symcon

ZIP des `db`-Ordners hochladen, optional eine `settings.json` für Namen und
Einheiten ergänzen. Danach: Variablen prüfen und den gewünschten
Home-Assistant-Entitäten zuordnen. Weichen Quell- und Zieleinheit
voneinander ab (z. B. `klx` in Symcon vs. `lx` in Home Assistant), erscheint
ein Hinweis und ein Umrechnungsfaktor kann angegeben werden (hier `1000`).
Vor dem eigentlichen Import lässt sich die Zuordnung noch einmal prüfen.

Für bereits abgeschlossene Monate mit vorhandener Archivdatei gilt
Monatsgranularität: der ganze Monat wird übersprungen, auch wenn er
tatsächlich Lücken enthält — siehe „Duplikatschutz" weiter unten.

### CSV

Trennzeichen sowie Zeit-, Wert- und Zielspalte frei zuordnen, das Ergebnis
vor dem eigentlichen Import prüfen (Vorschau der ersten Zeilen mit erkannten
Werten).

### Home Assistant

Bestehende Recorder-Daten direkt aus der laufenden Home-Assistant-Instanz
übernehmen, ohne Symcon oder eine hochgeladene Datei. Zur Auswahl stehen nur
Entitäten, die bereits in Zeitarchiv bekannt sind — also von der
Home-Assistant-Integration konfiguriert wurden und mindestens einen
Live-Wert übertragen haben.

Der empfohlene **Vollimport** verbindet beide Quellen automatisch. Die
Einzelmodi bleiben für gezielte Importe verfügbar:

- **Vollimport:** ermittelt zuerst die tatsächlich verfügbare Rohhistorie und
  ergänzt davor die ältere Stundenstatistik. Die Schnittstelle wird
  für jede Entität einzeln auf die nächste volle Stunde ab dem ersten
  abgerufenen Rohwert gelegt — aber nur, wenn die Statistik dort lückenlos
  bis zu dieser Stunde reicht. Der letzte bekannte Rohzustand wird an dieser
  Grenze fortgeführt; Statistik-Buckets enden exakt davor. Besteht zwischen
  den beiden HA-Quellen selbst eine Lücke, wird nicht gerundet: die Grenze
  liegt dann exakt beim ersten verfügbaren Rohwert, damit Zeitarchiv diese
  Lücke nicht künstlich vergrößert. So erzeugt Zeitarchiv nie eine
  Überschneidung und vergrößert nie eine bestehende Lücke zwischen den
  Quellen.

- **Rohhistorie:** Einzelmesswerte über die Home-Assistant-REST-API. Home
  Assistant hält diese standardmäßig aber nur einige Tage vor, deckt also
  nur die jüngste Vergangenheit ab.
- **Langzeitstatistik:** von Home Assistant per Voreinstellung dauerhaft
  aufbewahrte Stunden-/Tagesaggregate (Mittelwert bzw. fortlaufende Summe,
  je nachdem was die Entität in Home Assistant führt) über die
  Home-Assistant-WebSocket-API — deckt damit auch deutlich ältere
  Zeiträume ab, allerdings nur als Aggregat statt als Einzelmesswert. Steht
  nur für Entitäten mit Home-Assistant-`state_class` zur Verfügung (i. d. R.
  `sensor.*`-Entitäten), erkennbar an der Markierung "Nicht unterstützt" in
  der Spalte "Art" bei allen anderen.

Ablauf: Importmodus und Zeitraum wählen (die verfügbaren Voreinstellungen unterscheiden
sich je nach Quelle — bei Langzeitstatistik steht z. B. zusätzlich "Letztes
Jahr" zur Verfügung), optional "Verfügbarkeit prüfen" für eine Vorschau,
welche Entitäten in Home Assistant tatsächlich Daten der gewählten Quelle
haben und für welchen Zeitraum. Geprüft werden dabei ausschließlich die
markierten Entitäten; unmarkierte Zeilen bleiben unverändert. Das
Prüfergebnis bleibt je Quelle/Auflösung erhalten — auch nach einem
Seitenwechsel oder einem Wechsel zwischen
Vollimport, Rohhistorie und Langzeitstatistik, bis zum nächsten Neustart des Add-ons.
Ein Status-Chip rechts neben "Verfügbarkeit prüfen" zeigt den laufenden
Prüfstatus und anschließend den Zeitpunkt der letzten Prüfung; ab 15 Minuten
erscheint ein Hinweis, dass der Stand veraltet sein könnte.

Beim Vollimport werden Roh- und Statistikzeitraum getrennt gewählt. Der Dry
Run weist je Entität beide verwendeten Bereiche, die berechnete Schnittstelle,
bewusst verworfene Übergangswerte und den fortgeführten Rohwert-Anker aus.
Entitäten ohne Langzeitstatistik werden weiterhin vollständig mit ihrer
verfügbaren Rohhistorie importiert; ein Ausfall einer Quelle verhindert nicht,
dass erfolgreich abgerufene Werte der anderen Quelle verarbeitet werden.

Der laufende Kalendermonat wird unabhängig vom bereits vorhandenen
Datenbestand immer automatisch in den Hot Buffer importiert. Ohne die Option
"Archivlücken füllen" wird ein bereits abgeschlossener Monat mit
vorhandener Archivdatei komplett übersprungen — wie bei Symcon und CSV
(siehe „Duplikatschutz" weiter unten). Erst mit aktivierter Option werden
solche Monate zeilenweise um fehlende Zeitstempel ergänzt; vorhandene
Zeitstempel und Werte bleiben dabei unverändert.

Nach einem Dry Run kann eine Debug-Datei als ZIP heruntergeladen werden. Sie
enthält alle für die Diagnose relevanten abgerufenen, übernommenen und
verworfenen Werte samt Gründen, Quellenbereichen und Schnittstelle,
Monatszuordnung, Importplan und aktuellem
Archiv-/Hot-Buffer-Zustand. Zugangstoken und Autorisierungsheader werden nicht
aufgenommen. Da der Export dennoch Messwerte und Entitätsmetadaten enthält,
sollte er nur gezielt weitergegeben werden.

Dieser Import benötigt die Add-on-Berechtigung `homeassistant_api` sowie
eine Home-Assistant-Installation mit Supervisor (steht bei Home Assistant
Container nicht zur Verfügung).

### Reports

Jeder tatsächlich ausgeführte Import (Symcon, CSV oder Home Assistant)
bleibt hier mit Quelle, Zuordnung, Laufzeit, importierten und
übersprungenen Datensätzen sowie eventuellen Fehlern nachvollziehbar. Reine
Vorschauen (z. B. "Verfügbarkeit prüfen") erzeugen keinen Report. Die Liste
lässt sich nach Quelle und Status filtern (wirkt sofort bei Auswahl) und
nach jeder Spalte sortieren; ein Klick auf eine Zeile öffnet die
Detailansicht mit JSON-Download. Reports sind seitenweise darstellbar und
lassen sich gesammelt löschen, wenn sie nicht mehr benötigt werden.
Home-Assistant-Reports unterscheiden beim Vollimport Rohhistorie und
Langzeitstatistik und weisen neu archivierte Werte, Ergänzungen des laufenden
Monats, gefüllte Archivlücken sowie aus einem unzulässigen aktuellen Archiv in
den Hot Buffer gerettete Werte getrennt aus.

### Duplikatschutz

Der laufende Kalendermonat landet immer im Hot Buffer und wird dabei
zeilenweise dedupliziert: nur Zeitstempel, die dort noch nicht vorhanden
sind, werden ergänzt.

Für bereits abgeschlossene Archivmonate hängt das Verhalten von der Quelle
ab. Bei **Symcon- und CSV-Import** gilt Monatsgranularität: existiert für
einen Monat bereits eine Archivdatei, wird der gesamte Monat übersprungen —
auch wenn er tatsächlich Lücken enthält. Eine nachträgliche Ergänzung ist
hier nicht möglich, dafür muss der Monat notfalls manuell gelöscht und neu
importiert werden. Beim **Home-Assistant-Import** lässt sich das mit der
Option "Archivlücken füllen" gezielt aufheben: dann werden auch
abgeschlossene Monate um fehlende Zeitstempel ergänzt.

In allen Fällen gilt: vorhandene Messpunkte derselben Entität und desselben
Zeitstempels werden übersprungen — auch bei abweichender Event-ID — und
niemals ersetzt. Ein erneuter Symcon- oder CSV-Upload derselben Quelle
dupliziert also nichts, ebenso wenig ein wiederholter Home-Assistant-Import
über denselben Zeitraum.

### CSV-Export

Von der Verlaufsansicht einer Entität aus: die vollständige
Rohdatenhistorie dieser einen Entität bis zum Exportlimit als CSV
herunterladen.

## Backup / Restore

Eigener Menüpunkt **System → Backup / Restore** (nicht unter Einstellungen):

- **Backup erstellen:** kompletter Datenbestand (Index, Hot Buffer,
  Monatsarchive, Rollups) als ZIP, direkt herunterladbar. Zusätzlich zu,
  nicht statt der automatischen Home-Assistant-Snapshots — ein
  Home-Assistant-Snapshot sichert den Add-on-Zustand als Ganzes, ein
  Zeitarchiv-Backup ist unabhängig davon portabel und lässt sich auch
  außerhalb von Home Assistant aufbewahren.
- **Vorhandenes Backup importieren:** eine Backup-ZIP-Datei per Ziehen &amp;
  Ablegen oder über den Dateidialog hochladen — z. B. eines, das von einer
  anderen Zeitarchiv-Installation stammt oder extern aufbewahrt wurde (Home-
  Assistant-Neuinstallation, Geräteumzug). Die Datei wird vollständig
  geprüft (Prüfsummen, ZIP-Struktur, Index-Integrität), bevor sie als
  wiederherstellbares Backup in der Liste erscheint — bei einer zu großen
  oder beschädigten Datei bleibt der bisherige Datenbestand unangetastet.
  Obergrenzen: 2 GiB Upload, 5 GiB entpackt.
- **Zeitplan:** automatisch nach Zeitplan (Intervall, Uhrzeit, ggf.
  Wochentag), mit automatischer Aufräumung älterer Backups nach Anzahl
  und/oder Alter, damit der Speicherplatz nicht unbegrenzt wächst.
- **Prüfen:** Prüfsummen-Check eines vorhandenen Backups, ohne es
  anzuwenden — sinnvoll, um die Integrität eines Backups vor einem
  tatsächlichen Wiederherstellungsbedarf zu bestätigen. Gilt gleichermaßen
  für selbst erstellte wie für importierte Backups.
- **Wiederherstellen:** ersetzt den aktuellen Datenbestand vollständig
  durch den Inhalt des gewählten Backups (selbst erstellt oder importiert).
  Der bisherige Stand wird vor dem Überschreiben in ein
  Rollback-Verzeichnis verschoben, nicht gelöscht — bei Bedarf lässt sich
  der Zustand vor der Wiederherstellung also zurückholen. Nach einer
  Wiederherstellung empfiehlt sich ein kurzer Blick auf **Statistik**, um
  zu prüfen, ob die erwarteten Entitäten und Datensatzmengen wieder
  vorhanden sind.

## Einstellungen im Detail

| Bereich | Enthält |
| --- | --- |
| **Darstellung** | Startseite (Übersicht/Energiedashboard), Farbschema (Zeitarchiv/Home Assistant/Modern), Hell/Dunkel/Automatisch, Schriftgröße, Dashboard-Kachel-Ein-/Ausblendanimation, Startwerte für die Chart-Optionen der Entität-Verlaufsansicht |
| **Archivierung** | Standardwerte für neu erkannte Entitäten (wirken nie rückwirkend auf bestehende Entitäten): Auflösung, Aufbewahrung, Nachkommastellen, Wertänderungsfilter, Lücken-/Ausreißer-Erkennung |
| **Meldungen** | Tipp-Anzeige an-/ausschalten und Dialog mit allen Tipps (siehe [Housekeeping](#housekeeping)); Übersicht stummgeschalteter Systemmeldungen mit verbleibender Dauer, einzeln vorzeitig wieder aktivierbar |
| **Verbindung** | API-Token anzeigen/neu erzeugen, letzter empfangener Wert, Anzahl Schreibzugriffe und Auth-Fehler seit Start, verbundene Integrationsversion mit Zeitpunkt "zuletzt gesehen" (Hinweis bei veralteter oder neu verfügbarer Version) |
| **Diagnose** | Nächsten Schreibvorgang einmalig vollständig aufzeichnen (sensible Rohdaten, automatische Löschung spätestens nach 60 Minuten); eine einzelne Entität 15 Minuten lang einschließlich Ingest-Ergebnis verfolgen; Diagnosebericht herunterladen; Prozess-Start und -Laufzeit; **Hintergrundprozesse**-Übersicht (letzter Lauf/Status jeder Wartungsplaner-Aufgabe) |
| **Über Zeitarchiv** | Version (mit Hinweis, sobald ein Update verfügbar ist), Zeitzone, Datenverzeichnis, Links zu Dokumentation/Changelog/Fehlermeldung |

Ein neu erzeugter API-Token unter **Verbindung** ersetzt den bisherigen
sofort — die Zeitarchiv-Integration muss danach mit dem neuen Token
aktualisiert werden, sonst schlagen weitere Schreibversuche fehl.

Anwendungs-Loglevel, HTTP-Zugriffsprotokollierung und die Logansicht selbst
sind keine Einstellungen-Sektion mehr, sondern liegen direkt auf der Seite
**Protokoll** (Home-Assistant-Seitenleiste bzw. Menü).

Für den Normalbetrieb sind `warning` und HTTP **Nur fehlgeschlagene Anfragen**
die empfohlenen Einstellungen. `debug` und der Entity-Trace sind zeitlich
begrenzt zur Fehlersuche gedacht. Die lokale Logquelle reagiert schnell und
enthält nur den begrenzten Puffer des laufenden Prozesses; die
Supervisor-Historie reicht weiter zurück und kann beim Laden etwas länger
dauern. Zugangsdaten werden vor der Ausgabe maskiert. Write-Captures und
Entity-Traces können trotzdem Entity-IDs und Messwerte enthalten und sollten
nur so lange wie nötig aktiv beziehungsweise gespeichert bleiben.

## Typische Aufgaben

**"Ein Sensor sendet unplausible Ausreißer."**
→ Entität öffnen → Zahnrad-Symbol → Ausreißer-Erkennung auf ein passendes
Vielfaches einstellen und an der Quote darunter ablesen, was das bewirkt
(bei Schaltern gibt es die Einstellung nicht, siehe oben) → zurück zur
Verlaufsansicht →
**Bereinigen** → erkannte Ausreißer prüfen und löschen (Soft-Delete,
rückgängig machbar) → **Housekeeping → Speicherplatz**, wenn der Platz
tatsächlich freigegeben werden soll.

**"Ich will Innen- und Außentemperatur über die letzten 12 Monate
vergleichen."**
→ **Tabellen** → neue Tabelle → 12 Spalten (Zeitraum-Typ "Monat", Versatz 0
bis −11) → zwei Zeilen (je eine Entität) → optional eine Formel-Zeile für
die Differenz.

**"Ein Dashboard auf einem Wandtablet soll sich nicht versehentlich
verändern."**
→ Dashboard öffnen → Editor → "Fixiert" aktivieren.

**"Ich möchte alte Symcon-Daten übernehmen, ohne HA-Live-Daten zu
verdoppeln."**
→ **Import → Symcon** → ZIP hochladen → Zuordnung prüfen → Import starten.
Bereits vorhandene Zeitstempel werden automatisch übersprungen, unabhängig
von der Quelle.

**"Ich nutze kein Symcon und möchte trotzdem die bisherige HA-Historie
übernehmen."**
→ **Import → Home Assistant** → Entitäten auswählen, optional
"Verfügbarkeit prüfen" → Vorschau (Dry Run) → Import starten.

**"Eine Entität sendet nicht mehr, ich will sie aber behalten."**
→ Entität einfach unverändert lassen — bereits archivierte Werte bleiben
erhalten, Charts und Tabellen zeigen weiterhin die vorhandene Historie.
Erst bei Bedarf über das Zahnrad-Symbol **Alle Werte löschen** oder
**Entität entfernen** verwenden.

**"Ich will vor einem größeren Eingriff (Import, Bereinigung, Update) auf
Nummer sicher gehen."**
→ **System → Backup / Restore** → Backup erstellen → herunterladen oder im
konfigurierten Zeitplan belassen.

## Häufige Fragen

**Wirkt sich eine geänderte Auflösung auf bereits gespeicherte Werte aus?**
Nein. Eine geänderte Auflösung wirkt ausschließlich auf künftig
eintreffende Werte, nie rückwirkend auf bereits archivierte Daten.

**Wirkt sich eine geänderte Aufbewahrungsfrist auf bereits gespeicherte
Werte aus?**
Ja, sobald die Aufbewahrungs-Durchsetzung als Nächstes läuft — anders als
bei der Auflösung ist das hier bewusst rückwirkend gewollt: eine verkürzte
Frist entfernt dann auch längst archivierte, überfällige Monate. Details
und wie man das sicher handhabt (Vorschau, Unbegrenzt, Backup vorher)
stehen unter [Datenhandling → Aufbewahrung](#aufbewahrung-retention).

**Ist eine gelöschte Entität wirklich weg?**
Nach "Alle Werte löschen" oder "Entität entfernen" ja, endgültig. Vorher
lohnt sich ein Backup (siehe oben), falls die Löschung ein Versehen war.

**Warum sieht ein Chart trotz aktiver Integration keine neuen Werte?**
Meist fehlt ein passender Archivfilter in der Integration (siehe
[Erste Schritte](#erste-schritte), Schritt 4), oder Token/Host in der
Integrationskonfiguration stimmen nicht mit **Einstellungen → Verbindung**
überein.

**Kann ich ein Chart oder eine Tabelle für mehrere Dashboards
verwenden?**
Ja — ein und dasselbe Chart oder dieselbe Tabelle lässt sich auf beliebig
vielen Dashboards anheften; es gibt jeweils nur eine gemeinsame
Definition, Änderungen wirken sich überall gleichzeitig aus.

**Was passiert mit einer Kachel, wenn das zugrunde liegende Chart oder
die Tabelle gelöscht wird?**
Die Kachel verschwindet von allen Dashboards, auf denen sie angeheftet
war.
