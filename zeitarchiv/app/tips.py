"""Inhalt und Rotationsreihenfolge für den rotierenden Tipp im Meldungs-
Center (siehe notices.py, _current_tip_notice) — bewusst als eigenes Modul,
getrennt von der Stummschalt-/Auswahl-Logik: diese Liste soll wachsen und
schrumpfen können, ohne notices.py anzufassen."""

from __future__ import annotations

TIPS = [
    {
        "slug": "dashboards_gruppieren",
        "title": "Tipp: Eigene Dashboards anlegen",
        "detail": "Gruppiere zusammengehörige Charts und Tabellen auf einem eigenen Dashboard, statt alles in der Standard-Übersicht zu sammeln.",
        "meta": "Dashboards",
        "link": "/dashboards",
    },
    {
        "slug": "chart_duplizieren",
        "title": "Tipp: Chart duplizieren statt neu bauen",
        "detail": "Über das ⋮-Menü einer Kachel lässt sich ein bestehendes Chart oder eine Tabelle als Ausgangspunkt für eine Variante duplizieren.",
        "meta": "Charts",
        "link": "/charts",
    },
    {
        "slug": "tageslastprofil_wochentag",
        "title": "Tipp: Tageslastprofil nach Wochentag",
        "detail": "Das Energiedashboard zeigt den typischen Tagesverlauf getrennt nach Wochentag — praktisch, um Wochenend- von Wochentag-Mustern zu unterscheiden.",
        "meta": "Energiedashboard",
        "link": "/energiedashboard",
    },
    {
        "slug": "eigene_anzeigenamen",
        "title": "Tipp: Eigene Anzeigenamen vergeben",
        "detail": "Entitäten lassen sich mit einem eigenen Namen versehen, unabhängig vom Home-Assistant-Friendly-Name — praktisch bei kryptischen Originalnamen.",
        "meta": "Entitäten",
        "link": "/entities",
    },
    {
        "slug": "housekeeping_aufraeumen",
        "title": "Tipp: Aufräumen leicht gemacht",
        "detail": "Ungenutzte Charts/Tabellen, Duplikate und inaktive Entitäten sammeln sich im Housekeeping-Bereich — an einer Stelle statt verstreut.",
        "meta": "Housekeeping",
        "link": "/housekeeping",
    },
    {
        "slug": "farbschema_wechseln",
        "title": "Tipp: Farbschema wechseln",
        "detail": "Neben Hell/Dunkel stehen drei Farbschemata zur Wahl (Zeitarchiv, Home Assistant, Modern) — unter Darstellung in den Einstellungen.",
        "meta": "Einstellungen",
        "link": "/settings#darstellung",
    },
    {
        "slug": "meldungen_stummschalten",
        "title": "Tipp: Meldungen zeitweise stummschalten",
        "detail": "Eine Empfehlung nicht relevant? Über das 🔕-Icon lässt sie sich für 1 Stunde bis dauerhaft stummschalten, statt sie einfach zu ignorieren.",
        "meta": "Meldungs-Center",
        "link": None,
    },
    {
        "slug": "automatische_backups",
        "title": "Tipp: Automatische Backups einrichten",
        "detail": "Ein Zeitplan für regelmäßige Sicherungen lässt sich einmal festlegen, statt manuell ans Backup zu denken.",
        "meta": "Backup",
        "link": "/backup",
    },
    {
        "slug": "datum_springen",
        "title": "Tipp: Direkt zu einem Datum springen",
        "detail": "Auf der Bereinigungs- und Entitäten-Detailseite lässt sich per Klick auf den Zeitraum-Titel direkt zu einem bestimmten Datum springen, statt sich vorzublättern.",
        "meta": "Entitäten",
        "link": None,
    },
    {
        "slug": "tabellen_sortieren",
        "title": "Tipp: Tabellen durch Klick sortieren",
        "detail": "Die meisten Tabellen in der App lassen sich durch Klick auf eine Spaltenüberschrift sortieren — auch ohne extra Sortier-Steuerung.",
        "meta": "Allgemein",
        "link": None,
    },
    {
        "slug": "ausreisser_luecken",
        "title": "Tipp: Ausreißer und Lücken erkennen",
        "detail": "Schwellwerte für Ausreißer- und Lücken-Erkennung lassen sich je Entität passend zum jeweiligen Sensor einstellen.",
        "meta": "Entitäten",
        "link": "/entities",
    },
    {
        "slug": "wiederholungen_verdichten",
        "title": "Tipp: Wiederholungen verdichten",
        "detail": "Aufeinanderfolgende, praktisch gleiche Werte lassen sich in der Bereinigung automatisch zu einem einzigen Datenpunkt zusammenfassen.",
        "meta": "Bereinigung",
        "link": None,
    },
    {
        "slug": "vorjahresvergleich_tabelle",
        "title": "Tipp: Vorjahresvergleich in einer Tabelle",
        "detail": "Eine Vergleichsspalte lässt sich als Vorjahreszeitraum statt als fester Zeitraum definieren — praktisch für Jahresvergleiche auf einen Blick.",
        "meta": "Tabellen",
        "link": "/tables",
    },
    {
        "slug": "aufbewahrung_je_entitaet",
        "title": "Tipp: Aufbewahrungsfrist je Entität",
        "detail": "Statt einer globalen Frist für alle Entitäten lässt sich die Aufbewahrung individuell je Entität festlegen, um Speicherplatz gezielt zu sparen.",
        "meta": "Aufbewahrung",
        "link": "/housekeeping#aufbewahrung",
    },
    {
        "slug": "entitaeten_favorisieren",
        "title": "Tipp: Entitäten favorisieren",
        "detail": "Ein Klick auf den Stern hebt eine Entität in der Übersicht nach oben — praktisch für die, die man am häufigsten braucht.",
        "meta": "Entitäten",
        "link": "/entities",
    },
    {
        "slug": "csv_export",
        "title": "Tipp: CSV-Export für externe Auswertung",
        "detail": "Rohdaten oder Aggregate lassen sich als CSV exportieren, um sie z. B. in einer Tabellenkalkulation weiterzuverarbeiten.",
        "meta": "Export",
        "link": "/export",
    },
    {
        "slug": "protokoll_durchsuchen",
        "title": "Tipp: Protokoll durchsuchen",
        "detail": "Das Protokoll aller Hintergrundaktionen lässt sich durchsuchen, statt sich chronologisch durchzuklicken.",
        "meta": "Protokoll",
        "link": "/logs",
    },
    {
        "slug": "wachstum_ueber_zeit",
        "title": "Tipp: Wachstum über Zeit im Blick",
        "detail": "Die Statistik-Seite zeigt, wie Datensätze und Speicherverbrauch sich über die Zeit entwickeln — praktisch, um Trends frühzeitig zu erkennen.",
        "meta": "Statistik",
        "link": "/statistik",
    },
    {
        "slug": "rauschen_filtern",
        "title": "Tipp: Rauschen in Messwerten filtern",
        "detail": "Ein Wertfilter lässt sich je Entität aktivieren, um kleine Schwankungen (Messrauschen) automatisch zu glätten.",
        "meta": "Entitäten",
        "link": None,
    },
    {
        "slug": "mehrere_entitaeten_chart",
        "title": "Tipp: Mehrere Entitäten in einem Chart vergleichen",
        "detail": "Ein Chart lässt sich über mehrere Entitäten hinweg anlegen — praktisch, um z. B. Bezug und Einspeisung nebeneinander zu sehen.",
        "meta": "Charts",
        "link": "/charts/new",
    },
    {
        "slug": "kacheln_anpassen",
        "title": "Tipp: Kacheln anpassen",
        "detail": "Kacheln auf einem Dashboard lassen sich per Ziehen neu anordnen und über den Größen-Picker im Kachelmenü in Spalten/Zeilen skalieren.",
        "meta": "Dashboards",
        "link": "/dashboards",
    },
    {
        "slug": "verwendet_in",
        "title": "Tipp: Wo wird ein Chart überall verwendet?",
        "detail": "Im Editor eines Charts oder einer Tabelle zeigt „Verwendet in“, auf welchen Dashboards die Kachel bereits angepinnt ist.",
        "meta": "Charts",
        "link": "/charts",
    },
    {
        "slug": "schriftgroesse_anpassen",
        "title": "Tipp: Schriftgröße anpassen",
        "detail": "Die Schriftgröße der gesamten App lässt sich in den Einstellungen in mehreren Stufen anpassen — praktisch für größere Bildschirme oder bessere Lesbarkeit.",
        "meta": "Einstellungen",
        "link": "/settings#darstellung",
    },
    {
        "slug": "zaehlerrueckgaenge",
        "title": "Tipp: Zählerrückgänge erkennen",
        "detail": "Bei Zähler-Entitäten (z. B. Stromzähler) lässt sich erkennen, wenn ein Wert unerwartet sinkt — oft ein Hinweis auf einen Zählertausch oder -reset.",
        "meta": "Bereinigung",
        "link": None,
    },
    {
        "slug": "vorperiode_vergleichen",
        "title": "Tipp: Mit der Vorperiode vergleichen",
        "detail": "Ein Chart lässt sich mit „Vergleichen“ gegen den vorherigen Zeitraum oder das Vorjahr überlagern, um Veränderungen direkt zu sehen.",
        "meta": "Charts",
        "link": "/charts/new",
    },
    {
        "slug": "fortlaufender_zeitraum",
        "title": "Tipp: Fortlaufender statt kalendarischer Zeitraum",
        "detail": "Ein Chart lässt sich wahlweise fortlaufend (z. B. „letzte 7 Tage“) oder kalendarisch ausgerichtet (z. B. „diese Woche“) anzeigen.",
        "meta": "Charts",
        "link": None,
    },
    {
        "slug": "diagrammtyp_waehlen",
        "title": "Tipp: Diagrammtyp wählen",
        "detail": "Linie, Balken oder Fläche lassen sich je Chart einzeln festlegen, statt sich auf die automatische Wahl zu verlassen.",
        "meta": "Charts",
        "link": None,
    },
    {
        "slug": "kennzahlen_legende",
        "title": "Tipp: Kennzahlen in der Legende",
        "detail": "Die Chart-Legende kann Summe, Durchschnitt, Minimum oder Maximum direkt neben dem Entitätsnamen anzeigen.",
        "meta": "Charts",
        "link": None,
    },
    {
        "slug": "duplikate_entfernen",
        "title": "Tipp: Duplikate automatisch entfernen",
        "detail": "Erkannte doppelte Zeitstempel lassen sich mit einem Klick automatisch bereinigen, statt sie einzeln durchzugehen.",
        "meta": "Bereinigung",
        "link": "/housekeeping#duplikate",
    },
    {
        "slug": "import_berichte",
        "title": "Tipp: Import-Vorgänge nachvollziehen",
        "detail": "Jeder Import erzeugt einen Bericht mit Status und Details — praktisch, um frühere Importe nachzuvollziehen oder Fehler zu prüfen.",
        "meta": "Import",
        "link": "/import?tab=reports",
    },
]


def rotation_order(ordinal: int, rotation_days: int = 4) -> list[dict]:
    """Alle Tipps, beginnend beim für "heute" fälligen (ordinal = Kalendertag-
    Ordnungszahl, siehe date.toordinal() — Kalendertag statt Sekunden-Epoch,
    damit der Wechsel an der lokalen Mitternacht passiert statt zu einer
    beliebigen Uhrzeit), danach der Reihe nach weiter. notices.py sucht darin
    den ersten NICHT stummgeschalteten (siehe _current_tip_notice) — reine
    Listenreihenfolge statt Zufallsauswahl, damit dieselbe Rotation für jeden
    Aufruf am selben Tag reproduzierbar bleibt."""
    if not TIPS:
        return []
    start = (ordinal // rotation_days) % len(TIPS)
    return TIPS[start:] + TIPS[:start]
