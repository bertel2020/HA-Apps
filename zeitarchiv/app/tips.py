"""Inhalt und Rotationsreihenfolge für den rotierenden Tipp im Meldungs-
Center (siehe notices.py, _current_tip_notice) — bewusst als eigenes Modul,
getrennt von der Stummschalt-/Auswahl-Logik: diese Liste soll wachsen und
schrumpfen können, ohne notices.py anzufassen."""

from __future__ import annotations

from .i18n import N_

TIPS = [
    {
        "slug": "dashboards_gruppieren",
        "title": N_("Tipp: Eigene Dashboards anlegen"),
        "detail": N_("Gruppiere zusammengehörige Charts und Tabellen auf einem eigenen Dashboard, statt alles in der Standard-Übersicht zu sammeln."),
        "meta": N_("Dashboards"),
        "link": "/dashboards",
    },
    {
        "slug": "chart_duplizieren",
        "title": N_("Tipp: Chart duplizieren statt neu bauen"),
        "detail": N_("Über das ⋮-Menü einer Kachel lässt sich ein bestehendes Chart oder eine Tabelle als Ausgangspunkt für eine Variante duplizieren."),
        "meta": N_("Charts"),
        "link": "/charts",
    },
    {
        "slug": "tageslastprofil_wochentag",
        "title": N_("Tipp: Tageslastprofil nach Wochentag"),
        "detail": N_("Das Energiedashboard zeigt den typischen Tagesverlauf getrennt nach Wochentag — praktisch, um Wochenend- von Wochentag-Mustern zu unterscheiden."),
        "meta": N_("Energiedashboard"),
        "link": "/energiedashboard",
    },
    {
        "slug": "eigene_anzeigenamen",
        "title": N_("Tipp: Eigene Anzeigenamen vergeben"),
        "detail": N_("Entitäten lassen sich mit einem eigenen Namen versehen, unabhängig vom Home-Assistant-Friendly-Name — praktisch bei kryptischen Originalnamen."),
        "meta": N_("Entitäten"),
        "link": "/entities",
    },
    {
        "slug": "housekeeping_aufraeumen",
        "title": N_("Tipp: Aufräumen leicht gemacht"),
        "detail": N_("Ungenutzte Charts/Tabellen, Duplikate und inaktive Entitäten sammeln sich im Housekeeping-Bereich — an einer Stelle statt verstreut."),
        "meta": N_("Housekeeping"),
        "link": "/housekeeping",
    },
    {
        "slug": "farbschema_wechseln",
        "title": N_("Tipp: Farbschema wechseln"),
        "detail": N_("Neben Hell/Dunkel stehen drei Farbschemata zur Wahl (Zeitarchiv, Home Assistant, Modern) — unter Darstellung in den Einstellungen."),
        "meta": N_("Einstellungen"),
        "link": "/settings#darstellung",
    },
    {
        "slug": "meldungen_stummschalten",
        "title": N_("Tipp: Meldungen zeitweise stummschalten"),
        "detail": N_("Eine Empfehlung nicht relevant? Über das 🔕-Icon lässt sie sich für 1 Stunde bis dauerhaft stummschalten, statt sie einfach zu ignorieren."),
        "meta": N_("Meldungs-Center"),
        "link": None,
    },
    {
        "slug": "automatische_backups",
        "title": N_("Tipp: Automatische Backups einrichten"),
        "detail": N_("Ein Zeitplan für regelmäßige Sicherungen lässt sich einmal festlegen, statt manuell ans Backup zu denken."),
        "meta": N_("Backup"),
        "link": "/backup",
    },
    {
        "slug": "datum_springen",
        "title": N_("Tipp: Direkt zu einem Datum springen"),
        "detail": N_("Auf der Bereinigungs- und Entitäten-Detailseite lässt sich per Klick auf den Zeitraum-Titel direkt zu einem bestimmten Datum springen, statt sich vorzublättern."),
        "meta": N_("Entitäten"),
        "link": None,
    },
    {
        "slug": "tabellen_sortieren",
        "title": N_("Tipp: Tabellen durch Klick sortieren"),
        "detail": N_("Die meisten Tabellen in der App lassen sich durch Klick auf eine Spaltenüberschrift sortieren — auch ohne extra Sortier-Steuerung."),
        "meta": N_("Allgemein"),
        "link": None,
    },
    {
        "slug": "ausreisser_luecken",
        "title": N_("Tipp: Ausreißer und Lücken erkennen"),
        "detail": N_("Schwellwerte für Ausreißer- und Lücken-Erkennung lassen sich je Entität passend zum jeweiligen Sensor einstellen."),
        "meta": N_("Entitäten"),
        "link": "/entities",
    },
    {
        "slug": "wiederholungen_verdichten",
        "title": N_("Tipp: Wiederholungen verdichten"),
        "detail": N_("Aufeinanderfolgende, praktisch gleiche Werte lassen sich in der Bereinigung automatisch zu einem einzigen Datenpunkt zusammenfassen."),
        "meta": N_("Bereinigung"),
        "link": None,
    },
    {
        "slug": "vorjahresvergleich_tabelle",
        "title": N_("Tipp: Vorjahresvergleich in einer Tabelle"),
        "detail": N_("Eine Vergleichsspalte lässt sich als Vorjahreszeitraum statt als fester Zeitraum definieren — praktisch für Jahresvergleiche auf einen Blick."),
        "meta": N_("Tabellen"),
        "link": "/tables",
    },
    {
        "slug": "aufbewahrung_je_entitaet",
        "title": N_("Tipp: Aufbewahrungsfrist je Entität"),
        "detail": N_("Statt einer globalen Frist für alle Entitäten lässt sich die Aufbewahrung individuell je Entität festlegen, um Speicherplatz gezielt zu sparen."),
        "meta": N_("Aufbewahrung"),
        "link": "/housekeeping#aufbewahrung",
    },
    {
        "slug": "entitaeten_favorisieren",
        "title": N_("Tipp: Entitäten favorisieren"),
        "detail": N_("Ein Klick auf den Stern hebt eine Entität in der Übersicht nach oben — praktisch für die, die man am häufigsten braucht."),
        "meta": N_("Entitäten"),
        "link": "/entities",
    },
    {
        "slug": "csv_export",
        "title": N_("Tipp: CSV-Export für externe Auswertung"),
        "detail": N_("Rohdaten oder Aggregate lassen sich als CSV exportieren, um sie z. B. in einer Tabellenkalkulation weiterzuverarbeiten."),
        "meta": N_("Export"),
        "link": "/export",
    },
    {
        "slug": "protokoll_durchsuchen",
        "title": N_("Tipp: Protokoll durchsuchen"),
        "detail": N_("Das Protokoll aller Hintergrundaktionen lässt sich durchsuchen, statt sich chronologisch durchzuklicken."),
        "meta": N_("Protokoll"),
        "link": "/logs",
    },
    {
        "slug": "wachstum_ueber_zeit",
        "title": N_("Tipp: Wachstum über Zeit im Blick"),
        "detail": N_("Die Statistik-Seite zeigt, wie Datensätze und Speicherverbrauch sich über die Zeit entwickeln — praktisch, um Trends frühzeitig zu erkennen."),
        "meta": N_("Statistik"),
        "link": "/statistik",
    },
    {
        "slug": "rauschen_filtern",
        "title": N_("Tipp: Rauschen in Messwerten filtern"),
        "detail": N_("Ein Wertfilter lässt sich je Entität aktivieren, um kleine Schwankungen (Messrauschen) automatisch zu glätten."),
        "meta": N_("Entitäten"),
        "link": None,
    },
    {
        "slug": "mehrere_entitaeten_chart",
        "title": N_("Tipp: Mehrere Entitäten in einem Chart vergleichen"),
        "detail": N_("Ein Chart lässt sich über mehrere Entitäten hinweg anlegen — praktisch, um z. B. Bezug und Einspeisung nebeneinander zu sehen."),
        "meta": N_("Charts"),
        "link": "/charts/new",
    },
    {
        "slug": "kacheln_anpassen",
        "title": N_("Tipp: Kacheln anpassen"),
        "detail": N_("Kacheln auf einem Dashboard lassen sich per Ziehen neu anordnen und über den Größen-Picker im Kachelmenü in Spalten/Zeilen skalieren."),
        "meta": N_("Dashboards"),
        "link": "/dashboards",
    },
    {
        "slug": "verwendet_in",
        "title": N_("Tipp: Wo wird ein Chart überall verwendet?"),
        "detail": N_("Im Editor eines Charts oder einer Tabelle zeigt „Verwendet in“, auf welchen Dashboards die Kachel bereits angepinnt ist."),
        "meta": N_("Charts"),
        "link": "/charts",
    },
    {
        "slug": "schriftgroesse_anpassen",
        "title": N_("Tipp: Schriftgröße anpassen"),
        "detail": N_("Die Schriftgröße der gesamten App lässt sich in den Einstellungen in mehreren Stufen anpassen — praktisch für größere Bildschirme oder bessere Lesbarkeit."),
        "meta": N_("Einstellungen"),
        "link": "/settings#darstellung",
    },
    {
        "slug": "zaehlerrueckgaenge",
        "title": N_("Tipp: Zählerrückgänge werden gemeldet"),
        "detail": N_("Sinkt ein Zählerstand, meldet das Meldungs-Center es und ordnet es ein: „kehrt zurück“ ist meist ein Fehlwert, „bleibt niedrig“ eher ein Zählertausch. Housekeeping → Zählerrückgänge listet alle Fälle."),
        "meta": N_("Bereinigung"),
        "link": "/housekeeping#zaehlerrueckgaenge",
    },
    {
        "slug": "vorperiode_vergleichen",
        "title": N_("Tipp: Mit der Vorperiode vergleichen"),
        "detail": N_("Ein Chart lässt sich mit „Vergleichen“ gegen den vorherigen Zeitraum oder das Vorjahr überlagern, um Veränderungen direkt zu sehen."),
        "meta": N_("Charts"),
        "link": "/charts/new",
    },
    {
        "slug": "fortlaufender_zeitraum",
        "title": N_("Tipp: Fortlaufender statt kalendarischer Zeitraum"),
        "detail": N_("Ein Chart lässt sich wahlweise fortlaufend (z. B. „letzte 7 Tage“) oder kalendarisch ausgerichtet (z. B. „diese Woche“) anzeigen."),
        "meta": N_("Charts"),
        "link": None,
    },
    {
        "slug": "diagrammtyp_waehlen",
        "title": N_("Tipp: Diagrammtyp wählen"),
        "detail": N_("Linie, Balken oder Fläche lassen sich je Chart einzeln festlegen, statt sich auf die automatische Wahl zu verlassen."),
        "meta": N_("Charts"),
        "link": None,
    },
    {
        "slug": "kennzahlen_legende",
        "title": N_("Tipp: Kennzahlen in der Legende"),
        "detail": N_("Die Chart-Legende kann Summe, Durchschnitt, Minimum oder Maximum direkt neben dem Entitätsnamen anzeigen."),
        "meta": N_("Charts"),
        "link": None,
    },
    {
        "slug": "duplikate_entfernen",
        "title": N_("Tipp: Duplikate automatisch entfernen"),
        "detail": N_("Erkannte doppelte Zeitstempel lassen sich mit einem Klick automatisch bereinigen, statt sie einzeln durchzugehen."),
        "meta": N_("Bereinigung"),
        "link": "/housekeeping#duplikate",
    },
    {
        "slug": "import_berichte",
        "title": N_("Tipp: Import-Vorgänge nachvollziehen"),
        "detail": N_("Jeder Import erzeugt einen Bericht mit Status und Details — praktisch, um frühere Importe nachzuvollziehen oder Fehler zu prüfen."),
        "meta": N_("Import"),
        "link": "/import?tab=reports",
    },
    {
        "slug": "entitaet_migrieren",
        "title": N_("Tipp: Datensätze in eine andere Entität übertragen"),
        "detail": N_("Über Entität → Konfiguration → „Datensätze in andere Entität übertragen“ lässt sich der komplette archivierte Verlauf umziehen — praktisch, wenn Home Assistant ein Gerät ersetzt oder umbenannt hat."),
        "meta": N_("Entitäten"),
        "link": "/entities",
    },
    {
        "slug": "jahresvergleich_laufsumme",
        "title": N_("Tipp: Laufsumme und Ziellinie im Jahresvergleich"),
        "detail": N_("Der Jahresvergleich im Chart-Editor lässt sich um eine Laufsumme und eine frei wählbare Soll-Entität als Ziellinie erweitern — beide unabhängig voneinander zuschaltbar."),
        "meta": N_("Charts"),
        "link": "/charts/new",
    },
    {
        "slug": "archiv_verdichten",
        "title": N_("Tipp: Archivierte Monate nachträglich verdichten"),
        "detail": N_("Bereits archivierte Monate lassen sich auf eine gröbere Auflösung reduzieren — manuell mit Vorschau im Bearbeitungsbereich einer Entität, oder automatisch über ein Mindestalter in Housekeeping → Verdichten."),
        "meta": N_("Housekeeping"),
        "link": "/housekeeping#verdichten",
    },
    {
        "slug": "aktivitaet_protokoll",
        "title": N_("Tipp: Korrekturen und Verdichtungen im Blick"),
        "detail": N_("Der Housekeeping-Tab „Aktivität“ listet Korrekturen, hinzugefügte Werte, Bereinigungen und Verdichtungen, filterbar nach Entität, Aktionstyp und Zeitraum."),
        "meta": N_("Housekeeping"),
        "link": "/housekeeping#aktivitaet",
    },
    {
        "slug": "kachel_mehrfach_anheften",
        "title": N_("Tipp: Eine Entität mehrfach als Kachel zeigen"),
        "detail": N_("Dieselbe Entität lässt sich mehrfach als Werte-Kachel anheften — z. B. einmal mit dem aktuellen Wert, einmal mit dem Durchschnitt, jede mit eigenen Einstellungen."),
        "meta": N_("Dashboards"),
        "link": "/dashboards",
    },
    {
        "slug": "verlauf_pro_entitaet",
        "title": N_("Tipp: Änderungen an einer Entität nachlesen"),
        "detail": N_("Der Reiter „Verlauf“ in „Werte bearbeiten“ zeigt, was an genau dieser Entität geändert wurde: markiert, zurückgenommen, korrigiert (mit altem und neuem Wert), hinzugefügt, entfernt oder verdichtet."),
        "meta": N_("Entitäten"),
        "link": None,
    },
    {
        "slug": "automatische_bereinigung",
        "title": N_("Tipp: Markierte Werte automatisch entfernen"),
        "detail": N_("Markierte Werte lassen sich nach einem Mindestalter automatisch endgültig entfernen (Housekeeping → Speicherplatz). Im Reiter „Markiert“ steht je Charge, ab wann sie dran ist; bis dahin kannst du sie zurücknehmen."),
        "meta": N_("Housekeeping"),
        "link": "/housekeeping",
    },
    {
        "slug": "legende_als_tabelle",
        "title": N_("Tipp: Chart-Legende als Tabelle"),
        "detail": N_("Unter Optionen → Legenden-Stil → Tabelle stehen die Kennzahlen als Spalten statt in Chips — auch beim Jahresvergleich mit Laufsumme, mit einer Zeile je Entität und Jahr."),
        "meta": N_("Charts"),
        "link": None,
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
