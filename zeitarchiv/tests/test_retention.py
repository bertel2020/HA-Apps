"""Tests für app/storage/retention.py — Aufbewahrung durchsetzen (Konzept
"Offene Punkte": bisher wurde die Frist nur gespeichert, nie durchgesetzt).
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, retention, rollup
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def _write_archive_month(tmp: Path, entity_id: str, year: int, month: int, rows: list[tuple[float, float]]) -> Path:
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{year:04d}-{month:02d}.parquet"
    table = pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]})
    pq.write_table(table, path, compression="zstd")
    return path


def _write_rollup(tmp: Path, entity_id: str, level: str, bucket_starts: list[float]) -> Path:
    path = rollup.rollup_path(tmp, entity_id, level)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "bucket_start": bucket_starts,
            "value": [1.0] * len(bucket_starts),
            "min_value": [1.0] * len(bucket_starts),
            "max_value": [1.0] * len(bucket_starts),
        }
    )
    pq.write_table(table, path, compression="zstd")
    return path


def test_enforce_retention_deletes_whole_expired_months_only_and_updates_index() -> None:
    """Ein Monat wird nur gelöscht, wenn er KOMPLETT vor dem Cutoff liegt — ein
    nur teilweise abgelaufener Monat (hier Juli, dessen erste Tage zwar älter
    als 30 Tage sind, dessen letzter Tag aber noch innerhalb der Frist liegt)
    bleibt unangetastet, das ist die bewusste Monats-Granularität (kein
    Parquet-Rewrite bei der Durchsetzung)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")

        june_rows = [(_ts(2024, 6, 10, 8), 20.0), (_ts(2024, 6, 20, 8), 21.0)]
        july_rows = [(_ts(2024, 7, 5, 8), 22.0), (_ts(2024, 7, 25, 8), 23.0)]
        june_path = _write_archive_month(tmp, entity_id, 2024, 6, june_rows)
        july_path = _write_archive_month(tmp, entity_id, 2024, 7, july_rows)
        index.add_row_count(entity_id, len(june_rows) + len(july_rows))
        index.set_first_ts(entity_id, june_rows[0][0])

        _write_rollup(tmp, entity_id, "stunde", [_ts(2024, 6, 10, 8), _ts(2024, 7, 5, 8)])
        _write_rollup(tmp, entity_id, "monat", [_ts(2024, 6, 1, 0), _ts(2024, 7, 1, 0)])

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)  # Cutoff (30 Tage zurück) = 2024-07-16
        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "30d", TZ, now)

        assert result["months_deleted"] == 1
        assert result["rows_deleted"] == 2
        assert not june_path.exists()
        assert july_path.exists(), "Juli reicht noch in die Frist hinein — darf nicht gelöscht werden"

        stunde_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "stunde"))
        assert stunde_table.column("bucket_start").to_pylist() == [_ts(2024, 7, 5, 8)]
        monat_table = pq.read_table(rollup.rollup_path(tmp, entity_id, "monat"))
        assert monat_table.column("bucket_start").to_pylist() == [_ts(2024, 7, 1, 0)]

        entity = index.get_entity(entity_id)
        assert entity["row_count"] == 2  # 4 ursprünglich - 2 gelöschte Juni-Zeilen
        assert entity["first_ts"] == july_rows[0][0]  # neuer frühester Wert: erste verbliebene Juli-Zeile

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_prunes_expired_hot_buffer_rows() -> None:
    """Bei sehr kurzer Frist (30 Tage) UND "now" nahe am Monatsende kann der
    Cutoff auch mitten in den laufenden Monat fallen — dann müssen einzelne
    Hot-Buffer-Zeilen physisch entfernt werden, nicht nur ganze Archiv-Monate."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")

        now = datetime(2024, 8, 31, 12, tzinfo=TZ)  # Cutoff (30 Tage zurück) = 2024-08-01 12:00
        expired_ts = _ts(2024, 8, 1, 8)  # vor dem Cutoff
        kept_ts = _ts(2024, 8, 15, 8)  # nach dem Cutoff
        hotbuffer.append(tmp, entity_id, expired_ts, 20.0, TZ)
        hotbuffer.append(tmp, entity_id, kept_ts, 21.0, TZ)
        index.add_row_count(entity_id, 2)

        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "30d", TZ, now)

        assert result["rows_deleted"] == 1
        hot_file = hotbuffer.hot_path(tmp, entity_id, now.timestamp(), TZ)
        assert hotbuffer.read_rows(hot_file) == [(kept_ts, 21.0)]
        assert index.get_entity(entity_id)["row_count"] == 1

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_skips_unlimited_retention() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        assert index.get_entity(entity_id)["retention"] == "unlimited"

        old_path = _write_archive_month(tmp, entity_id, 2020, 1, [(_ts(2020, 1, 1, 0), 1.0)])
        index.add_row_count(entity_id, 1)

        now = datetime(2024, 8, 15, tzinfo=TZ)
        result = retention.enforce_retention_for_entity(tmp, index, entity_id, "unlimited", TZ, now)

        assert result == {"rows_deleted": 0, "bytes_freed": 0, "months_deleted": 0}
        assert old_path.exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enforce_retention_all_sums_across_entities_and_skips_unlimited() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        limited_id = "sensor.limited"
        unlimited_id = "sensor.unlimited"
        index.get_or_create_entity(limited_id, "sensor", "measurement", "°C")
        index.set_config(limited_id, retention="30d")
        index.get_or_create_entity(unlimited_id, "sensor", "measurement", "°C")

        _write_archive_month(tmp, limited_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        _write_archive_month(tmp, unlimited_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        index.add_row_count(limited_id, 1)
        index.add_row_count(unlimited_id, 1)

        now = datetime(2024, 8, 15, tzinfo=TZ)
        totals = retention.enforce_retention_all(tmp, index, TZ, now=now)

        assert totals["entities_affected"] == 1
        assert totals["rows_deleted"] == 1
        assert totals["months_deleted"] == 1
        assert not (tmp / "archive" / limited_id / "2024-01.parquet").exists()
        assert (tmp / "archive" / unlimited_id / "2024-01.parquet").exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_matches_enforcement_without_deleting_files() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-preview-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.preview"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.set_config(entity_id, retention="30d")
        old_path = _write_archive_month(
            tmp, entity_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0), (_ts(2024, 1, 11, 8), 2.0)]
        )
        index.add_row_count(entity_id, 2)
        now = datetime(2024, 8, 15, tzinfo=TZ)

        preview = retention.preview_retention_all(tmp, index, TZ, now=now)
        assert preview["rows_deleted"] == 2
        assert preview["months_deleted"] == 1
        assert preview["entities_affected"] == 1
        assert old_path.exists(), "Die Vorschau darf keine Datei verändern"
        assert index.get_entity(entity_id)["row_count"] == 2

        actual = retention.enforce_retention_all(tmp, index, TZ, now=now)
        assert actual == preview
        assert not old_path.exists()
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_overview_groups_due_data_and_next_expiration_by_policy() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-retention-overview-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        due_id = "sensor.due"
        future_id = "sensor.future"
        index.get_or_create_entity(due_id, "sensor", "measurement", "°C")
        index.set_config(due_id, retention="30d")
        index.get_or_create_entity(future_id, "sensor", "measurement", "°C")
        index.set_config(future_id, retention="90d")
        _write_archive_month(tmp, due_id, 2024, 1, [(_ts(2024, 1, 10, 8), 1.0)])
        _write_archive_month(tmp, future_id, 2024, 7, [(_ts(2024, 7, 10, 8), 1.0)])
        index.add_row_count(due_id, 1)
        index.add_row_count(future_id, 1)

        now = datetime(2024, 8, 15, 12, tzinfo=TZ)
        overview = retention.preview_retention_overview(tmp, index, TZ, now=now)
        groups = {row["retention"]: row for row in overview["groups"]}

        assert overview["totals"]["rows_deleted"] == 1
        assert overview["totals"]["entities_affected"] == 1
        assert groups["30d"]["rows_due"] == 1
        assert groups["30d"]["entities_due"] == 1
        assert groups["90d"]["rows_due"] == 0
        assert groups["90d"]["next_expiration_ts"] is not None
        assert overview["generated_at"] == now.timestamp()
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    if not _PYARROW_AVAILABLE:
        print("übersprungen: pyarrow nicht installiert (siehe addon/requirements.txt)")
        return
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()


# --- Beschnitt über Kalenderintervalle statt Zeile für Zeile (ZP-010) -------

def _alte_maske_monate(starts: list[float], monate: set[tuple[int, int]]) -> list[bool]:
    """Die Fassung vor ZP-010, wortgetreu: zwei datetime-Objekte je Zeile.

    Steht hier als Referenz, nicht als Erinnerungsstück — der Umbau ist nur
    dann harmlos, wenn er für jede Eingabe DASSELBE liefert, und das lässt
    sich nur gegen das Original prüfen.
    """
    return [
        (datetime.fromtimestamp(s, TZ).year, datetime.fromtimestamp(s, TZ).month) not in monate
        for s in starts
    ]


def _schreibe(path: Path, starts: list[float]) -> None:
    pq.write_table(
        pa.table({"bucket_start": starts, "value": [1.0] * len(starts)}), path
    )


def test_interval_pruning_matches_the_row_by_row_original() -> None:
    """Differenztest gegen die alte Zeilen-Maske über eine Fallmatrix.

    Vor ZP-010 gab es für diese vier Funktionen keinen einzigen Test — eine
    grüne Suite sagte über den löschenden Pfad also nichts."""
    faelle = [
        ("gemischt über Jahresgrenze", [_ts(2024, 12, 31, 23), _ts(2025, 1, 1, 0), _ts(2025, 1, 31, 23)]),
        ("nur Dezember", [_ts(2024, 12, 1, 0), _ts(2024, 12, 15, 12), _ts(2024, 12, 31, 23)]),
        ("Sommerzeitbeginn", [_ts(2024, 3, 31, 1), _ts(2024, 3, 31, 4), _ts(2024, 4, 1, 0)]),
        ("Winterzeitende", [_ts(2024, 10, 27, 1), _ts(2024, 10, 27, 4), _ts(2024, 11, 1, 0)]),
        ("exakt auf den Grenzen", [_ts(2024, 5, 1, 0), _ts(2024, 6, 1, 0)]),
        ("eine Sekunde vor der Grenze", [_ts(2024, 6, 1, 0) - 1, _ts(2024, 6, 1, 0)]),
        ("alles im gelöschten Monat", [_ts(2024, 5, d, 12) for d in range(1, 29)]),
        ("nichts im gelöschten Monat", [_ts(2023, 5, 1, 12), _ts(2025, 5, 1, 12)]),
        ("eine einzige Zeile", [_ts(2024, 5, 15, 12)]),
        ("leere Datei", []),
    ]
    # Dezember gehört zwingend dazu: Nur dort wechselt period_span() das Jahr,
    # und ein Fehler darin bliebe sonst unbemerkt — das Intervall wäre leer und
    # es würde stillschweigend NICHTS gelöscht.
    monatsmengen = [{(2024, 5)}, {(2024, 3)}, {(2024, 10)}, {(2025, 1)},
                    {(2024, 12)}, {(2024, 12), (2025, 1)},
                    {(2024, 5), (2024, 6)}, {(1999, 1)}]

    with tempfile.TemporaryDirectory() as tmp:
        for name, starts in faelle:
            for monate in monatsmengen:
                erwartet_maske = _alte_maske_monate(starts, monate)
                if starts and all(erwartet_maske):
                    erwartet = list(starts)                       # unverändert
                elif starts and not any(erwartet_maske):
                    erwartet = None                               # Datei entfällt
                else:
                    erwartet = [s for s, k in zip(starts, erwartet_maske) if k]

                path = Path(tmp) / "monat.parquet"
                _schreibe(path, starts)
                rollup.drop_rows_in_spans(
                    path, None, [rollup.period_span(TZ, j, m) for j, m in monate]
                )

                if erwartet is None:
                    assert not path.exists(), f"{name} / {sorted(monate)}: Datei müsste weg sein"
                    continue
                assert path.exists(), f"{name} / {sorted(monate)}: Datei wurde zu Unrecht gelöscht"
                tatsaechlich = pq.read_table(path).column("bucket_start").to_pylist()
                assert tatsaechlich == erwartet, f"{name} / {sorted(monate)}"


def test_an_empty_rollup_file_is_left_alone() -> None:
    """pc.all() liefert auf einer leeren Maske weder True noch False, sondern
    Null. Ohne eigenen Ausgang fiele eine leere Datei in den Zweig "nichts
    bleibt übrig" und würde GELÖSCHT — die Fassung davor ließ sie liegen
    (all([]) ist True). Genau daran ist mein erster Entwurf gescheitert."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "monat.parquet"
        _schreibe(path, [])
        rollup.drop_rows_in_spans(path, None, [rollup.period_span(TZ, 2024, 5)])
        assert path.exists(), "eine leere Rollup-Datei darf nicht verschwinden"


