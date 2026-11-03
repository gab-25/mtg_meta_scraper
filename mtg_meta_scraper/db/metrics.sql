-- Metagame metrics. Run by Repo.init_schema() after the Alembic migrations (idempotent).

-- Performance weights: change them HERE to change them everywhere.
CREATE OR REPLACE FUNCTION placement_points(rank_min int) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT (CASE
        WHEN rank_min = 1 THEN 8
        WHEN rank_min = 2 THEN 6
        WHEN rank_min <= 4 THEN 4
        WHEN rank_min <= 8 THEN 2
        WHEN rank_min <= 16 THEN 1
        ELSE 0.5
    END)::numeric
$$;

-- One deck per row, with the grouping used by the metrics:
-- MTGTop8 category, or commander for Duel Commander / cEDH.
CREATE OR REPLACE VIEW deck_facts AS
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
            THEN d.commander ELSE a.name END) IS NOT NULL;

-- Meta share (%) per time bucket; Leagues included. Only the top_n groups by deck count.
CREATE OR REPLACE FUNCTION meta_share_series(
    fmt text, bucket interval, t_from timestamptz, t_to timestamptz, top_n int
) RETURNS TABLE ("time" timestamptz, group_name text, share numeric)
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
$$;

-- Average performance (points per placement) per bucket; Leagues excluded. Same top_n as the share.
CREATE OR REPLACE FUNCTION performance_series(
    fmt text, bucket interval, t_from timestamptz, t_to timestamptz, top_n int
) RETURNS TABLE ("time" timestamptz, group_name text, avg_points numeric)
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
$$;

-- Rising decks: current period (as_of - period, as_of] vs the previous one of equal length.
CREATE OR REPLACE FUNCTION rising(
    fmt text, period interval, min_decks int, as_of timestamptz
) RETURNS TABLE (group_name text, decks int, share numeric, share_delta numeric,
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
$$;

-- Detail: deck names that make up a group.
CREATE OR REPLACE FUNCTION group_breakdown(
    fmt text, grp text, t_from timestamptz, t_to timestamptz
) RETURNS TABLE (deck_name text, decks int)
LANGUAGE sql STABLE AS $$
    SELECT df.deck_name, count(*)::int
    FROM deck_facts df
    WHERE df.format_code = fmt AND df.group_name = grp
      AND df.event_date BETWEEN t_from::date AND t_to::date
    GROUP BY df.deck_name
    ORDER BY 2 DESC, 1
$$;
