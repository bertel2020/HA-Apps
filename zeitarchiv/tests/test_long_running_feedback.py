"""Rückmeldung für Aktionen, die spürbar dauern.

Anlass war eine Messung gegen einen echten Bestand (34 Entitäten, 10,2 Mio.
Zeilen, dazu eine 3,0-GB-Symcon-Quelle mit 233 Variablen):

* Symcon-Dry-Run über alle Variablen — rund 1,7 Minuten, ohne jede Rückmeldung
* Bereinigung — rund 20 s, davon 15,6 s Rollup-Neuberechnung über 163 Monate
* CSV-Import — allein das Einlesen von 104 MB dauert 6,0 s
* Backup erstellen — 2,4 s, und ausgerechnet das hatte als einziges einen Balken

Beim Umbau kamen zwei Fehler zum Vorschein, die beide dieselbe Ursache haben:
Der Pfad war nie durchlaufen worden, weil ihn kein Test berührte. Sie stehen
deshalb hier zuerst.
"""

from __future__ import annotations

import re

import pytest

from _paths import APP, TEMPLATES
from app.formatting import format_int
from app.progress import JobBusy, JobProgress

# --------------------------------------------------------------------------
# Die zwei Fehler, die der Umbau zutage förderte
# --------------------------------------------------------------------------

#: Wie eine Route ihre Vorlage benennt: TemplateResponse(request, "name.html", …)
VORLAGE = re.compile(r'TemplateResponse\(\s*\w+\s*,\s*"([^"]+\.html)"')


def _vorlagenverweise() -> dict[str, set[str]]:
    return {
        pfad.name: set(VORLAGE.findall(pfad.read_text(encoding="utf-8")))
        for pfad in APP.glob("*.py")
    }


def test_every_template_a_route_names_actually_exists() -> None:
    """Ein falscher Vorlagenname fällt erst zur Laufzeit auf — als 500.

    Genau das war der Zustand von `/import/start` und `/import/progress`: Beide
    baten um "self._import_progress.html". Die Datei heißt `_import_progress.html`;
    das "self." stammt aus einem Suchen-und-Ersetzen, das beim Herauslösen der
    Import-Routen in eine Klasse auch in den Zeichenketten zugeschlagen hat.
    Die Fortschrittsanzeige des Symcon-Imports — die aufwendigste der App —
    war damit vom Umbau bis 0.84.0 tot, ohne dass ein Test es bemerkte.
    """
    fehlend = []
    gesamt = 0
    for modul, namen in _vorlagenverweise().items():
        for name in namen:
            gesamt += 1
            if not (TEMPLATES / name).is_file():
                fehlend.append(f"{modul} → {name}")
    assert gesamt >= 40, f"nur {gesamt} Vorlagenverweise gefunden — Muster prüfen"
    assert not fehlend, "Routen verweisen auf nicht vorhandene Vorlagen: " + ", ".join(fehlend)


@pytest.mark.parametrize("zeilen", [0, 999, 1_000, 1_234_567])
def test_the_import_progress_bar_survives_large_row_counts(zeilen: int) -> None:
    """`format_int` auf seiner eigenen Ausgabe wirft ab vier Stellen.

    Die Fortschrittsanzeige bekam ihre Zeilenzahl vorformatiert übergeben UND
    legte in der Vorlage noch einmal `|format_int` darauf. Bis 999 Zeilen geht
    das gut, weil dort kein Tausenderpunkt entsteht; ab 1.000 versucht
    `int("1.000")` und scheitert. Ein Import unter 1.000 Zeilen ist der
    Ausnahmefall — die Anzeige wäre also fast immer geplatzt.
    """
    from app.main import templates

    vorlage = templates.env.get_template("_import_progress.html")
    html = vorlage.render(
        phase="importing",
        current_variable="34427",
        planned_variables=41,
        total_variables=233,
        done_months=118,
        total_months=1412,
        rows_imported=zeilen,
        percent=8,
    )
    assert format_int(zeilen) in html


# --------------------------------------------------------------------------
# Der gemeinsame Auftragszustand
# --------------------------------------------------------------------------

def test_a_second_start_does_not_launch_a_second_run() -> None:
    """Zwei gleichzeitige Läufe auf denselben Daten wären das eigentliche
    Problem hinter dem Doppelklick — die Sperre gehört deshalb auf den Server,
    nicht nur an den Button."""
    auftrag = JobProgress("test")
    with auftrag.claim():
        with pytest.raises(JobBusy):
            with auftrag.claim():
                pass


