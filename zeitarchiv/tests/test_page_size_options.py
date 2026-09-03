"""Regressionstests für die einheitlichen Seitengrößen aller Listen."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
FRAGMENT_TEMPLATES = ("_entities_table.html", "_rows_table.html")


def test_all_list_size_selects_offer_1000_instead_of_unlimited() -> None:
    for name in FRAGMENT_TEMPLATES:
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "[10, 50, 100, 500, 1000]" in source, name
        assert '<option value="0">Alle</option>' not in source, name

    import_source = (TEMPLATES / "import.html").read_text(encoding="utf-8")
    assert '<option value="1000">1000 / Seite</option>' in import_source
    assert '<option value="0">Alle</option>' not in import_source


def test_server_and_streaming_pagination_cap_at_1000() -> None:
    main_path = ROOT / "app" / "main.py"
    main = main_path.read_text(encoding="utf-8")
    cleanup = (
        ROOT / "app" / "storage" / "cleanup.py"
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
