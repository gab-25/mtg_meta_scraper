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
        repo.conn.execute(final).fetchall()
        repo.conn.rollback()


def test_dashboard_variables_present():
    dash = json.loads(DASHBOARD.read_text())
    assert {v["name"] for v in dash["templating"]["list"]} == {
        "format", "bucket", "top_n", "period", "min_decks", "grp",
    }
