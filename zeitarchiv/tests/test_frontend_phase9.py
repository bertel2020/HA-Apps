"""Code-Review Phase 9: Frontend-Korrektheit — Fehlerzustände, Serverzeit, Tastatur, Wächter, Texte.

Die Skripte laufen in node mit einem winzigen Fake-DOM; wo das nicht lohnt, prüft ein Quelltext-Vertrag die Stelle.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from _paths import ADDON, APP, PAGE_JS, TEMPLATES

JS = ADDON / "app" / "static" / "js"
CARD = ADDON.parent / "custom_components" / "zeitarchiv" / "frontend" / "zeitarchiv-card.js"
if not CARD.exists():
    CARD = ADDON / "custom_components" / "zeitarchiv" / "frontend" / "zeitarchiv-card.js"


def node(script: str, tz: str = "UTC") -> object:
    run = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True,
        env={"TZ": tz, "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    return json.loads(run.stdout)


# ---------------------------------------------------------------- A8: Restore-Assistent bei Serverfehler


WIZARD = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};          // Ereignis -> Handler (document)
const clicks = {};             // Element-ID -> Handler
const texts = {};
function element(id) {
  const el = {id, dataset: {}, hidden: false, disabled: false, textContent: '', classList: {toggle() {}, add() {}, remove() {}},
    style: {}, setAttribute() {}, removeAttribute() {}, querySelector: () => null, querySelectorAll: () => [],
    closest: () => null, addEventListener(name, fn) { (clicks[id] ||= {})[name] = fn; }, checked: false, value: ''};
  texts[id] = el;
  return el;
}
const els = {};
const root = element('root');
root.dataset.restoreUrl = '/restore/x'; root.dataset.requestFailed = 'ANFRAGE'; root.dataset.applyFailed = 'ANTWORT';
const form = element('rw');
form.querySelector = (sel) => (sel === 'input[name=mode]:checked' ? {value: 'all'} : null);
['rw-next', 'rw-back', 'rw-apply', 'rw-done', 'rw-cancel', 'rw-info'].forEach((id) => { els[id] = element(id); });
const panels = [];
const document = {
  querySelector: (sel) => (sel === '.rw' ? root : (sel.startsWith('#') ? els[sel.slice(1)] || element(sel) : null)),
  getElementById: (id) => (id === 'rw' ? form : els[id] || null),
  querySelectorAll: () => [],
  addEventListener: (name, fn) => { (listeners[name] ||= new Set()).add(fn); },
  removeEventListener: (name, fn) => { listeners[name] && listeners[name].delete(fn); },
};
const fire = (name) => (listeners[name] || new Set()).forEach((fn) => fn({type: name}));
let outcome = 'ok';
const calls = [];
const htmx = {ajax: (verb, url) => { calls.push(verb + ' ' + url); if (outcome !== 'ok') fire(outcome); return Promise.resolve(); }};
const window = {scrollTo() {}};
new Function('document', 'window', 'htmx', source)(document, window, htmx);
(async () => {
  const wait = () => new Promise((r) => setTimeout(r, 5));
  const scenario = process.argv[2];
  const out = {};
  if (scenario === 'review-fails') outcome = 'htmx:responseError';
  clicks['rw-next'].click();
  await wait();
  if (scenario === 'apply-fails') {
    outcome = 'htmx:timeout';
    clicks['rw-apply'].click();
    await wait();
  }
  out.info = els['rw-info'].textContent;
  out.doneHidden = els['rw-done'].hidden;
  out.applyHidden = els['rw-apply'].hidden;
  out.leftoverListeners = Object.values(listeners).reduce((n, set) => n + set.size, 0);
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.parametrize(
    ("scenario", "info", "done_hidden", "apply_hidden", "calls"),
    [
        ("ok", "", True, False, ["POST /restore/x/review"]),
        ("review-fails", "ANFRAGE", True, True, ["POST /restore/x/review"]),
        ("apply-fails", "ANTWORT", True, False, ["POST /restore/x/review", "POST /restore/x/apply"]),
    ],
)
def test_the_restore_wizard_does_not_jump_to_done_when_the_server_fails(
    scenario: str, info: str, done_hidden: bool, apply_hidden: bool, calls: list[str]
) -> None:
    script = WIZARD.replace("process.argv[1]", json.dumps(str(JS / "backup-restore.js")))
    out = node(script.replace("process.argv[2]", json.dumps(scenario)))
    assert out["info"] == info
    assert out["doneHidden"] is done_hidden   # nie auf "Fertig" gesprungen
    assert out["applyHidden"] is apply_hidden
    assert out["leftoverListeners"] == 0      # die Fehler-Ereignisse sind wieder abgemeldet
    assert out["calls"] == calls


def test_the_wizard_template_carries_both_error_texts() -> None:
    template = (TEMPLATES / "backup_restore.html").read_text(encoding="utf-8")
    assert "data-request-failed=" in template and "data-apply-failed=" in template


# ---------------------------------------------------------------- B29: Dropzones per Tastatur


DROPZONE = r"""
const fs = require('fs');
let handler;
global.document = {addEventListener: (name, fn) => { if (name === 'keydown') handler = fn; }, getElementById: () => null};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const run = (target, key) => { let prevented = false, clicked = 0;
  target.__click = () => { clicked += 1; };
  handler({key, target, preventDefault: () => { prevented = true; }});
  return {prevented, clicked}; };