def test_a_failed_run_still_ends_in_a_finished_state() -> None:
    """Ohne diesen Zweig bliebe die Anzeige bei 40 % stehen — für immer, weil
    das Polling erst aufhört, wenn `running` False wird."""
    auftrag = JobProgress("test")
    auftrag.start(lambda: (_ for _ in ()).throw(ValueError("kaputt")))
    for _ in range(200):
        if not auftrag.snapshot()["running"]:
            break
        import time

        time.sleep(0.01)
    stand = auftrag.snapshot()
    assert stand["running"] is False
    assert stand["started"] is True
    assert "kaputt" in stand["error"]
    assert stand["result"] is None


def test_a_claim_that_fails_before_the_thread_starts_is_released() -> None:
    """Scheitert schon das Einreihen, wäre die Aktion sonst bis zum Neustart
    blockiert — ohne dass irgendetwas liefe, das man abwarten könnte."""
    auftrag = JobProgress("test")
    with pytest.raises(RuntimeError):
        with auftrag.claim():
            raise RuntimeError("Formular unbrauchbar")
    assert auftrag.snapshot()["running"] is False
    # started zurückgesetzt: Sonst lieferte der Poll-Endpunkt eine
    # Fortschrittsanzeige für einen Lauf, den es nie gab.
    assert auftrag.snapshot()["started"] is False


def test_the_percentage_never_exceeds_one_hundred() -> None:
    """`done` kann über `total` hinauslaufen, wenn eine Phase mehr Schritte
    meldet als vorab geschätzt (die CSV-Zeilenzahl ist eine Näherung) — ein
    Balken über die Nut hinaus sähe nach einem Fehler aus."""
    auftrag = JobProgress("test", unit="Zeilen")
    auftrag.set_phase("läuft", total=100)
    auftrag.advance(140)
    stand = auftrag.snapshot()
    assert stand["percent"] == 100
    assert stand["done"] == 100


def test_an_unknown_total_yields_no_invented_percentage() -> None:
    """Lieber ein leerer Balken als eine geschätzte Zahl."""
    auftrag = JobProgress("test")
    auftrag.set_phase("läuft ohne Gesamtzahl")
    auftrag.advance(17)
    stand = auftrag.snapshot()
    assert stand["total"] == 0
    assert stand["percent"] == 0
    assert stand["done"] == 17


def test_a_new_phase_resets_the_counter() -> None:
    """Sonst stünde beim Wechsel von einer kurzen in eine lange Phase
    kurzzeitig der alte, bereits vollständige Stand unter der neuen
    Überschrift."""
    auftrag = JobProgress("test", unit="Zeilen")
    auftrag.set_phase("Schritt 1/2", total=10)
    auftrag.advance(10, "abc")
    auftrag.set_phase("Schritt 2/2", unit="Monate")
    stand = auftrag.snapshot()
    assert stand["done"] == 0
    assert stand["detail"] == ""
    assert stand["unit"] == "Monate"


# --------------------------------------------------------------------------
# Die Endpunkte
# --------------------------------------------------------------------------

#: Jede Fortschrittsanzeige pollt genau einen dieser Endpunkte.
POLL_ENDPUNKTE = [
    "/import/progress",
    "/import/dry-run/progress",
    "/import/csv/progress",
    "/import/ha/progress",
    "/settings/purge/progress",
]


@pytest.mark.parametrize("pfad", POLL_ENDPUNKTE)
def test_a_progress_endpoint_is_silent_until_something_ran(client, pfad: str) -> None:
    """Die Ausgabecontainer holen sich diese Endpunkte beim Laden der Seite
    (hx-trigger="load"), damit eine laufende Aktion einen Reload überlebt. Ohne
    leere Antwort stünde dort bei jedem normalen Seitenaufruf eine
    Fortschrittsanzeige für nichts."""
    antwort = client.get(pfad)
    assert antwort.status_code == 200
    assert antwort.text.strip() == ""


def test_the_shared_progress_partial_renders_for_every_caller() -> None:
    """_job_progress.html braucht zwei Werte, die nicht aus dem Auftrag
    kommen: die id des Containers und den Poll-Endpunkt. Fehlt einer, pollt die
    Anzeige ins Leere bzw. ersetzt sich selbst nie — beides sieht aus wie ein
    Hänger, nicht wie ein Fehler."""
    from app.main import templates

    quelle = "\n".join(
        pfad.read_text(encoding="utf-8") for pfad in APP.glob("*.py")
    )
    kontexte = re.findall(r'"progress_id":\s*"([^"]+)",\s*\n\s*"poll_url":\s*"([^"]+)"', quelle)
    assert len(kontexte) >= 4, f"nur {len(kontexte)} Fortschritts-Kontexte gefunden"
    vorlage = templates.env.get_template("_job_progress.html")
    for progress_id, poll_url in kontexte:
        html = vorlage.render(
            progress_id=progress_id, poll_url=poll_url,
            phase_label="Schritt 1/2 · läuft…", done=3, total=10, unit="Monate",
            detail="sensor.x 2024-03", percent=30,
        )
        assert f'id="{progress_id}"' in html
        assert f'hx-get="{poll_url}"' in html
        assert 'hx-trigger="every 500ms"' in html


