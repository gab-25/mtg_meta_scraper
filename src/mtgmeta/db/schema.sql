CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS format (
    code text PRIMARY KEY,
    name text NOT NULL
);

INSERT INTO format (code, name) VALUES
    ('ST', 'Standard'), ('PI', 'Pioneer'), ('MO', 'Modern'), ('LE', 'Legacy'),
    ('VI', 'Vintage'), ('PAU', 'Pauper'), ('EDH', 'Duel Commander'), ('cEDH', 'cEDH')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS event (
    id          int PRIMARY KEY,
    format_code text NOT NULL REFERENCES format (code),
    name        text NOT NULL,
    event_date  date NOT NULL,
    players     int,
    source      text,
    stars       smallint NOT NULL DEFAULT 0,
    is_major    bool NOT NULL DEFAULT false,
    is_online   bool,
    is_league   bool NOT NULL,
    scraped_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_format_date_idx ON event (format_code, event_date);

CREATE TABLE IF NOT EXISTS archetype (
    id          int PRIMARY KEY,
    format_code text NOT NULL REFERENCES format (code),
    name        text NOT NULL,
    macro       text
);

CREATE TABLE IF NOT EXISTS deck (
    id             int PRIMARY KEY,
    event_id       int NOT NULL REFERENCES event (id) ON DELETE CASCADE,
    rank_raw       text NOT NULL,
    rank_min       int NOT NULL,
    rank_max       int NOT NULL,
    deck_name      text NOT NULL,
    player         text NOT NULL,
    archetype_id   int REFERENCES archetype (id),
    commander      text,
    detail_scraped bool NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS deck_event_idx ON deck (event_id);

CREATE TABLE IF NOT EXISTS deck_card (
    deck_id   int NOT NULL REFERENCES deck (id) ON DELETE CASCADE,
    section   text NOT NULL CHECK (section IN ('main', 'side', 'commander')),
    card_name text NOT NULL,
    qty       int NOT NULL,
    PRIMARY KEY (deck_id, section, card_name)
);

CREATE TABLE IF NOT EXISTS scan_state (
    event_id     int PRIMARY KEY,
    status       text NOT NULL CHECK (status IN ('done', 'missing', 'skipped', 'error')),
    format_label text,
    event_date   date,
    attempts     int NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
