import json
import re
from pathlib import Path

DASHBOARDS = Path(__file__).parent.parent / "grafana" / "dashboards"
DASHBOARD = DASHBOARDS / "meta.json"
DETAIL = DASHBOARDS / "archetype-detail.json"
UPDATES = DASHBOARDS / "updates.json"

SUBS = {
    "$__timeFrom()": "(now() - interval '6 months')",
    "$__timeTo()": "now()",
    "${format:sqlstring}": "'MO'",
    "${grp:sqlstring}": "'Kraum, Ludevic''s Opus'",
    "$format": "MO",
    "${bucket}": "7 days",
    "$bucket": "7 days",
    "${period}": "14 days",
    "$period": "14 days",
    "${top_n}": "10",
    "$top_n": "10",
    "${min_decks}": "5",
    "$min_decks": "5",
    "${grp}": "X",
    "$grp": "X",
}


def _queries(dash: dict) -> list[str]:
    sql = [t["rawSql"] for p in dash["panels"] for t in p.get("targets", []) if t.get("rawSql")]
    sql += [v["query"] for v in dash["templating"]["list"] if v["type"] == "query"]
    return sql


def _substitute(sql: str) -> str:
    for k in sorted(SUBS, key=len, reverse=True):
        sql = sql.replace(k, SUBS[k])
    return sql


def test_dashboard_panel_sql_executes(repo):
    queries = [q for path in (DASHBOARD, DETAIL, UPDATES) for q in _queries(json.loads(path.read_text()))]
    assert len(queries) >= 20
    for sql in queries:
        final = _substitute(sql)
        assert not re.search(r"\$\w", final), final
        with repo.session.begin():
            repo.session.connection().exec_driver_sql(final.replace("%", "%%")).fetchall()  # a literal % is not a placeholder


def test_dashboard_variables_present():
    dash = json.loads(DASHBOARD.read_text())
    assert {v["name"] for v in dash["templating"]["list"]} == {"format", "bucket", "top_n", "period", "min_decks"}
    detail = json.loads(DETAIL.read_text())
    assert {v["name"] for v in detail["templating"]["list"]} == {"format", "grp", "bucket"}


def test_meta_links_to_the_archetype_detail():
    dash = json.loads(DASHBOARD.read_text())
    detail = json.loads(DETAIL.read_text())
    links = [link["url"] for p in dash["panels"] for link in [
        *p["fieldConfig"]["defaults"].get("links", []),
        *(link for o in p["fieldConfig"]["overrides"] for prop in o["properties"] if prop["id"] == "links"
          for link in prop["value"]),
    ]]
    assert len(links) == 3  # Meta share, Performance, Rising decks
    for url in links:
        assert url.startswith(f"/d/{detail['uid']}/")
        assert "var-grp=" in url and "${format:queryparam}" in url and "${__url_time_range}" in url


def _panel_sql(dash: dict, title: str) -> str:
    [panel] = [p for p in dash["panels"] if p["title"] == title]
    return panel["targets"][0]["rawSql"]


def test_last_update_panel(repo):
    from datetime import datetime, timezone

    from conftest import fetch

    dash = json.loads(DASHBOARD.read_text())
    sql = _panel_sql(dash, "Last update")
    assert fetch(repo, sql) == [("never",)]
    started = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
    repo.log_update_run(1, started, "failed", error="RuntimeError: down")
    repo.log_update_run(2, started, "ok", scanned=12)
    [(value,)] = fetch(repo, sql)
    assert value.endswith(" · ok")
    assert fetch(repo, "SELECT attempt, status, scanned, error FROM update_run ORDER BY id") == [
        (1, "failed", None, "RuntimeError: down"), (2, "ok", 12, None),
    ]


def _updates_sql(title: str) -> str:
    return _substitute(_panel_sql(json.loads(UPDATES.read_text()), title))