const input = () => { const el = {click() { el.count = (el.count || 0) + 1; }}; return el; };
const label = (role, inner) => ({tagName: 'LABEL', getAttribute: (n) => (n === 'role' ? role : null), querySelector: () => inner, htmlFor: ''});
const a = input(), b = input(), c = input(), d = input();
handler({key: 'Enter', target: label('button', a), preventDefault() {}});
handler({key: ' ', target: label('button', b), preventDefault() {}});
handler({key: 'a', target: label('button', c), preventDefault() {}});
handler({key: 'Enter', target: label(null, d), preventDefault() {}});
handler({key: 'Enter', target: {tagName: 'BUTTON', getAttribute: () => 'button', querySelector: () => a}, preventDefault() {}});
console.log(JSON.stringify({enter: a.count || 0, space: b.count || 0, otherKey: c.count || 0, noRole: d.count || 0}));
"""


def test_enter_and_space_open_the_file_dialog_of_a_dropzone() -> None:
    out = node(DROPZONE.replace("process.argv[1]", json.dumps(str(JS / "dropzone-keyboard.js"))))
    assert out == {"enter": 1, "space": 1, "otherKey": 0, "noRole": 0}


def test_every_page_with_a_dropzone_loads_the_keyboard_script() -> None:
    assert "js/dropzone-keyboard.js" in (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for name in ("import.html", "_csv_import_section.html", "_settings_backup_ready.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for label in re.findall(r"<label[^>]*(?:dropzone|settings-json)[^>]*>", text):
            assert 'role="button"' in label and 'tabindex="0"' in label, (name, label)
    assert re.search(r'<label class="btn"[^>]*role="button"[^>]*tabindex="0"', (TEMPLATES / "import.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- B26: Sortierung im Zahlenformat der Oberfläche


SORT = r"""
const fs = require('fs');
global.window = global;
global.window.ZA_FORMAT = LOCALE;
eval(fs.readFileSync(NUMBER_FORMAT, 'utf8'));
const source = fs.readFileSync(SORTABLE, 'utf8');
// parseNumber() ist privat in der Hülle des Skripts; ihr Quelltext genügt.
const parse = new Function('window', source.match(/function parseNumber[\s\S]*?\n  }\n/)[0] + '\nreturn parseNumber;')(window);
console.log(JSON.stringify(['1.234', '12.5', '1,234', '12,5', '1.234,5', '1,234.5', '−', 'abc', '-3,5'].map(parse)));
"""


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("de", [1234, 125, 1.234, 12.5, 1234.5, 1.2345, None, None, -3.5]),
        ("en", [1.234, 12.5, 1234, 125, 1.2345, 1234.5, None, None, -35]),
    ],
)
def test_table_sorting_reads_numbers_in_the_format_of_the_interface(fmt: str, expected: list) -> None:
    script = (SORT.replace("LOCALE", json.dumps("en-US" if fmt == "en" else "de-DE"))
              .replace("NUMBER_FORMAT", json.dumps(str(JS / "number-format.js")))
              .replace("SORTABLE", json.dumps(str(JS / "sortable-table.js"))))
    assert node(script) == expected


# ---------------------------------------------------------------- B24: Kalender in der Zeitzone des Servers


STATE_BUCKETS = r"""
const fs = require('fs');
global.window = global;
global.document = {documentElement: {lang: 'de', dataset: {tz: 'Europe/Berlin', format: 'de'}}, body: {}};
global.window.ZA_FORMAT = 'de-DE';
global.getComputedStyle = () => ({getPropertyValue: () => ''});
global.t = (v) => v;
const read = (n) => fs.readFileSync(process.argv[1] + '/' + n, 'utf8');
eval(read('server-time.js')); eval(read('html-escape.js')); eval(read('number-format.js'));
const StateEntities = new Function('window', 'document', 'getComputedStyle', 't', 'NumberFormat', 'escapeHtml',
  read('state-entities.js') + '\nreturn window.StateEntities || StateEntities;')(window, document, getComputedStyle, t, window.NumberFormat, window.escapeHtml);
