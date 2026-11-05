"""SQL view and functions used by Grafana, versioned with Alembic through alembic_utils.

Changing a definition here requires a migration:
    uv run alembic revision --autogenerate -m "describe the change"
The migration contains the full new definition; `test_models_match_migrations` fails if you forget it.
"""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_view import PGView
from alembic_utils.replaceable_entity import register_entities

# Performance weights: change them HERE, then generate a migration (see README).
PLACEMENT_POINTS = PGFunction(
    schema="public",
    signature="placement_points(rank_min int)",
    definition="""
RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT (CASE
        WHEN rank_min = 1 THEN 8
        WHEN rank_min = 2 THEN 6
        WHEN rank_min <= 4 THEN 4
        WHEN rank_min <= 8 THEN 2
        WHEN rank_min <= 16 THEN 1
        ELSE 0.5
    END)::numeric
$$
""",
)

# One deck per row, with the grouping used by the metrics:
# MTGTop8 category, or commander for Duel Commander / cEDH.
DECK_FACTS = PGView(
    schema="public",
    signature="deck_facts",
    definition="""
SELECT d.id AS deck_id,
       d.event_id,
       e.format_code,
       e.event_date,
       e.is_league,
       d.rank_min,
       CASE WHEN e.format_code IN ('EDH', 'cEDH') AND d.commander IS NOT NULL
            THEN d.commander ELSE a.name END AS group_name,
       a.name AS archetype_name,
       d.deck_name,
       placement_points(d.rank_min) AS points
FROM deck d
JOIN event e ON e.id = d.event_id
LEFT JOIN archetype a ON a.id = d.archetype_id
WHERE (CASE WHEN e.format_code IN ('EDH', 'cEDH') AND d.commander IS NOT NULL
            THEN d.commander ELSE a.name END) IS NOT NULL
""",
)

# Meta share (%) per time bucket; Leagues included. Only the top_n groups by deck count.
META_SHARE_SERIES = PGFunction(
    schema="public",
    signature="meta_share_series(fmt text, bucket interval, t_from timestamptz, t_to timestamptz, top_n int)",
    definition="""
RETURNS TABLE ("time" timestamptz, group_name text, share numeric)
LANGUAGE sql STABLE AS $$
    WITH f AS (
        SELECT time_bucket(bucket, df.event_date)::timestamptz AS b, df.group_name AS g
        FROM deck_facts df
        WHERE df.format_code = fmt AND df.event_date BETWEEN t_from::date AND t_to::date
    ),
    top AS (SELECT g FROM f GROUP BY g ORDER BY count(*) DESC, g LIMIT top_n),
    tot AS (SELECT b, count(*) AS n FROM f GROUP BY b)
    SELECT f.b, f.g, 100.0 * count(*) / NULLIF(tot.n, 0)
    FROM f JOIN tot USING (b)
    WHERE f.g IN (SELECT g FROM top)
    GROUP BY f.b, f.g, tot.n
    ORDER BY 1, 2
$$
""",
)

# Average performance (points per placement) per bucket; Leagues excluded. Same top_n as the share.
PERFORMANCE_SERIES = PGFunction(
    schema="public",
    signature="performance_series(fmt text, bucket interval, t_from timestamptz, t_to timestamptz, top_n int)",
    definition="""
RETURNS TABLE ("time" timestamptz, group_name text, avg_points numeric)
LANGUAGE sql STABLE AS $$
    WITH f AS (
        SELECT time_bucket(bucket, df.event_date)::timestamptz AS b, df.group_name AS g,
               df.is_league, df.points
        FROM deck_facts df
        WHERE df.format_code = fmt AND df.event_date BETWEEN t_from::date AND t_to::date
    ),
    top AS (SELECT g FROM f GROUP BY g ORDER BY count(*) DESC, g LIMIT top_n)
    SELECT b, g, avg(points)
    FROM f
    WHERE NOT is_league AND g IN (SELECT g FROM top)
    GROUP BY b, g
    ORDER BY 1, 2
$$
""",
)

# Rising decks: current period (as_of - period, as_of] vs the previous one of equal length.
RISING = PGFunction(
    schema="public",
    signature="rising(fmt text, period interval, min_decks int, as_of timestamptz)",
    definition="""
RETURNS TABLE (group_name text, decks int, share numeric, share_delta numeric,
                 avg_points numeric, points_delta numeric, win_pct numeric)
LANGUAGE sql STABLE AS $$
    WITH f AS (
        SELECT df.group_name AS g, df.is_league, df.rank_min, df.points,
               CASE WHEN df.event_date > (as_of - period)::date THEN 'cur' ELSE 'prev' END AS p
        FROM deck_facts df
        WHERE df.format_code = fmt
          AND df.event_date > (as_of - 2 * period)::date
          AND df.event_date <= as_of::date
    ),
    tot AS (SELECT p, count(*) AS n FROM f GROUP BY p),
    agg AS (
        SELECT f.p, f.g,
               count(*) AS decks,
               100.0 * count(*) / NULLIF(max(tot.n), 0) AS share,
               avg(f.points) FILTER (WHERE NOT f.is_league) AS avg_points,
               100.0 * count(*) FILTER (WHERE NOT f.is_league AND f.rank_min = 1)
                   / NULLIF(count(*) FILTER (WHERE NOT f.is_league), 0) AS win_pct
        FROM f JOIN tot USING (p)
        GROUP BY f.p, f.g
    )
    SELECT c.g, c.decks::int, c.share,
           c.share - COALESCE(pv.share, 0),
           c.avg_points,
           c.avg_points - pv.avg_points,  -- NULL if the previous period is missing
           c.win_pct
    FROM agg c
    LEFT JOIN agg pv ON pv.g = c.g AND pv.p = 'prev'
    WHERE c.p = 'cur' AND c.decks >= min_decks
    ORDER BY 4 DESC, 1
$$
""",
)