def test_no_route_names_the_shared_partial_without_a_poll_url() -> None:
    """Gegenprobe zum Test darüber: Es genügt nicht, dass die Kontexte
    vollständig sind — jede Stelle, die die Vorlage rendert, muss auch einen
    davon benutzen."""
    for pfad in APP.glob("*.py"):
        quelle = pfad.read_text(encoding="utf-8")
        # Nur wer die Vorlage wirklich rendert — progress.py NENNT sie im
        # Modul-Docstring, reicht aber selbst nie einen Kontext hinein.
        if '"_job_progress.html"' not in quelle:
            continue
        if not VORLAGE.search(quelle):
            continue
        assert "poll_url" in quelle, f"{pfad.name} rendert die Anzeige ohne poll_url"


# --------------------------------------------------------------------------
# Die Vorlagen
# --------------------------------------------------------------------------

#: Aktionen, für die eine Dauer gemessen wurde und die deshalb nicht mehr
#: aussehen dürfen wie ein Klick ohne Wirkung. Wert ist die Vorlage, in der
#: der auslösende Button steht.
LANGE_AKTIONEN = {
    "import/dry-run": "import.html",
    "import/start": "import.html",
    "import/csv/dry-run": "_csv_import_section.html",
    "import/csv/start": "_csv_import_section.html",
    "import/ha/dry-run": "_ha_import_section.html",
    "import/ha/start": "_ha_import_section.html",
    "import/ha/availability": "_ha_import_section.html",
    "settings/purge": "_settings_purge_form.html",
    "settings/retention-enforcement/run": "_settings_retention_form.html",
    "settings/retention-enforcement/preview": "_settings_retention_form.html",
    "settings/storage-index/check": "_settings_storage_index_form.html",
    "settings/storage-index/repair": "_settings_storage_index_form.html",
    "backup/start": "_settings_backup_ready.html",
}


@pytest.mark.parametrize(("ziel", "vorlage"), sorted(LANGE_AKTIONEN.items()))
def test_every_slow_button_locks_itself_for_the_duration(ziel: str, vorlage: str) -> None:
    """hx-disabled-elt="this" ist die halbe Miete von Stufe 1.

    Die andere Hälfte ist CSS (`.btn.htmx-request` in app.css) und wird unten
    geprüft. Ohne das Attribut bleibt der Button klickbar, und jeder weitere
    Klick löst eine weitere Anfrage aus — beim Import auf denselben Daten.
    """
    quelle = (TEMPLATES / vorlage).read_text(encoding="utf-8")
    block = quelle[quelle.index(f'hx-post="{ziel}"'):]
    # Bis zum Ende des öffnenden Tags schauen, nicht weiter: Sonst fände man
    # das Attribut des NÄCHSTEN Buttons.
    block = block[: block.index(">")]
    assert "hx-disabled-elt" in block, f'{vorlage}: {ziel} sperrt sich nicht'


