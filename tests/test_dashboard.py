import json
import re
from pathlib import Path

DASHBOARD = Path(__file__).parent.parent / "grafana" / "dashboards" / "mtg-meta.json"

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
    dash = json.loads(DASHBOARD.read_text())
    queries = _queries(dash)
    assert len(queries) >= 5
    for sql in queries:
        final = _substitute(sql)
        assert not re.search(r"\$\w", final), final
        with repo.session.begin():
            repo.session.connection().exec_driver_sql(final).fetchall()


def test_dashboard_variables_present():
    dash = json.loads(DASHBOARD.read_text())
    assert {v["name"] for v in dash["templating"]["list"]} == {
        "format", "bucket", "top_n", "period", "min_decks", "grp",
    }


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
