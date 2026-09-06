"""Blockierende Arbeit darf nicht im Event-Loop landen.

Die App bedient ihre schweren Routen bewusst als synchrone `def` — FastAPI
schiebt die in einen Threadpool. Wo eine Route trotzdem `async def` sein muss
(Formulare, Uploads), wird die eigentliche Arbeit über `run_in_threadpool()`
ausgelagert. Diese Datei hält genau diese Aufteilung fest.

Der Anlass ist keine Stilfrage. `StorageCoordinator.exclusive()` und
`.entity()` warten per Default UNBEGRENZT (`Condition.wait_for(timeout=None)`),
bis keine andere Speicheroperation mehr läuft. Im Threadpool blockiert das
einen Worker; im Event-Loop blockiert es den Prozess — dann nimmt der Server
gar keine Anfrage mehr an, auch `/api/health` nicht, solange z. B. ein Import
oder ein Retention-Lauf den exklusiven Zugriff hält.
"""

from __future__ import annotations

import ast

from _paths import APP

# Was ausschließlich synchron aufgerufen werden darf. Der Coordinator steht
# hier wegen des unbegrenzten Wartens; storage_locked() nimmt darüber hinaus
# selbst ein Lock und ist als Dekorator für Koroutinen wirkungslos (siehe
# test_storage_locked_is_never_applied_to_a_coroutine unten).
BLOCKING_SUFFIXES = ("exclusive", "entity", "entities")


def _async_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            yield node


def _own_body(node: ast.AST) -> list[ast.AST]:
    """Alles im Rumpf AUSSER dem, was in verschachtelten `def`s steht.

    Genau dort liegt die Grenze: eine innere synchrone Funktion ist der
    übliche Weg, blockierende Arbeit an `run_in_threadpool()` zu übergeben —
    was in ihr steht, läuft also gerade NICHT im Event-Loop.
    """
    nested: set[int] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.FunctionDef) and child is not node:
            for inner in ast.walk(child):
                nested.add(id(inner))
    return [n for n in ast.walk(node) if id(n) not in nested]


def _takes_a_storage_lock(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            call = ast.unparse(child.func)
            if call.split(".")[-1] in BLOCKING_SUFFIXES and "coordinator" in call.lower():
                return True
    return False


def test_no_coroutine_takes_a_storage_lock_on_the_event_loop() -> None:
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _async_functions(tree):
            body = _own_body(func)
            for node in body:
                if not isinstance(node, ast.Call):
                    continue
                call = ast.unparse(node.func)
                if call.split(".")[-1] in BLOCKING_SUFFIXES and "coordinator" in call.lower():
                    offenders.append(f"{path.name}:{node.lineno} {func.name}() nimmt {call}() direkt")

            # Eine innere def ist nur dann eine Auslagerung, wenn sie auch
            # ausgelagert WIRD. Direkt aufgerufen läuft ihr Inhalt genauso im
            # Event-Loop — das ist derselbe Fehler, nur eine Ebene tiefer
            # versteckt, und ohne diese Prüfung fiele er hier nicht auf.
            locking = {
                inner.name
                for inner in ast.walk(func)
                if isinstance(inner, ast.FunctionDef) and inner is not func and _takes_a_storage_lock(inner)
            }
            for node in body:
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in locking:
                    offenders.append(
                        f"{path.name}:{node.lineno} {func.name}() ruft {node.func.id}() direkt auf, "
                        "statt es an run_in_threadpool() zu übergeben"
                    )

    assert not offenders, (
        "Coordinator-Lock im Event-Loop — in eine innere def verschieben und "
        "per run_in_threadpool() aufrufen: " + "; ".join(offenders)
    )


def test_storage_locked_is_never_applied_to_a_coroutine() -> None:
    """`storage_locked()` wickelt den Handler in ein synchrones `wrapped()`.

    Auf eine `async def` angewandt nähme es das Lock, erzeugte sofort das
    Koroutinen-Objekt und gäbe das Lock wieder frei, BEVOR auch nur eine Zeile
    des Handlers gelaufen ist — der Handler liefe also vollständig ungesperrt.
    Das fällt nirgends als Fehler auf, es serialisiert nur nichts mehr.
    """
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _async_functions(tree):
            for decorator in func.decorator_list:
                if "storage_locked" in ast.unparse(decorator):
                    offenders.append(f"{path.name}:{func.lineno} {func.name}()")
    assert not offenders, (
        "storage_locked() auf einer Koroutine ist wirkungslos: " + "; ".join(offenders)
    )