const JAN = 1767222000;   // 2026-01-01 00:00 Europe/Berlin
const iso = (ts) => new Date(ts * 1000).toISOString();
const out = {};
const [yearStart, yearNext] = StateEntities.bucketFns.year();
out.year = [iso(yearStart(JAN + 86400 * 40)), iso(yearNext(JAN))];
const [monthStart, monthNext] = StateEntities.bucketFns.month();
out.month = [iso(monthStart(JAN + 86400 * 40)), iso(monthNext(JAN))];
const [dayStart, dayNext] = StateEntities.bucketFns.day();
out.day = [iso(dayStart(JAN + 5 * 3600)), iso(dayNext(JAN))];
const [hourStart] = StateEntities.bucketFns.hour();
out.hour = iso(hourStart(JAN + 5 * 3600 + 1234));
console.log(JSON.stringify(out));
"""


@pytest.mark.parametrize("browser_tz", ["Europe/Berlin", "Europe/Lisbon", "America/New_York", "Asia/Tokyo"])
def test_state_buckets_follow_the_calendar_of_the_server_in_every_browser_zone(browser_tz: str) -> None:
    """Server Berlin, Browser Portugal: Der Jahresbucket begann früher eine Stunde zu früh (31.12. 23:00 Berlin → "Dez 2025")."""
    out = node(STATE_BUCKETS.replace("process.argv[1]", json.dumps(str(JS))), tz=browser_tz)
    # Berlin ist im Januar UTC+1: 00:00 Berlin = 23:00 UTC des Vortags.
    assert out["year"] == ["2025-12-31T23:00:00.000Z", "2026-12-31T23:00:00.000Z"]
    assert out["month"] == ["2026-01-31T23:00:00.000Z", "2026-01-31T23:00:00.000Z"]  # Feb-Start, Feb-Start nach Jan
    assert out["day"] == ["2025-12-31T23:00:00.000Z", "2026-01-01T23:00:00.000Z"]
    assert out["hour"] == "2026-01-01T04:00:00.000Z"


MULTI_YEAR = r"""
const fs = require('fs');
global.window = global;
global.document = {documentElement: {lang: 'de', dataset: {tz: 'Europe/Berlin', format: 'de'}}, body: {}};
global.window.ZA_FORMAT = 'de-DE';
const read = (n) => fs.readFileSync(process.argv[1] + '/' + n, 'utf8');
eval(read('server-time.js'));
const src = read('chart-tile-render.js');
// monthlyBuckets() und das Kopfjahr stehen in der Hülle; hier genügt ihr Quelltext.
const monthly = new Function('window', src.match(/function monthlyBuckets[\s\S]*?\n  }\n/)[0] + '\nreturn monthlyBuckets;')(window);
const JAN_BERLIN = 1767222000;
const buckets = monthly([{ts: JAN_BERLIN, value: 5}, {ts: JAN_BERLIN + 86400 * 31, value: 7}], 'counter');
console.log(JSON.stringify({buckets: buckets.slice(0, 3), usesServerYear: /ServerTime\.parts\(data\.window_start\)\.year/.test(src)}));
"""


@pytest.mark.parametrize("browser_tz", ["Europe/Lisbon", "America/New_York"])
def test_monthly_buckets_use_the_server_month(browser_tz: str) -> None:
    out = node(MULTI_YEAR.replace("process.argv[1]", json.dumps(str(JS))), tz=browser_tz)
    assert out["buckets"] == [5, 7, None]  # 1. Januar 00:00 Berlin gehört in den Januar, nicht in den Dezember
    assert out["usesServerYear"] is True


# ---------------------------------------------------------------- Quelltext-Verträge


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["chart_editor.js", "table_editor.js", "dashboard_editor.js"])
def test_save_ignores_a_second_call_while_it_is_running(name: str) -> None:
    source = _source(PAGE_JS / name)
    body = source.split("async save() {", 1)[1]
    first_statements = "\n".join(line for line in body.splitlines()[:6] if not line.strip().startswith("//"))
    assert "if (this.saving) return;" in first_statements
    assert "} catch (error) {" in body.split("async delete", 1)[0].split("finally")[0][-400:] or "appAlert(t('Speichern fehlgeschlagen.'))" in body


@pytest.mark.parametrize(("page", "template"), [("entity_detail.js", "entity_detail.html"), ("chart_editor.js", "chart_editor.html")])
def test_a_failed_chart_load_shows_an_error_with_a_retry_button(page: str, template: str) -> None:
    script = _source(PAGE_JS / page)
    assert "loadError: false" in script and "this.loadError = true;" in script and "this.loadError = false;" in script
    assert "heatmapRes && heatmapRes.ok" in script   # kein .json() auf einer Fehlerantwort
    html = _source(TEMPLATES / template)
    assert "Die Daten konnten nicht geladen werden." in html and 'Erneut versuchen' in html and '@click="load()"' in html


def test_the_zip_progress_polling_survives_a_hiccup_and_the_upload_is_locked() -> None:
    script = _source(PAGE_JS / "import.js")
    assert "MAX_POLL_FAILURES" in script and "if (!r.ok) throw" in script and ".catch(() => {" in script
    assert "if (uploading) return;" in script and "input.disabled = true;" in script


def test_compacting_gives_feedback_and_cannot_be_started_twice() -> None:
    script = _source(PAGE_JS / "cleanup.js")
    assert "async function submitCompact(startTs, endTs, target, button)" in script
    assert "button.disabled = true;" in script and "t('Wird verdichtet …')" in script and "finally {" in script


def test_sortable_headers_are_keyboard_operable_and_announce_their_sort_state() -> None:
    script = _source(JS / "sortable-table.js")
    assert "th.tabIndex = 0;" in script and "aria-sort" in script and "'ascending'" in script and "'descending'" in script
    assert "event.key !== 'Enter' && event.key !== ' '" in script
    assert "dt-sortable-col:focus-visible" in _source(APP / "static" / "css" / "app.css")


def test_the_section_toggle_reports_whether_it_is_expanded() -> None:
    assert "setAttribute('aria-expanded'" in _source(JS / "dashboard-tiles.js")
    assert 'class="dsection-toggle"' in _source(TEMPLATES / "_dashboard_tiles.html")
    assert re.search(r'class="dsection-toggle"[^>]*aria-expanded="true"', _source(TEMPLATES / "_dashboard_tiles.html"))


def test_tooltips_with_a_focusable_target_open_on_focus() -> None:
    script = _source(JS / "fixed-tooltip.js")
    assert "'focusin'" in script and "'focusout'" in script and "aria-describedby" in script and "role', 'tooltip'" in script


def test_slow_answers_cannot_overwrite_newer_ones() -> None:
    energy = _source(JS / "energiedashboard.js")
    assert "const seq = ++this._loadSeq;" in energy and "if (seq !== this._loadSeq) return;" in energy
    assert "const seq = ++this._heatmapSeq;" in energy and "if (seq !== this._heatmapSeq) return;" in energy
    tiles = _source(JS / "dashboard-tiles.js")
    assert tiles.count("nextRenderSeq(el)") >= 3 and "el.dataset.renderSeq !== seq" in tiles


def test_failures_are_reported_instead_of_swallowed() -> None:
    assert "markGrowthRange(shownGrowthRange)" in _source(PAGE_JS / "statistik.js")
    assert "Die Auswahl konnte nicht geladen werden." in _source(PAGE_JS / "export.js")
    assert "Export wird erstellt …" in _source(PAGE_JS / "export.js")
    assert "serverDetail" in _source(PAGE_JS / "entity_migrate.js")
    assert "saved = res.ok;" in _source(JS / "dashboard-tiles.js")
    assert "if (!res.ok && !columnsData) throw" in _source(JS / "chart-tile-render.js")


@pytest.mark.parametrize(
    ("template", "forbidden"),
    [
        ("report_detail.html", ["<strong>Datei:</strong>", "<strong>Report-ID:</strong>", "<strong>Statistik:</strong>", "<strong>Roh:</strong>",
                                "<strong>Quelle:</strong>", "<p>Ziel:", "<p>Quelle:", "<p>Statistikzeitraum:", "v1=('Ja'", "confirm('Diesen"]),
        ("_ha_import_section.html", ["<strong>Roh:</strong>", "<strong>Statistik:</strong>"]),
        ("_dashboard_tiles.html", ['title="Zum Umsortieren ziehen"']),
    ],
)
def test_no_hardcoded_german_is_left_in_the_templates(template: str, forbidden: list[str]) -> None:
    text = _source(TEMPLATES / template)
    for fragment in forbidden:
        assert fragment not in text, f"{template}: {fragment}"


def test_the_warning_badge_uses_the_theme_variables() -> None:
    css = _source(APP / "static" / "css" / "pages" / "energiedashboard.css")
    assert "#F3E4C4" not in css and "#8A6414" not in css


@pytest.mark.skipif(not CARD.exists(), reason="Karte der Integration liegt nicht neben diesem Baum")
def test_the_card_ignores_keys_of_its_legend_and_does_not_draw_after_removal() -> None:
    card = _source(CARD)
    assert "if (event.target !== card) return;" in card
    assert card.count("if (!this.isConnected) return;") >= 2