def test_the_css_actually_draws_a_running_button() -> None:
    """Das Attribut allein ändert nichts Sichtbares: htmx setzt zusätzlich die
    Klasse .htmx-request, und bis 0.84.0 zeichnete die keine einzige Regel.

    Die Reihenfolge ist Teil der Zusicherung — `.btn:disabled{opacity:.4}`
    steht davor, und hx-disabled-elt setzt genau dieses disabled. Käme die
    laufende Variante zuerst, wäre der laufende Button blasser als ein
    gesperrter.
    """
    css = (APP / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".btn.htmx-request{" in css
    assert ".btn.htmx-request:disabled{" in css
    assert css.index(".btn:disabled{") < css.index(".btn.htmx-request:disabled{")
    assert "@keyframes btn-spin" in css
    # .navbtn ist 28x28 mit fester Größe — ein angehängter Ring würde das
    # Glyph aus der Mitte drücken.
    assert ".btn.htmx-request:not(.navbtn)::after" in css


def test_every_busy_chip_indicator_points_at_a_chip_that_exists() -> None:
    """hx-indicator nimmt einen CSS-Selektor. Zeigt er ins Leere, passiert
    schlicht nichts — kein Fehler, keine Meldung, nur wieder ein Button ohne
    Rückmeldung."""
    for pfad in TEMPLATES.glob("*.html"):
        quelle = pfad.read_text(encoding="utf-8")
        for selektor in re.findall(r'hx-indicator="#([\w-]+)"', quelle):
            vorhanden = (
                f'id="{selektor}"' in quelle
                or f"busy_chip('{selektor}'" in quelle
            )
            assert vorhanden, f"{pfad.name}: hx-indicator #{selektor} hat kein Ziel"


def test_pages_that_use_the_chip_load_its_script() -> None:
    """Der Chip erscheint per CSS auch ohne JavaScript — nur die mitlaufende
    Uhr fehlt dann. Ein fehlendes Skript fiele deshalb nicht auf, außer man
    prüft es."""
    seiten = {
        pfad.name: pfad.read_text(encoding="utf-8")
        for pfad in TEMPLATES.glob("*.html")
        if not pfad.name.startswith("_")
    }
    teilvorlagen = {
        pfad.name: pfad.read_text(encoding="utf-8")
        for pfad in TEMPLATES.glob("_*.html")
    }
    for seite, quelle in seiten.items():
        eingebunden = {
            name for name in teilvorlagen if f'include "{name}"' in quelle
        }
        nutzt_chip = "busy_chip(" in quelle or any(
            "busy_chip(" in teilvorlagen[name] for name in eingebunden
        )
        if nutzt_chip:
            assert "js/busy-chip.js" in quelle, f"{seite} nutzt den Chip ohne sein Skript"


# --------------------------------------------------------------------------
# Stufe 4: das Abzeichen an der Glocke
#
# Seit die Aufträge im Hintergrund laufen, überleben sie den Seitenwechsel.
# Die Kopfleiste ist das einzige Bauteil auf jeder Seite — also der einzige
# Ort, an dem "es arbeitet gerade etwas" überhaupt stehen kann.
# --------------------------------------------------------------------------

#: Schalter für die Testquelle unten. Eine eigene Quelle statt der echten
#: (_purge_progress): Die Registratur kennt kein Abmelden, und ein Auftrag,
#: den ein Test auf "läuft" stehen lässt, verfälschte jeden folgenden.
#: Diese hier meldet None, sobald der Schalter aus ist, und ist damit
#: unschädlich, auch wenn sie eingetragen bleibt.
_test_activity: dict | None = None


def _register_test_source() -> None:
    from app.progress import register_source

    register_source("pytest-job", "Testvorgang", lambda: _test_activity)


@pytest.fixture
def laufender_vorgang():
    """Lässt genau einen Vorgang laufen und räumt hinterher auf."""
    global _test_activity
    _register_test_source()
    _test_activity = {
        "phase": "Schritt 1/2 · läuft…", "done": 41, "total": 163,
        "unit": "Monate", "detail": "sensor.x 2024-03", "percent": 25,
    }
    yield
    _test_activity = None


def test_the_registry_replaces_a_source_with_the_same_id() -> None:
    """Ein Dienst kann mehrfach konstruiert werden (Tests tun das). Ohne
    Ersetzen bliebe der erste Eintrag stehen und zeigte für immer auf einen
    abgelösten Zustand — die Glocke meldete dann einen Vorgang, den es
    nicht mehr gibt."""
    from app.progress import _quellen, activity_snapshot, register_source

    register_source("pytest-doppelt", "Erst", lambda: None)
    vorher = len(_quellen)
    register_source("pytest-doppelt", "Dann", lambda: {"phase": "", "done": 0, "total": 0,
                                                       "unit": "", "detail": "", "percent": 0})
    assert len(_quellen) == vorher
    treffer = [j for j in activity_snapshot() if j["id"] == "pytest-doppelt"]
    assert len(treffer) == 1
    assert treffer[0]["label"] == "Dann"
    register_source("pytest-doppelt", "Dann", lambda: None)


def test_a_broken_source_does_not_take_the_whole_header_down() -> None:
    """Die Liste ist Beiwerk. Eine Quelle, die wirft, darf nicht jede Seite
    der App mitreißen — der Kontextprozessor läuft bei JEDER Antwort."""
    from app.progress import activity_snapshot, register_source

    def kaputt() -> dict | None:
        raise RuntimeError("Quelle defekt")

    register_source("pytest-kaputt", "Kaputt", kaputt)
    assert [j for j in activity_snapshot() if j["id"] == "pytest-kaputt"] == []
    register_source("pytest-kaputt", "Kaputt", lambda: None)


def test_a_job_without_a_label_stays_out_of_the_header() -> None:
    """Sonst tauchte jeder in einem Test angelegte Auftrag in der Kopfleiste
    auf — und die Registratur wüchse mit jedem Testlauf."""
    from app.progress import _quellen

    vorher = {q.kennung for q in _quellen}
    JobProgress("pytest-namenlos")
    assert {q.kennung for q in _quellen} == vorher


def test_the_bell_shows_no_activity_badge_while_nothing_runs(client) -> None:
    html = client.get("/uebersicht").text
    assert 'class="notice-badge is-activity"' not in html
    assert 'aria-label="Meldungen"' in html


def test_one_running_job_gets_a_badge_without_a_digit(client, laufender_vorgang) -> None:
    """Der Kern der Entscheidung: Bereinigung, Import, Backup, Retention und
    Index-Optimierung nehmen alle coordinator.exclusive() und können gar nicht
    gleichzeitig laufen. Eine dauerhaft angezeigte "1" wäre eine Ziffer ohne
    Information — die Pille bleibt leer, bis die Zahl etwas sagt."""
    html = client.get("/uebersicht").text
    treffer = re.search(r'class="notice-badge is-activity">([^<]*)</span>', html)
    assert treffer is not None, "kein Aktivitäts-Abzeichen gerendert"
    assert treffer.group(1) == ""
    assert 'aria-label="Meldungen — 1 Vorgang läuft"' in html


def test_two_running_jobs_add_the_digit(client, laufender_vorgang) -> None:
    global _test_activity
    from app.progress import register_source

    register_source("pytest-job-2", "Zweiter Testvorgang", lambda: _test_activity)
    try:
        html = client.get("/uebersicht").text
        treffer = re.search(r'class="notice-badge is-activity">([^<]*)</span>', html)
        assert treffer is not None
        assert treffer.group(1) == "2"
        assert 'aria-label="Meldungen — 2 Vorgänge laufen"' in html
    finally:
        register_source("pytest-job-2", "Zweiter Testvorgang", lambda: None)


def test_the_activity_endpoint_is_empty_while_nothing_runs(client) -> None:
    """#notice-activity wird beim Seitenaufbau serverseitig gefüllt und danach
    im Takt nachgeladen. Läuft nichts, muss die Antwort leer sein — sonst
    stünde in jedem Glocken-Menü ein Abschnitt für nichts."""
    antwort = client.get("/notices/activity")
    assert antwort.status_code == 200
    assert antwort.text.strip() == ""


def test_the_activity_endpoint_lists_the_running_job(client, laufender_vorgang) -> None:
    html = client.get("/notices/activity").text
    assert 'data-job-id="pytest-job"' in html
    assert "Testvorgang" in html
    assert "sensor.x 2024-03" in html
    # Zahlen und Balken nur mit echter Gesamtzahl.
    assert "41" in html and "163" in html
    assert 'class="activity-fill" style="width:25%"' in html


def test_a_job_without_a_total_gets_no_bar(client) -> None:
    """Die Aufbewahrung liefert keinen Zwischenstand. Ein Balken müsste dort
    eine Gesamtzahl behaupten, die niemand kennt."""
    global _test_activity
    _register_test_source()
    _test_activity = {"phase": "Aufbewahrung wird angewendet…", "done": 0, "total": 0,
                      "unit": "", "detail": "", "percent": 0}
    try:
        html = client.get("/notices/activity").text
        assert 'data-job-id="pytest-job"' in html
        assert "activity-track" not in html
    finally:
        _test_activity = None


def test_the_activity_badge_only_changes_side_and_colour() -> None:
    """Es ist dasselbe Abzeichen wie rechts, nur links und in der Akzentfarbe.
    Übernähme die Regel auch Maße, liefen die beiden bei einer Änderung an
    .notice-badge auseinander — und genau das soll nicht passieren."""
    css = (APP / "static" / "css" / "app.css").read_text(encoding="utf-8")
    block = css[css.index(".notice-badge.is-activity{"):]
    block = block[: block.index("}")]
    for verboten in ("width", "height", "padding", "border-radius", "font-size"):
        assert verboten not in block, f"is-activity setzt {verboten} neu statt zu erben"
    assert "left:2px" in block and "right:auto" in block
    assert "--accent-line" in block


def test_the_badge_blinks_only_on_change_and_respects_reduced_motion() -> None:
    """Dauerblinken fiele unter die Regel "Pausieren, Beenden, Ausblenden"
    (alles über fünf Sekunden) — ein Symcon-Import läuft Minuten. Drei Blitze
    je Ereignis bleiben darunter."""
    css = (APP / "static" / "css" / "app.css").read_text(encoding="utf-8")
    regel = ".notice-btn.is-changed .notice-badge.is-activity{"
    assert regel in css
    animation = css[css.index(regel):][: css[css.index(regel):].index("}")]
    assert "infinite" not in animation, "das Abzeichen blinkt dauerhaft"
    assert "steps(1,end) 3" in animation
    abschalter = "@media (prefers-reduced-motion:reduce){\n  .notice-btn.is-changed .notice-badge.is-activity{animation:none;}"
    assert abschalter in css


def test_the_header_script_is_loaded_where_the_header_is() -> None:
    """Die Kopfleiste steht auf rund zwanzig Seiten. Das Skript hängt deshalb
    an _topnav.html selbst — wie Alpine — statt an einer Liste von Seiten, die
    sich mit jeder neuen Seite verschöbe."""
    topnav = (TEMPLATES / "_topnav.html").read_text(encoding="utf-8")
    assert "js/topnav-activity.js" in topnav
    for seite in TEMPLATES.glob("*.html"):
        if seite.name.startswith("_"):
            continue
        quelle = seite.read_text(encoding="utf-8")
        assert "js/topnav-activity.js" not in quelle, (
            f"{seite.name} lädt das Skript ein zweites Mal"
        )


# --------------------------------------------------------------------------
# Stufe 4, zweiter Teil: die Vorgänge, die niemand angestoßen hat
#
# Die erste Runde meldete die sieben Aufträge an, die ein Klick auslöst. Übrig
# blieben fünf, die von selbst anlaufen — beim Serverstart, im Wartungsplaner
# oder mitten im Schreibpfad. Genau die sind der schwierigere Fall: Wer nichts
# gedrückt hat, sucht die Erklärung für einen zähen Server auch nirgends.
# --------------------------------------------------------------------------

#: Was die Glocke kennen muss, mit dem Namen, unter dem es dort steht.
#: Absichtlich ausgeschrieben statt aus dem Code eingesammelt: Diese Liste ist
#: die Behauptung, gegen die geprüft wird. Kommt eine lange Aktion dazu und
#: meldet sich nicht an, muss jemand diese Zeile bewusst schreiben.
ERWARTETE_QUELLEN = {
    "retention": "Aufbewahrung",
    "backup": "Backup",
    "purge": "Bereinigung",
    "symcon-import": "Symcon-Import",
    "symcon-dry-run": "Symcon-Vorschau",
    "csv-import": "CSV-Import",
    "ha-import": "Home-Assistant-Import",
    # --- ab hier die fünf ohne Auslöser ---
    "storage-reconcile": "Speicherabgleich",
    "rotation": "Rotation",
    "hourly-backfill": "Stunden-Rollup",
    "rollup-rebuild": "Rollup-Neuaufbau",
    "index-optimize": "Index-Optimierung",
}


def test_every_long_running_action_reports_to_the_bell(client) -> None:
    """Der Anker der ganzen Stufe. Eine lange Aktion, die sich nicht anmeldet,
    ist für jeden anderen Tab ein grundlos hängender Server."""
    from app.progress import _quellen

    angemeldet = {q.kennung: q.label for q in _quellen if not q.kennung.startswith("pytest-")}
    assert angemeldet == ERWARTETE_QUELLEN


# -- JobProgress.track(): der Lauf, der im Request-Thread bleibt ------------

def test_track_releases_the_job_and_lets_the_error_through() -> None:
    """claim() allein gibt bei sauberem Verlassen NICHT frei (siehe dessen
    Docstring) — track() muss das in JEDEM Fall tun, sonst gälte die Rotation
    nach einem einzigen Fehler bis zum Neustart als laufend."""
    auftrag = JobProgress("pytest-track-fehler")

    with pytest.raises(ValueError, match="kaputt"):
        with auftrag.track():
            raise ValueError("kaputt")

    stand = auftrag.snapshot()
    assert stand["running"] is False
    assert stand["error"] == "kaputt", "der Fehler ist weder verschluckt noch verloren"
    assert auftrag.activity() is None


def test_track_leaves_an_already_running_job_alone() -> None:
    """Zwei Rollup-Neuaufbauten für verschiedene Entitäten dürfen gleichzeitig
    laufen — die Reihenfolge regeln die Entitätssperren, nicht diese Anzeige.
    Der zweite darf den Stand des ersten weder überschreiben noch beim
    Verlassen dessen Anzeige abräumen."""
    auftrag = JobProgress("pytest-track-doppelt")

    with auftrag.track():
        auftrag.set_phase("Erster Lauf", total=10)
        auftrag.advance(done=4, detail="sensor.a")
        with auftrag.track():
            auftrag.set_detail("sensor.b")
        assert auftrag.snapshot()["running"] is True, "der zweite hat den ersten beendet"
        assert auftrag.snapshot()["done"] == 4, "der zweite hat den Zähler zurückgesetzt"
    assert auftrag.snapshot()["running"] is False


def test_setting_a_detail_invents_no_count() -> None:
    """Rollup-Nachbau und Index-Kompaktierung bestehen aus genau einem Stück.
    Ein Zähler wäre dort eine erfundene Zahl — und die Glocke zeichnet einen
    Balken, sobald total steht."""
    auftrag = JobProgress("pytest-detail")
    with auftrag.track():
        auftrag.set_phase("Läuft")
        auftrag.set_detail("sensor.x")
        stand = auftrag.snapshot()
    assert stand["detail"] == "sensor.x"
    assert stand["total"] == 0 and stand["done"] == 0


# -- Die fünf einzeln --------------------------------------------------------

def test_the_reconciliation_heartbeat_still_ticks_per_entity() -> None:
    """last_reconcile_tick MUSS im Schleifenkörper stehen, nicht dahinter:
    Der Tick ist die Stall-Erkennung für genau den Fall, dass der Abgleich an
    einer Entitätssperre hängt — käme er erst nach dem vollständigen
    Durchlauf, meldete er nie einen Hänger. Beim Einziehen der
    Fortschrittsanzeige ist die Zeile genau einmal aus der Schleife
    herausgerutscht; deshalb steht sie hier."""
    import ast

    quelle = (APP / "background.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    funktion = next(
        knoten for knoten in ast.walk(baum)
        if isinstance(knoten, ast.FunctionDef)
        and knoten.name == "_background_storage_reconciliation"
    )
    schleifen = [k for k in ast.walk(funktion) if isinstance(k, ast.For)]
    assert len(schleifen) == 1
    im_schleifenkoerper = {
        ziel.attr
        for anweisung in ast.walk(schleifen[0])
        if isinstance(anweisung, ast.Assign)
        for ziel in anweisung.targets
        if isinstance(ziel, ast.Attribute)
    }
    assert "last_reconcile_tick" in im_schleifenkoerper


def test_the_backfill_announces_nothing_when_there_is_nothing_to_do(tmp_path) -> None:
    """Der Wartungsplaner ruft den Backfill alle 30 Sekunden auf, meistens mit
    leerer Warteschlange. Läge die Anmeldung vor der Prüfung, blinkte die
    Glocke im Halbminutentakt für einen Vorgang, den es nicht gab."""
    from zoneinfo import ZoneInfo

    from app.energiedashboard_routes import process_pending_hourly_backfill
    from app.progress import activity_snapshot
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    index = Index(tmp_path / "index.sqlite")
    process_pending_hourly_backfill(tmp_path, index, ZoneInfo("UTC"), StorageCoordinator())
    assert [j for j in activity_snapshot() if j["id"] == "hourly-backfill"] == []

    # Und auch dann nicht, wenn die Rolle zwischen Einreihen und Abarbeiten
    # wieder entfernt wurde — die Entität steht dann noch in der Schlange.
    index.set_setting("energiedashboard_hourly_backfill_pending", '["sensor.weg"]')
    process_pending_hourly_backfill(tmp_path, index, ZoneInfo("UTC"), StorageCoordinator())
    assert [j for j in activity_snapshot() if j["id"] == "hourly-backfill"] == []


def test_rotation_reports_every_entity_with_an_honest_total(tmp_path) -> None:
    """Der Balken an der Glocke braucht eine Gesamtzahl. Sie kann nur aus
    rotate_all_stale() selbst kommen — der Aufrufer kennt die Entitätenliste
    nicht, ohne sie ein zweites Mal unter der Wartungssperre zu holen."""
    from zoneinfo import ZoneInfo

    from app.storage import rotate
    from app.storage.index import Index

    index = Index(tmp_path / "index.sqlite")
    for entity_id in ("sensor.a", "sensor.b", "sensor.c"):
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

    gemeldet: list[tuple[int, int, str]] = []
    rotate.rotate_all_stale(
        tmp_path, index, ZoneInfo("UTC"),
        on_entity=lambda nummer, gesamt, eid: gemeldet.append((nummer, gesamt, eid)),
    )
    assert [(n, g) for n, g, _ in gemeldet] == [(1, 3), (2, 3), (3, 3)]
    assert {eid for _, _, eid in gemeldet} == {"sensor.a", "sensor.b", "sensor.c"}


def test_the_index_optimisation_is_visible_while_it_blocks_everything(tmp_path) -> None:
    """Der Fall, der die Anzeige am nötigsten hat: Das VACUUM hält
    coordinator.exclusive() und legt damit auch die Aufnahme still. Geprüft
    wird deshalb nicht, DASS es sich anmeldet, sondern dass es das genau
    während der Sperre tut."""
    from contextlib import contextmanager

    from app.index_optimization import optimize_index
    from app.progress import activity_snapshot

    waehrend_der_sperre: list[list[dict]] = []

    class FakeIndex:
        def get_database_maintenance_stats(self) -> dict:
            return {"reclaimable_bytes": 4096}

        def vacuum_database(self) -> dict:
            waehrend_der_sperre.append(activity_snapshot())
            return {"before": {"database_bytes": 8192}, "after": {"database_bytes": 4096}}

    class FakeCoordinator:
        @contextmanager
        def exclusive(self):
            yield

    index_path = tmp_path / "index.sqlite"
    index_path.write_bytes(b"x" * 8192)

    ergebnis = optimize_index(FakeIndex(), index_path, FakeCoordinator())
    assert ergebnis["success"] is True

    laufend = {j["id"]: j for j in waehrend_der_sperre[0]}
    assert "index-optimize" in laufend, "die Sperre steht, die Glocke schweigt"
    assert laufend["index-optimize"]["label"] == "Index-Optimierung"
    assert laufend["index-optimize"]["total"] == 0, "ein Balken behauptete hier einen Fortschritt"
    assert [j for j in activity_snapshot() if j["id"] == "index-optimize"] == []


def test_the_rollup_rebuild_is_visible_while_the_write_path_waits(monkeypatch, tmp_path) -> None:
    """Ändert Home Assistant die Aggregationsart, baut der Schreibpfad alle
    Rollups der Entität neu auf — gemessen gut fünf Sekunden mit gehaltener
    Entitätssperre, ausgelöst von niemandem."""
    from zoneinfo import ZoneInfo

    from app.progress import activity_snapshot
    from app.storage import ingestion, rollup

    waehrend_des_umbaus: list[list[dict]] = []

    def fake_rebuild(*args, **kwargs) -> None:
        waehrend_des_umbaus.append(activity_snapshot())

    monkeypatch.setattr(rollup, "rebuild_entity_rollups", fake_rebuild)
    ingestion._rebuild_after_type_change(
        tmp_path, "sensor.typwechsel", "counter", ZoneInfo("UTC"), False
    )

    laufend = {j["id"]: j for j in waehrend_des_umbaus[0]}
    assert laufend["rollup-rebuild"]["label"] == "Rollup-Neuaufbau"
    assert laufend["rollup-rebuild"]["detail"] == "sensor.typwechsel"
    assert [j for j in activity_snapshot() if j["id"] == "rollup-rebuild"] == []


def test_the_write_path_still_routes_the_type_change_through_the_announcement() -> None:
    """Der Hook hängt an get_or_create_entity(on_type_change=...). Ginge er
    wieder direkt auf rollup.rebuild_entity_rollups(), wäre die Anzeige
    lautlos weg — der Test darüber liefe trotzdem weiter grün."""
    quelle = (APP / "storage" / "ingestion.py").read_text(encoding="utf-8")
    assert "on_type_change=lambda _old, new, hourly_rollup: _rebuild_after_type_change(" in quelle
    assert quelle.count("rollup.rebuild_entity_rollups(") == 1, (
        "der Neuaufbau läuft an der Anmeldung vorbei"
    )


def test_the_startup_reconciliation_announces_the_entity_it_is_on(monkeypatch, tmp_path) -> None:
    """Auf einem großen Bestand der längste Vorgang überhaupt — und er läuft
    ausgerechnet dann, wenn gerade jemand die frisch gestartete App aufruft.
    Geprüft wird der Stand MITTEN im Durchlauf: dass er sich am Ende wieder
    abmeldet, sagt über die Zeit dazwischen nichts."""
    from zoneinfo import ZoneInfo

    from app.background import BackgroundDependencies, BackgroundService
    from app.progress import activity_snapshot
    from app.storage import reconcile
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    index = Index(tmp_path / "index.sqlite")
    for entity_id in ("sensor.a", "sensor.b"):
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

    unterwegs: list[dict] = []

    def fake_audit(_dir, _index, _tz, *, entity_ids, repair):
        unterwegs.extend(j for j in activity_snapshot() if j["id"] == "storage-reconcile")
        return {"entities_checked": 1, "mismatches": [], "errors": [], "repaired": repair}

    monkeypatch.setattr(reconcile, "audit_storage_metadata", fake_audit)
    dienst = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=ZoneInfo("UTC"), index=index, coordinator=StorageCoordinator(),
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    dienst._background_storage_reconciliation()

    assert [j["detail"] for j in unterwegs] == ["sensor.a", "sensor.b"]
    assert {j["total"] for j in unterwegs} == {2}, "ohne Gesamtzahl gäbe es keinen Balken"
    assert [j for j in activity_snapshot() if j["id"] == "storage-reconcile"] == []