def test_a_calendar_month_is_a_clean_interval_in_every_timezone() -> None:
    """period_span() ersetzt "Jahr und Monat dieser Zeile" durch ein Intervall.
    Das gilt nur, wenn Monatsgrenzen überall sauber liegen — und es gibt
    Zonen, in denen die lokale Mitternacht des Ersten wegen einer Umstellung
    gar nicht existiert (Africa/Cairo 2014-08 springt auf 01:00). Geprüft
    werden genau diese Grenzen, sekundenweise."""
    from zoneinfo import available_timezones

    kandidaten = ["Africa/Cairo", "Africa/Casablanca", "Africa/Algiers", "America/Santiago",
                  "Asia/Beirut", "Europe/Berlin", "Pacific/Apia"]
    vorhanden = available_timezones()
    geprueft = 0
    for name in [z for z in kandidaten if z in vorhanden]:
        tz = ZoneInfo(name)
        for jahr in range(2000, 2031):
            for monat in range(1, 13):
                a, b = rollup.period_span(tz, jahr, monat)
                lokal = datetime.fromtimestamp(a, tz)
                if (lokal.day, lokal.hour) == (1, 0):
                    continue  # unauffällige Grenze, die decken die Fälle oben ab
                geprueft += 1
                for t in range(int(a) - 3600, int(a) + 3601):
                    d = datetime.fromtimestamp(t, tz)
                    assert (a <= t < b) == ((d.year, d.month) == (jahr, monat)), f"{name} {jahr}-{monat}"
    assert geprueft, "keine einzige auffällige Monatsgrenze gefunden — Prüfung wäre wirkungslos"


