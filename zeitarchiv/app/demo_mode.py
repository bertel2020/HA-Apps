"""Demo-Modus: eigenes, vollständig getrenntes Datenverzeichnis
(``<BASE_DIR>/demo``) für eine synthetische Vorführ-/Testinstanz — siehe
``DEMO_MODUS_PLAN.md`` im Projekt-Wurzelverzeichnis für die Gesamtübersicht.

Dieses Modul kennt bewusst NUR das Dateisystem, nie den laufenden `Index`
einer Demo-Instanz: `demo_dir_info()`/`remove_demo_dir()` müssen auch dann
sicher aufrufbar sein, wenn der aktuell laufende Prozess NICHT im Demo-Modus
läuft (Zustand "ungenutzt" — siehe Housekeeping → Demo-Daten) und deshalb
keine offene Verbindung zur Demo-`index.sqlite` hat. `Index.__init__()`
öffnet seine Datei immer lese-schreibend (legt sie bei Bedarf sogar neu an)
und führt Migrationen aus — für einen bloßen Blick von außen wäre das ein
echter, ungewollter Schreibzugriff auf ein Verzeichnis, das gerade niemand
aktiv verwendet. Solange die Instanz selbst im Demo-Modus läuft, liefert
stattdessen `Index.get_overview()` dieselben (und genauere) Zahlen direkt
aus der offenen Verbindung — siehe Aufrufer in housekeeping_routes.py.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from zoneinfo import ZoneInfo

from .demo_generation import DEMO_ENTITIES, run_generation
from .progress import JobProgress
from .route_support import dir_size_and_newest_mtime
from .storage.coordinator import StorageCoordinator
from .storage.index import Index
from .storage.paths import storage_area_dir

DEMO_DIR_NAME = "demo"

#: Fortschritt der Demo-Daten-Erzeugung — EIN gemeinsames Objekt für alle drei
#: Auslöser (housekeeping_routes.py: "Jetzt ergänzen"/"Neu erzeugen",
#: background.py: Zeitplan + Erststart), weil sie sich ohnehin gegenseitig
#: ausschließen sollen (immer nur ein Generierungslauf gleichzeitig,
#: JobProgress.claim() sorgt dafür) und die Fortschrittsanzeige in
#: Housekeeping unabhängig davon stimmen muss, WER den Lauf ausgelöst hat.
#: Lebt hier statt in housekeeping_routes.py, weil auch background.py
#: (Scheduler, Erststart) darauf zugreifen muss, ohne von den Routen
#: abhängig zu werden.
demo_progress = JobProgress("demo-generate", unit="Entitäten", label="Demo-Daten")

_DEMO_PHASE_LABELS = {
    "append": "Demo-Daten werden ergänzt …",
    "regenerate": "Demo-Daten werden neu erzeugt …",
    "fresh": "Demo-Daten werden erstmalig erzeugt …",
}

# Sekundenwerte zu formatting.DEMO_APPEND_INTERVAL_LABELS — getrennt von den
# Anzeige-Texten, weil background.py nur die Zahlen braucht und
# formatting.py bewusst frei von Scheduler-Logik bleiben soll.
DEMO_APPEND_INTERVAL_SECONDS: dict[str, int | None] = {
    "off": None, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600,
}


def demo_dir(base_dir: Path) -> Path:
    """<BASE_DIR>/demo — unabhängig davon, ob DIESER Prozess gerade selbst
    dorthin zeigt (Demo-Modus aktiv) oder nicht (siehe Moduldoc)."""
    return base_dir / DEMO_DIR_NAME


def load_options(base_dir: Path) -> dict:
    """Add-on-Optionen (config.yaml → Home-Assistant-Konfiguration) aus
    base_dir/options.json — IMMER base_dir (roh), nie das ggf. auf
    base_dir/demo zeigende DATA_DIR: der Supervisor legt die Datei fest in
    das eine gemountete Datenverzeichnis, unabhängig vom Demo-Modus.
    resolve_demo_mode() unten liest daraus erst, WOHIN DATA_DIR zeigen soll
    — ein Zugriff über DATA_DIR selbst wäre also zirkulär bzw. läse im
    Demo-Modus aus dem falschen (leeren) Unterordner."""
    options_path = base_dir / "options.json"
    if options_path.exists():
        return json.loads(options_path.read_text(encoding="utf-8"))
    return {}


def resolve_demo_mode(options: dict, environ: Mapping[str, str] | None = None) -> bool:
    """Ob DIESER Prozess im Demo-Modus laufen soll (DEMO_MODUS_PLAN.md).
    ZEITARCHIV_DEMO_MODE bleibt als Fallback für Docker-Compose-/venv-Betrieb
    ohne Supervisor, analog zu ZEITARCHIV_TIMEZONE bei load_timezone()
    (timezone_config.py). Ein Wechsel braucht immer einen Neustart des
    Prozesses — main.py ruft dies nur einmal beim Modulimport auf, kein
    Laufzeit-Umschalter."""
    env = os.environ if environ is None else environ
    return bool(options.get("demo_mode")) or env.get("ZEITARCHIV_DEMO_MODE") == "1"


def demo_dir_info(base_dir: Path) -> dict | None:
    """None, falls <base_dir>/demo nicht existiert (oder leer ist). Sonst
    rein dateisystembasiert:

    - size_bytes: rekursive Summe aller Dateigrößen
    - newest_mtime: jüngste Änderungszeit einer enthaltenen Datei (Unix-Ts)
    - entity_count_approx: Anzahl Unterordner in archive/ — jede Entität hat
      dort genau einen, nach ihrer Entity-ID benannten Ordner (entity_dir()).
      Eine NÄHERUNG: Entitäten ohne abgeschlossenen ersten Monat stehen nur
      im Hot Buffer und fehlen hier — für die Anzeige in einer seit Tagen
      ungenutzten Instanz vernachlässigbar (siehe Moduldoc zum Grund, warum
      hier nicht stattdessen kurz der Index geöffnet wird).
    """
    root = demo_dir(base_dir)
    if not root.is_dir():
        return None

    # Bereits vorhandene, robustere Variante (verkraftet eine zwischenzeitlich
    # verschwundene Datei während des Walks) statt eines eigenen Nachbaus —
    # bislang für housekeeping.import_leftovers genutzt (notices.py), passt
    # aber wortwörtlich auf dieselbe Frage hier.
    size_bytes, newest_mtime = dir_size_and_newest_mtime([root])
    if newest_mtime is None:
        return None

    archive_root = storage_area_dir(root, "archive")
    entity_count_approx = (
        sum(1 for entry in archive_root.iterdir() if entry.is_dir())
        if archive_root.is_dir()
        else 0
    )

    return {
        "size_bytes": size_bytes,
        "newest_mtime": newest_mtime,
        "entity_count_approx": entity_count_approx,
    }


def remove_demo_dir(base_dir: Path) -> None:
    """Entfernt <base_dir>/demo vollständig. Nur aufrufen, wenn die
    laufende Instanz NICHT selbst im Demo-Modus läuft (siehe Moduldoc) —
    das prüft main.py vor dem Aufruf, hier keine zweite Prüfung, um nicht
    zwei Quellen der Wahrheit für DEMO_MODE zu haben."""
    shutil.rmtree(demo_dir(base_dir))


def current_demo_state(demo_mode_active: bool, dir_info: dict | None) -> str | None:
    """Welcher der drei möglichen Housekeeping-Anblicke gerade gilt —
    einmal pro Request berechnet und sowohl an den Abschnitt als auch an
    den Nav-Eintrag weitergereicht (siehe housekeeping_routes.py), damit
    beide nie auseinanderlaufen können."""
    if demo_mode_active:
        return "active"
    if dir_info is not None:
        return "orphan"
    return None


def build_demo_worker(
    data_dir: Path, index: Index, tz: ZoneInfo, coordinator: StorageCoordinator, mode: str,
) -> Callable[[], str]:
    """mode ist "append" ("Jetzt ergänzen"), "regenerate" ("Neu erzeugen")
    oder "fresh" (Erststart, siehe background.py) — schließen sich
    gegenseitig aus, deshalb ein gemeinsamer Worker statt dreier. Läuft über
    demo_progress.start(), egal wer aufruft."""

    def worker() -> str:
        demo_progress.set_phase(_DEMO_PHASE_LABELS[mode], len(DEMO_ENTITIES))
        with coordinator.exclusive():
            result = run_generation(
                data_dir, index, tz, random.Random(),
                append=(mode == "append"), clean=(mode == "regenerate"),
                on_entity=lambda i, entity_id, _n: demo_progress.advance(i, entity_id),
            )
        index.set_setting("demo_append_last_run", str(time.time()))
        if result.skipped:
            return result.skip_reason
        return f"{result.rows_written} Werte über {result.entity_count} Entitäten geschrieben."

    return worker
