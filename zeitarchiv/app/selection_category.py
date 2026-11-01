"""Kategorie beim Übernehmen im Auswahl-Assistenten (ROADMAP 1.42, Phase 2).

Thema (warum archiviert) und Kategorie (wo eingeordnet) bleiben getrennt: Das
Thema liefert nur den Vorschlag, die Kategorie gilt erst, wenn sie im
Bestätigungsschritt stehen bleibt. Fehlt sie, legt das Übernehmen sie an. Die
Zuordnung hängt an der Entitäts-ID und braucht keine Entitätszeile, gilt also
schon vor dem ersten Wert.
"""

from __future__ import annotations

from typing import Any

from .i18n import tr
from .storage.index import DuplicateNameError, Index, InvalidNameError, _normalized_name


def options(index: Index, suggested_name: str) -> dict[str, Any]:
    """Auswahl für den Bestätigungsschritt. ``suggested_name`` ist der Name des
    Themas (leer bei einer Suche). Vorbelegt wird die gleichnamige Kategorie;
    gibt es sie nicht, wird sie als neue angeboten und, sobald es überhaupt
    Kategorien gibt, auch vorgewählt — wer noch keine nutzt, bekommt keine
    ungefragt angelegt."""
    categories = index.list_entity_categories()
    match = next((c for c in categories if suggested_name and _normalized_name(c["name"]) == _normalized_name(suggested_name)), None)
    new_name = "" if match or not suggested_name else suggested_name
    if match:
        selected = f"id:{match['id']}"
    elif new_name and categories:
        selected = f"new:{new_name}"
    else:
        selected = ""
    return {"categories": categories, "new_name": new_name, "selected": selected, "show": bool(categories or new_name)}


def parse(choice: str) -> tuple[str, int | str | None]:
    """„id:5“ → ("id", 5), „new:Wetter“ → ("new", "Wetter"), sonst ("none", None)."""
    kind, _, value = choice.partition(":")
    if kind == "id" and value.isdigit():
        return "id", int(value)
    if kind == "new" and value.strip():
        return "new", value.strip()
    return "none", None


def check(index: Index, choice: str) -> str | None:
    """Fehlertext, wenn die Wahl nicht umsetzbar ist (vor dem Auftrag prüfen,
    damit kein halber Vorgang entsteht); sonst None."""
    kind, value = parse(choice)
    if kind == "id":
        if all(c["id"] != value for c in index.list_entity_categories()):
            return tr("Kategorie nicht gefunden")
    elif kind == "new":
        try:
            index.validate_new_category_name(str(value))
        except DuplicateNameError:
            return None  # gibt es schon: das Übernehmen nimmt die vorhandene
        except InvalidNameError as exc:
            return str(exc)
    return None


def apply(index: Index, choice: str, entity_ids: list[str], *, overwrite: bool) -> int:
    """Ordnet die Entitäten zu, legt eine fehlende Kategorie dabei an. Gibt die
    Zahl der geänderten Entitäten zurück."""
    kind, value = parse(choice)
    if kind == "none":
        return 0
    if kind == "new":
        existing = next((c for c in index.list_entity_categories() if _normalized_name(c["name"]) == _normalized_name(str(value))), None)
        category_id = existing["id"] if existing else index.create_entity_category(str(value))
    else:
        category_id = int(value)  # type: ignore[arg-type]
    return index.set_entities_category(entity_ids, category_id, only_known=False, keep_existing=not overwrite)
