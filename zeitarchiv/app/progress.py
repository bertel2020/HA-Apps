"""Fortschritts-Zustand für Aufträge, die in einem Hintergrund-Thread laufen.

Warum es das gibt: Das Muster — Hintergrund-Thread, geteilter Zustand hinter
einem Lock, htmx-Polling auf einen Endpunkt, der entweder die
Fortschrittsanzeige oder das Endergebnis liefert — stand bis 0.84.0 zweimal
ausgeschrieben in der Anwendung (``_ImportProgress`` in import_routes.py,
``_BackupProgress`` in main.py). Vier weitere Aktionen brauchen es ebenfalls,
weil sie gemessen deutlich über der Schwelle liegen, ab der ein Klick ohne
Rückmeldung wie ein Hänger wirkt:

* Symcon-Dry-Run über 233 Variablen — rund 1,7 Minuten
* CSV-Import und -Vorschau — allein das Parsen von 104 MB dauert 6,0 s
* Home-Assistant-Import — ein sequenzieller Roundtrip je Entität
* Bereinigung — rund 20 s, davon 15,6 s Rollup-Neuberechnung über 163 Monate

Sechs handgeschriebene Kopien desselben Musters wären sechs Stellen, an denen
ein vergessenes ``running = False`` eine Anzeige für immer stehen ließe.
Deshalb hier einmal, mit dem ``finally``-Zwang in :meth:`JobProgress.run`.

Die beiden bestehenden Zustände (Import, Backup) sind bewusst NICHT
umgeschrieben: beide tragen fachliche Zusatzfelder (Monatszähler,
``job_id`` der Backup-Tabelle) und laufen erprobt. Sie teilen sich mit den
neuen Aufträgen die Darstellung (_job_progress.html, .job-progress-* in
app.css), nicht den Zustand.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


# --------------------------------------------------------------------------
# Registratur der laufenden Vorgänge
#
# Die Kopfleiste muss wissen, ob gerade IRGENDETWAS läuft — auch auf einer
# Seite, die mit dem Vorgang nichts zu tun hat. Seit die Aufträge im
# Hintergrund laufen, überleben sie den Seitenwechsel; ohne diese Stelle
# wüsste außerhalb ihrer eigenen Seite niemand davon.
#
# Bewusst eine Liste von Lesefunktionen statt einer Liste von JobProgress:
# Zwei ältere Zustände (Symcon-Import, Backup) sind eigene Klassen mit
# fachlichen Zusatzfeldern und laufen erprobt. Sie melden sich hier mit einem
# kleinen Adapter an, statt umgeschrieben zu werden.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Quelle:
    kennung: str
    label: str
    #: Liefert die Anzeigefelder des laufenden Vorgangs — oder None, wenn
    #: gerade keiner läuft. Muss ohne Datei- oder Datenbankzugriff auskommen:
    #: Die Kopfleiste fragt im Sekundentakt, und zwar auf jeder Seite.
    lesen: Callable[[], dict | None]


_quellen: list[_Quelle] = []
_quellen_lock = threading.Lock()


def register_source(kennung: str, label: str, lesen: Callable[[], dict | None]) -> None:
    """Meldet eine Quelle an. Eine bereits vorhandene mit derselben Kennung
    wird ersetzt — sonst hinterließe jede erneute Konstruktion eines Dienstes
    (in Tests durchaus üblich) einen zweiten Eintrag, der auf einen längst
    abgelösten Zustand zeigt."""
    with _quellen_lock:
        for stelle, vorhanden in enumerate(_quellen):
            if vorhanden.kennung == kennung:
                _quellen[stelle] = _Quelle(kennung, label, lesen)
                return
        _quellen.append(_Quelle(kennung, label, lesen))


def activity_snapshot() -> list[dict]:
    """Alle gerade laufenden Vorgänge, in Anmeldereihenfolge.

    Bewusst NUR die laufenden: Das Blinken beim Fertigwerden entsteht im
    Browser dadurch, dass eine Kennung aus der Liste verschwindet — dafür
    braucht der Server keinen Abschlusszustand vorhalten und es gibt kein
    Wettrennen zwischen zwei offenen Tabs darum, wer den Übergang "sieht".

    Eine Quelle, die beim Lesen wirft, wird übergangen statt die ganze
    Kopfleiste mitzureißen: Diese Liste ist Beiwerk, kein Selbstzweck.
    """
    with _quellen_lock:
        quellen = list(_quellen)
    laufend: list[dict] = []
    for quelle in quellen:
        try:
            stand = quelle.lesen()
        except Exception:  # noqa: BLE001 — siehe Docstring
            continue
        if stand:
            laufend.append({"id": quelle.kennung, "label": quelle.label, **stand})
    return laufend


class JobBusy(Exception):
    """Es läuft bereits ein Auftrag desselben Namens.

    Die Routen übersetzen das in einen 409 bzw. in die unveränderte
    Fortschrittsanzeige des bereits laufenden Auftrags — nie in einen zweiten
    Thread auf denselben Daten.
    """


class JobProgress:
    """Zustand genau eines Auftrags: läuft er, wie weit ist er, was kam heraus.

    Alle Felder stehen hinter ``lock``; gelesen wird ausschließlich über
    :meth:`snapshot`, damit kein Aufrufer versehentlich zwei Felder aus zwei
    verschiedenen Momenten miteinander verrechnet (genau der Fehler, der eine
    Prozentzahl über 100 erzeugt).

    ``phase_label`` ist der Text, den die Anzeige als Überschrift zeigt, und
    wandert mit den Phasen mit — bei mehrstufigen Aufträgen also z. B.
    "Schritt 1/2 · Berechne Vorschau…" und danach "Schritt 2/2 · Import läuft…".
    """

    def __init__(self, name: str, unit: str = "", label: str = "") -> None:
        #: Nur für Logmeldungen und Fehlertexte, nie für die Anzeige.
        self.name = name
        #: Was gezählt wird ("Variablen", "Zeilen", "Monate") — steht hinter
        #: den beiden Zahlen in der Fortschrittszeile.
        self.unit = unit
        self.lock = threading.Lock()
        self.started = False
        self.running = False
        #: Spielart des laufenden Auftrags, siehe claim(). Leer, wenn es nur
        #: eine gibt.
        self.kind = ""
        self.phase_label = ""
        self.done = 0
        self.total = 0
        #: Freie Kennung der gerade bearbeiteten Einheit (Symcon-ID,
        #: Entitäts-ID, Monat) — in der Anzeige vor den Zahlen, monospaced.
        self.detail = ""
        self.error = ""
        #: Ergebnis des abgeschlossenen Laufs, vom Aufrufer gesetzt und von
        #: der Ergebnis-Vorlage gelesen. Bewusst untypisiert: was ein Ergebnis
        #: ist, weiß nur der jeweilige Auftrag.
        self.result: Any = None
        self.finished_at = 0.0
        #: Anzeigename in der Kopfleiste. Nur mit label meldet sich der
        #: Auftrag überhaupt an — ein namenloser (etwa in Tests) taucht dort
        #: nicht auf und hinterlässt auch keinen Eintrag.
        self.label = label
        if label:
            register_source(name, label, self.activity)

    def activity(self) -> dict | None:
        """Anzeigefelder für die Kopfleiste, oder None wenn nichts läuft."""
        stand = self.snapshot()
        if not stand["running"]:
            return None
        return {
            "phase": stand["phase_label"],
            "done": stand["done"],
            "total": stand["total"],
            "unit": stand["unit"],
            "detail": stand["detail"],
            "percent": stand["percent"],
        }

    # -- Schreibende Seite: ausschließlich aus dem Hintergrund-Thread ------

    def set_phase(self, phase_label: str, total: int = 0, unit: str | None = None) -> None:
        """Beginnt einen Abschnitt und setzt den Zähler zurück.

        Der Zähler MUSS mit zurückgesetzt werden: Ohne das stünde beim
        Übergang von einer kurzen zu einer langen Phase kurzzeitig der alte,
        bereits vollständige Stand unter der neuen Überschrift.

        ``unit`` darf je Phase wechseln — der CSV-Import zählt erst Zeilen
        und danach Monate. Ohne Angabe bleibt die Einheit des Auftrags.
        """
        with self.lock:
            self.phase_label = phase_label
            self.total = total
            self.done = 0
            self.detail = ""
            if unit is not None:
                self.unit = unit

    def advance(self, done: int | None = None, detail: str | None = None) -> None:
        """Setzt den Stand (``done``) oder zählt um eins hoch (ohne Argument)."""
        with self.lock:
            self.done = self.done + 1 if done is None else done
            if detail is not None:
                self.detail = detail

    def set_total(self, total: int) -> None:
        with self.lock:
            self.total = total

    # -- Lebenszyklus -----------------------------------------------------

    @contextmanager
    def claim(self, kind: str = "") -> Iterator[None]:
        """Belegt den Auftrag oder wirft :class:`JobBusy`.

        Bewusst getrennt vom eigentlichen Lauf: Das Belegen passiert noch im
        Request-Thread, damit ein zweiter Klick sofort eine Antwort bekommt
        statt erst, nachdem ein zweiter Thread schon gestartet wäre.

        ``kind`` unterscheidet Spielarten desselben Auftrags, die sich einen
        Zustand teilen — beim CSV-Import etwa "dry-run" und "start", die
        dieselbe Datei anfassen und deshalb nie gleichzeitig laufen dürfen,
        aber verschiedene Ergebnisvorlagen haben. Es wird hier und nicht erst
        im Ergebnis gesetzt, damit auch ein ABGEBROCHENER Lauf noch sagen
        kann, welche Vorlage seinen Fehler anzeigen soll.

        ACHTUNG: Ein sauberes Verlassen des Blocks gibt die Belegung NICHT
        wieder frei — das tut allein :meth:`run` in seinem ``finally``. Beide
        gehören deshalb zusammen, und genau dafür gibt es :meth:`start`. Wer
        ``claim()`` allein benutzt, hinterlässt einen Auftrag, der bis zum
        Neustart als laufend gilt.
        """
        with self.lock:
            if self.running:
                raise JobBusy(f"{self.name} läuft bereits")
            self.started = True
            self.running = True
            self.kind = kind
            self.phase_label = ""
            self.done = 0
            self.total = 0
            self.detail = ""
            self.error = ""
            self.result = None
            self.finished_at = 0.0
        try:
            yield
        except BaseException:
            # Schlägt schon das Einreihen fehl (z. B. weil das Formular
            # unbrauchbar ist), darf die Belegung nicht bestehen bleiben —
            # sonst wäre die Aktion bis zum Neustart blockiert.
            with self.lock:
                self.running = False
                self.started = False
            raise

    def run(self, arbeit: Callable[[], Any], logger: Any = None) -> None:
        """Führt ``arbeit`` aus und hinterlässt in JEDEM Fall einen Endzustand.

        Der Rückgabewert von ``arbeit`` landet in ``result``. Eine Ausnahme
        landet in ``error`` — nicht im Nichts: Ohne diesen Zweig endete ein
        fehlgeschlagener Auftrag als Anzeige, die bei 40 % stehen bleibt und
        nie wieder verschwindet.
        """
        try:
            ergebnis = arbeit()
        except Exception as fehler:  # noqa: BLE001 — bewusst alles, siehe Docstring
            if logger is not None:
                logger.exception("%s fehlgeschlagen · event=job_failed job=%s", self.name, self.name)
            with self.lock:
                self.error = str(fehler)[:2000] or fehler.__class__.__name__
        else:
            with self.lock:
                self.result = ergebnis
        finally:
            with self.lock:
                self.running = False
                self.finished_at = time.time()

    def start(self, arbeit: Callable[[], Any], logger: Any = None, kind: str = "") -> None:
        """Belegt den Auftrag und startet ihn in einem Daemon-Thread."""
        with self.claim(kind):
            threading.Thread(
                target=self.run,
                args=(arbeit, logger),
                name=f"zeitarchiv-{self.name}",
                daemon=True,
            ).start()

    # -- Lesende Seite ----------------------------------------------------

    def snapshot(self) -> dict:
        """Ein zusammenhängender Blick auf den Zustand, für Vorlage und Route."""
        with self.lock:
            total = self.total
            done = min(self.done, total) if total else self.done
            return {
                "started": self.started,
                "running": self.running,
                "kind": self.kind,
                "phase_label": self.phase_label,
                "done": done,
                "total": total,
                "unit": self.unit,
                "detail": self.detail,
                "error": self.error,
                "result": self.result,
                "finished_at": self.finished_at,
                # Ohne bekannte Gesamtzahl gibt es keinen ehrlichen Prozentwert.
                # 0 statt einer geschätzten Zahl: der Balken bleibt dann leer
                # und die Zeile darüber trägt die Information.
                "percent": int(done / total * 100) if total else 0,
            }