def _jahres_baum(tmp: Path, entity_id: str, archiv_monate: list[tuple[int, int]],
                 jahre: list[int]) -> None:
    """Archivmonate als Dateien + jahr.parquet mit einer Zeile je Jahr."""
    archiv = tmp / "archive" / entity_id
    archiv.mkdir(parents=True, exist_ok=True)
    for jahr, monat in archiv_monate:
        pq.write_table(pa.table({"ts": [_ts(jahr, monat, 1, 12)], "value": [1.0]}),
                       archiv / f"{jahr:04d}-{monat:02d}.parquet")
    ziel = tmp / "rollup" / entity_id
    ziel.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"bucket_start": [_ts(j, 1, 1, 0) for j in jahre],
                             "value": [float(j) for j in jahre]}),
                   ziel / "jahr.parquet")


def test_the_year_rollup_row_goes_only_when_the_whole_year_is_gone() -> None:
    """Eine Jahreszeile fasst zwölf Monate zusammen — sie darf erst weg, wenn
    KEIN archivierter Monat des Jahres mehr übrig ist.

    Der Test entstand, weil eine Mutation zeigte, dass sich _prune_year_rollup()
    komplett abschalten ließ, ohne dass ein einziger Test anschlug."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        entity_id = "sensor.zaehler"

        # 2023 ist restlos aus dem Archiv verschwunden, 2024 nur teilweise.
        _jahres_baum(tmp, entity_id, archiv_monate=[(2024, 6)], jahre=[2023, 2024])
        retention._prune_year_rollup(
            tmp, entity_id, TZ, {(2023, m) for m in range(1, 13)} | {(2024, 1)}
        )
        uebrig = pq.read_table(rollup.rollup_path(tmp, entity_id, "jahr"))
        jahre = [datetime.fromtimestamp(s, TZ).year for s in uebrig.column("bucket_start").to_pylist()]
        assert jahre == [2024], f"2023 müsste weg sein, 2024 bleiben — bekommen: {jahre}"

        # Gegenprobe: bleibt ein Monat des Jahres im Archiv, bleibt die Zeile.
        shutil.rmtree(tmp / "rollup")
        shutil.rmtree(tmp / "archive")
        _jahres_baum(tmp, entity_id, archiv_monate=[(2023, 7)], jahre=[2023])
        retention._prune_year_rollup(tmp, entity_id, TZ, {(2023, 1)})
        uebrig = pq.read_table(rollup.rollup_path(tmp, entity_id, "jahr"))
        assert uebrig.num_rows == 1, "solange ein Monat archiviert ist, bleibt die Jahreszeile"
