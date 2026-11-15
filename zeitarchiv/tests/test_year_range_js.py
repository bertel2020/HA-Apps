"""Jahre-Auswahl der Langzeit-Reiter (static/js/year-range.js), unter node ausgeführt."""
import json
import shutil
import subprocess

import pytest

from _paths import APP

MODULE = APP / "static/js/year-range.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


def _run(expression: str):
    script = f"const y = require({json.dumps(str(MODULE))}); console.log(JSON.stringify({expression}));"
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)


YEARS = "[2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019]"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'all'", {"kind": "all"}), ("0", {"kind": "all"}), ("5", {"kind": "last", "n": 5}), ("'10'", {"kind": "last", "n": 10}),
        ("'2019-2023'", {"kind": "range", "from": 2019, "to": 2023}), ("'2023-2019'", {"kind": "range", "from": 2019, "to": 2023}),
        ("'unsinn'", {"kind": "all"}), ("null", {"kind": "all"}),
    ],
)
def test_parse(value: str, expected: dict) -> None:
    assert _run(f"y.parse({value})") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("'all'", "all"), ("'5'", "5"), ("'10'", "all"),  # zehn bei acht Jahren kürzt nichts
        ("'2021-2024'", "2021-2024"), ("'2010-2024'", "2019-2024"), ("'2019-2026'", "all"), ("'2030-2031'", "all"),
    ],
)
def test_normalize_checks_the_selection_against_the_available_years(value: str, expected: str) -> None:
    assert _run(f"y.normalize({value}, {YEARS})") == expected


def test_filter_works_on_numbers_texts_and_objects_in_both_orders() -> None:
    assert _run(f"y.filter({YEARS}, '5')") == [2026, 2025, 2024, 2023, 2022]
    assert _run(f"y.filter({YEARS}, '2021-2023')") == [2023, 2022, 2021]
    assert _run("y.filter(['2019', '2020', '2021', '2022'], '2', true)") == ["2019", "2020", "2021", "2022"]
    assert _run("y.filter(['2019', '2020', '2021', '2022'], '5', true)") == ["2019", "2020", "2021", "2022"]
    assert _run("y.filter(['2019', '2020', '2021', '2022', '2023', '2024'], '5', true)") == ["2020", "2021", "2022", "2023", "2024"]
    assert _run("y.filter([{year: 2024}, {year: 2023}, {year: 2022}], '2023-2024')") == [{"year": 2024}, {"year": 2023}]
    assert _run(f"y.filter({YEARS}, 'all')") == [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019]


def test_query_for_the_server() -> None:
    assert _run("y.query('all')") == "years=0"
    assert _run("y.query('5')") == "years=5"
    assert _run("y.query('2019-2023')") == "from=2019&to=2023"


def test_options_hide_ten_below_eleven_years() -> None:
    assert [o["value"] for o in _run("y.options(8)")] == ["5", "all"]
    assert [o["value"] for o in _run("y.options(14)")] == ["5", "10", "all"]
    assert _run("y.options(8)")[-1]["label"] == "Alle (8)"
