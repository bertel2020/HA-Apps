#!/usr/bin/env python3
"""CLI-Hülle um app/demo_generation.py::run_generation() — der eigentliche
Simulationskern lebt dort, weil ihn seit dem Demo-Modus (siehe
DEMO_MODUS_PLAN.md im Projekt-Wurzelverzeichnis) auch die App selbst braucht
(Einstellungen → Demo-Daten), nicht nur diese CLI.

Erzeugt Demo-Daten für eine leere/neue Zeitarchiv-Instanz. Kein laufender
Server nötig; einfach danach ZEITARCHIV_DATA_DIR auf das Zielverzeichnis
zeigen lassen (siehe docs/development.md) bzw. eine bereits laufende Instanz
neu starten, damit sie die neuen Dateien einliest.

Nicht gegen ein Datenverzeichnis laufen lassen, dessen Server GLEICHZEITIG
läuft — SQLite-Zugriffe aus zwei Prozessen parallel sind nicht vorgesehen.
(Der In-App-Weg über den Demo-Modus umgeht das strukturell: dort läuft
run_generation() im selben Prozess wie der Server, siehe DEMO_MODUS_PLAN.md.)

Beispiele:
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo --months 12 --clean
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo --append

--append ergänzt eine bereits vorhandene Demo-Instanz um die Werte seit dem
letzten Lauf statt die komplette Historie neu zu würfeln — z. B. per Cron
regelmäßig ausgeführt, bleibt eine Demo-Instanz so ein "lebendes" System, das
nie hinter das aktuelle Datum zurückfällt. Zähler- und Schalter-Entitäten
knüpfen dabei an ihren zuletzt gespeicherten Wert an (kein Zählersprung/
-rücksetzer beim Fortsetzen); überlappende Zeitstempel werden von
import_rows() ohnehin dedupliziert, ein Sicherheitsabstand ist also
unkritisch. Ohne vorhandene Demo-Daten fällt --append automatisch auf eine
normale Vollerzeugung (--months) zurück.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demo_generation import DEMO_ENTITIES, run_generation  # noqa: E402
from app.storage.entity_removal import delete_all_values, delete_entity  # noqa: E402
from app.storage.index import Index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Zeitarchiv-Datenverzeichnis (wird bei Bedarf angelegt)")
    parser.add_argument("--months", type=int, default=6, help="Wie viele Monate Historie erzeugt werden (Standard: 6)")
    parser.add_argument("--tz", default="Europe/Berlin", help="IANA-Zeitzone (Standard: Europe/Berlin)")
    parser.add_argument("--seed", type=int, default=42, help="Zufalls-Seed für reproduzierbare Läufe (Standard: 42)")
    parser.add_argument("--clean", action="store_true", help="Werte vorhandener demo_*-Entitäten im Zielverzeichnis zuerst bereinigen (Entitäten selbst bleiben bestehen)")
    parser.add_argument("--append", action="store_true", help="Statt die komplette Historie neu zu würfeln: nur die Werte seit dem letzten Lauf ergänzen (--months wird dabei ignoriert) — macht aus der Demo-Instanz ein 'lebendes' System, z. B. per Cron. Ohne vorhandene Demo-Daten wird automatisch auf eine normale Vollerzeugung zurückgefallen")
    parser.add_argument(
        "--clear", nargs="?", choices=["values", "entities"], const="entities", default=None,
        help=(
            "Eigenständige Aktion statt Erzeugen: löscht vorhandene demo_*-"
            "Entitäten und beendet sich danach, OHNE etwas neu zu erzeugen "
            "(anders als --clean, das Werte bereinigt UND direkt anschließend "
            "die Historie neu würfelt). 'values' entfernt nur die Werte "
            "(Entität/Konfiguration bleibt bestehen, wie der Bereinigungs-"
            "schritt von --clean für sich allein). 'entities' entfernt die "
            "demo_*-Entitäten vollständig inkl. Konfiguration — Dashboards/"
            "Referenzen auf diese Entity-IDs zeigen danach ins Leere. Ohne "
            "Wert (nur '--clear') gilt ebenfalls 'entities'. Schließt sich "
            "mit --clean/--append/--months aus."
        ),
    )
    args = parser.parse_args()
    if args.append and args.clean:
        parser.error("--append und --clean schließen sich gegenseitig aus.")
    if args.clear and (args.clean or args.append):
        parser.error("--clear schließt sich mit --clean und --append gegenseitig aus.")

    tz = ZoneInfo(args.tz)
    index = Index(args.data_dir / "index.sqlite")

    if args.clear:
        existing = {e["entity_id"] for e in index.list_entities()}
        wanted = {e.entity_id for e in DEMO_ENTITIES}
        targets = existing & wanted
        if args.clear == "values":
            for entity_id in targets:
                delete_all_values(args.data_dir, index, entity_id)
            print(f"{len(targets)} vorhandene Demo-Entität(en) geleert (nur Werte entfernt, "
                  "Konfiguration bleibt bestehen).")
        else:
            for entity_id in targets:
                delete_entity(args.data_dir, index, entity_id)
            print(f"{len(targets)} vorhandene Demo-Entität(en) vollständig entfernt (inkl. Konfiguration).")
        return

    rng = random.Random(args.seed)

    result = run_generation(
        args.data_dir, index, tz, rng,
        months=args.months, append=args.append, clean=args.clean,
        on_entity=lambda _i, entity_id, n: print(f"{entity_id}: {n} Werte geschrieben"),
        on_status=print,
    )

    if result.skipped:
        print(result.skip_reason)
        return

    print(f"Indexabgleich: {result.entities_checked} Entität(en) geprüft, "
          f"{result.mismatches_repaired} Abweichung(en) repariert.")
    print(f"\nFertig. ZEITARCHIV_DATA_DIR={args.data_dir} beim Start der App verwenden "
          f"(siehe docs/development.md) bzw. eine bereits laufende Instanz auf diesem "
          f"Datenverzeichnis neu starten.")


if __name__ == "__main__":
    main()