def _rome_time(repo, days_ago: int, hour: int):
    """A moment of the Europe/Rome day `days_ago` days before today, as the database sees it."""
    from conftest import scalar

    return scalar(repo, "SELECT ((now() AT TIME ZONE 'Europe/Rome')::date - :d + make_interval(hours => :h))"
                        " AT TIME ZONE 'Europe/Rome'", d=days_ago, h=hour)


def _run(repo, started, status, attempt=1, scanned=None, error=None):
    from conftest import fetch

    fetch(repo, "INSERT INTO update_run (attempt, started_at, finished_at, status, scanned, error) "
                "VALUES (:a, :s, :s + interval '90 seconds', :st, :sc, :e) RETURNING id",
          a=attempt, s=started, st=status, sc=scanned, e=error)


def test_updates_today(repo):
    from conftest import fetch

    sql = _updates_sql("Today")
    assert fetch(repo, sql) == [("Not yet",)]
    _run(repo, _rome_time(repo, 1, 4), "ok", scanned=10)
    assert fetch(repo, sql) == [("Not yet",)]
    _run(repo, _rome_time(repo, 0, 4), "failed", error="RuntimeError: MTGTop8 home page unavailable")
    _run(repo, _rome_time(repo, 0, 5), "failed", attempt=2, error="RuntimeError: MTGTop8 home page unavailable")
    assert fetch(repo, sql) == [("Failing · 2 failed",)]
    _run(repo, _rome_time(repo, 0, 6), "ok", attempt=3, scanned=40)
    assert fetch(repo, sql) == [("Updated",)]
    [panel] = [p for p in json.loads(UPDATES.read_text())["panels"] if p["title"] == "Today"]
    patterns = [m["options"]["pattern"] for m in panel["fieldConfig"]["defaults"]["mappings"]]
    assert any(re.fullmatch(p, "Failing · 2 failed") for p in patterns)


def test_updates_attempts(repo):
    from conftest import fetch

    _run(repo, _rome_time(repo, 2, 4), "failed", scanned=150, error="stopped early: too many missing/failed pages in a row")
    _run(repo, _rome_time(repo, 2, 5), "ok", attempt=2, scanned=30)
    assert fetch(repo, _updates_sql("Failed attempts")) == [(1,)]
    [(day, ok, failed)] = fetch(repo, _updates_sql("Attempts per day"))
    assert (ok, failed) == (1, 1)
    rows = fetch(repo, _updates_sql("Attempts"))
    assert [(r[1], r[2], r[4]) for r in rows] == [(2, "ok", 30), (1, "failed", 150)]
    assert float(rows[0][3]) == 90 and rows[1][5].startswith("stopped early")


def test_updates_events_and_errors(repo):
    from datetime import date, timedelta

    from conftest import fetch

    today = date.today()
    fetch(repo, "INSERT INTO format (code, name) VALUES ('MO', 'Modern'), ('LE', 'Legacy') ON CONFLICT DO NOTHING "
                "RETURNING code")
    for event_id, code, days_ago in [(1, "MO", 1), (2, "MO", 1), (3, "LE", 1), (4, "MO", 400)]:
        fetch(repo, "INSERT INTO event (id, format_code, name, event_date, is_league) "
                    "VALUES (:i, :c, 'E', :d, false) RETURNING id", i=event_id, c=code, d=today - timedelta(days=days_ago))
    rows = fetch(repo, _updates_sql("Events per day"))
    assert [(metric, value) for _, metric, value in rows] == [("LE", 1), ("MO", 2)]
    assert fetch(repo, _updates_sql("Events in error")) == [(0,)]
    repo.mark_scan(10, "error")
    repo.mark_scan(10, "error")
    repo.mark_scan(11, "done", "Modern", today)
    rows = fetch(repo, _updates_sql("Event errors"))
    assert [(r[0], r[3]) for r in rows] == [(10, 2)]
    assert fetch(repo, _updates_sql("Events in error")) == [(1,)]

def test_updates_panels_do_not_overlap():
    cells = set()
    for p in json.loads(UPDATES.read_text())["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]
        area = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
        assert not cells & area, p["title"]
        cells |= area