# Detail: deck names that make up a group.
GROUP_BREAKDOWN = PGFunction(
    schema="public",
    signature="group_breakdown(fmt text, grp text, t_from timestamptz, t_to timestamptz)",
    definition="""
RETURNS TABLE (deck_name text, decks int)
LANGUAGE sql STABLE AS $$
    SELECT df.deck_name, count(*)::int
    FROM deck_facts df
    WHERE df.format_code = fmt AND df.group_name = grp
      AND df.event_date BETWEEN t_from::date AND t_to::date
    GROUP BY df.deck_name
    ORDER BY 2 DESC, 1
$$
""",
)

# Detail: share (%) of one group per time bucket, 0 in the buckets where the format has decks but the group none.
GROUP_SHARE_SERIES = PGFunction(
    schema="public",
    signature="group_share_series(fmt text, grp text, bucket interval, t_from timestamptz, t_to timestamptz)",
    definition="""
RETURNS TABLE ("time" timestamptz, share numeric)
LANGUAGE sql STABLE AS $$
    SELECT time_bucket(bucket, df.event_date)::timestamptz,
           100.0 * count(*) FILTER (WHERE df.group_name = grp) / count(*)
    FROM deck_facts df
    WHERE df.format_code = fmt AND df.event_date BETWEEN t_from::date AND t_to::date
    GROUP BY 1
    ORDER BY 1
$$
""",
)

# Detail: cards of a group's decks, per section. Only decks whose list was scraped count.
GROUP_TOP_CARDS = PGFunction(
    schema="public",
    signature="group_top_cards(fmt text, grp text, t_from timestamptz, t_to timestamptz)",
    definition="""
RETURNS TABLE (section text, card_name text, decks int, decks_pct numeric, avg_qty numeric)
LANGUAGE sql STABLE AS $$
    WITH g AS (
        SELECT df.deck_id
        FROM deck_facts df
        WHERE df.format_code = fmt AND df.group_name = grp
          AND df.event_date BETWEEN t_from::date AND t_to::date
          AND EXISTS (SELECT 1 FROM deck_card dc WHERE dc.deck_id = df.deck_id)
    ),
    tot AS (SELECT count(*) AS n FROM g)
    SELECT dc.section, dc.card_name, count(*)::int,
           100.0 * count(*) / max(tot.n), avg(dc.qty)
    FROM g
    JOIN deck_card dc ON dc.deck_id = g.deck_id
    CROSS JOIN tot
    GROUP BY dc.section, dc.card_name
    ORDER BY CASE dc.section WHEN 'commander' THEN 0 WHEN 'main' THEN 1 ELSE 2 END, 3 DESC, 2
$$
""",
)

# Detail: one group over the whole range. Share includes Leagues; performance and wins exclude them.
GROUP_SUMMARY = PGFunction(
    schema="public",
    signature="group_summary(fmt text, grp text, t_from timestamptz, t_to timestamptz)",
    definition="""
RETURNS TABLE (decks int, share numeric, avg_points numeric, win_pct numeric)
LANGUAGE sql STABLE AS $$
    SELECT count(*) FILTER (WHERE df.group_name = grp)::int,
           100.0 * count(*) FILTER (WHERE df.group_name = grp) / NULLIF(count(*), 0),
           avg(df.points) FILTER (WHERE df.group_name = grp AND NOT df.is_league),
           100.0 * count(*) FILTER (WHERE df.group_name = grp AND NOT df.is_league AND df.rank_min = 1)
               / NULLIF(count(*) FILTER (WHERE df.group_name = grp AND NOT df.is_league), 0)
    FROM deck_facts df
    WHERE df.format_code = fmt AND df.event_date BETWEEN t_from::date AND t_to::date
$$
""",
)

# Detail: average performance of one group per time bucket; Leagues excluded.
GROUP_PERFORMANCE_SERIES = PGFunction(
    schema="public",
    signature="group_performance_series(fmt text, grp text, bucket interval, t_from timestamptz, t_to timestamptz)",
    definition="""
RETURNS TABLE ("time" timestamptz, avg_points numeric)
LANGUAGE sql STABLE AS $$
    SELECT time_bucket(bucket, df.event_date)::timestamptz, avg(df.points)
    FROM deck_facts df
    WHERE df.format_code = fmt AND df.group_name = grp AND NOT df.is_league
      AND df.event_date BETWEEN t_from::date AND t_to::date
    GROUP BY 1
    ORDER BY 1
$$
""",
)

# Order matters: each object only depends on the ones before it.
ENTITIES = [PLACEMENT_POINTS, DECK_FACTS, META_SHARE_SERIES, PERFORMANCE_SERIES, RISING, GROUP_BREAKDOWN,
            GROUP_SHARE_SERIES, GROUP_TOP_CARDS, GROUP_SUMMARY, GROUP_PERFORMANCE_SERIES]

# Only views and functions: the TimescaleDB extension is created by migration 0001.
register_entities(ENTITIES, entity_types=[PGView, PGFunction])
