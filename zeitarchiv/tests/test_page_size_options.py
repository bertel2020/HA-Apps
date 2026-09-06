"""Regressionstests für die einheitlichen Seitengrößen aller Listen."""

from __future__ import annotations

import ast

from _paths import APP, TEMPLATES



FRAGMENT_TEMPLATES = ("_entities_table.html", "_rows_table.html")


def test_all_list_size_selects_offer_1000_instead_of_unlimited() -> None:
    for name in FRAGMENT_TEMPLATES:
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "[10, 20, 50, 100, 500, 1000]" in source, name
        assert '<option value="0">Alle</option>' not in source, name

    import_source = (TEMPLATES / "import.html").read_text(encoding="utf-8")
    assert '<option value="1000">1000 / Seite</option>' in import_source
    assert '<option value="0">Alle</option>' not in import_source


def test_lists_start_with_twenty_rows() -> None:
    """20 statt 50 als Vorgabe: auf dem Telefon ist jede Zeile eine Karte, 50
    davon sind rund fünfzehn Bildschirmlängen. Am Schreibtisch kostet es einen
    Klick mehr, mobil spart es sehr viel Scrollweg — und wer mehr will, findet
    die Auswahl direkt neben der Seitenzahl.

    Geprüft wird die Signatur, nicht ein Textfund: eine vergessene Route fiele
    sonst nicht auf."""
    for name in ("main.py", "import_routes.py", "report_routes.py", "housekeeping_routes.py"):
        tree = ast.parse((APP / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            defaults = dict(zip([a.arg for a in args.args][-len(args.defaults):] if args.defaults else [],
                                args.defaults))
            defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
            value = defaults.get("page_size")
            if value is None:
                continue
            if isinstance(value, ast.Constant):
                found = value.value
            elif isinstance(value, ast.Call):  # Query(default=..., ...)
                found = next((kw.value.value for kw in value.keywords if kw.arg == "default"), None)
            else:
                continue
            # Die Backup-Liste bleibt bei 10: wenige, dafür hohe Zeilen mit
            # Aktionsknöpfen.
            assert found in (10, 20), f"{name}:{node.name} hat page_size={found}"


def test_server_and_streaming_pagination_cap_at_1000() -> None:
    main_path = APP / "main.py"
    main = main_path.read_text(encoding="utf-8")
    cleanup = (
        APP / "storage" / "cleanup.py"
    ).read_text(encoding="utf-8")
    assert "page_size = 1000 if page_size <= 0 else min(page_size, 1000)" in main
    assert "page_size = max(1, min(int(page_size), 1000))" in cleanup

    tree = ast.parse(main)
    paginate_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_paginate"
    )
    namespace: dict[str, object] = {}
    exec(compile(ast.Module([paginate_node], type_ignores=[]), main_path, "exec"), namespace)
    paginate = namespace["_paginate"]
    rows, pagination = paginate(list(range(1_500)), page=1, page_size=0)
    assert len(rows) == 1_000
    assert pagination["page_size"] == 1_000
    assert pagination["total_pages"] == 2
