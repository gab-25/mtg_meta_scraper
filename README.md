# MTG Meta Scraper

Raccoglie da [MTGTop8](https://mtgtop8.com/) i risultati dei tornei dei formati competitive di Magic, li salva su TimescaleDB e li mostra in una dashboard Grafana per capire **quali mazzi stanno scalando il meta**.

Formati: Standard, Pioneer, Modern, Legacy, Vintage, Pauper, Duel Commander, cEDH.

## Cosa mostra la dashboard

- **Quota di meta (%)**: percentuale dei mazzi di un formato che appartengono a ogni archetipo, nel tempo (MTGO League incluse).
- **Performance**: punti medi per piazzamento (1° = 8, 2° = 6, 3-4 = 4, 5-8 = 2, 9-16 = 1, oltre = 0.5), nel tempo. MTGO League escluse. È un sostituto del winrate, che MTGTop8 non pubblica.
- **Mazzi in salita**: ultimo periodo contro quello precedente, con variazione di quota, di performance e % di vittorie di torneo.
- **Dettaglio**: i nomi di mazzo che compongono un archetipo.

Gli archetipi sono le categorie di MTGTop8 (es. "Rakdos Aggro"). In Duel Commander e cEDH si raggruppa per **comandante**: le coppie partner contano come una voce sola.

Variabili in alto: formato, bucket temporale, top N archetipi, periodo "in salita", mazzi minimi. Per i formati Commander conviene alzare "Mazzi minimi" e abbassare "Top N", perché i comandanti sono molti e con pochi mazzi ciascuno.

## Avvio

```bash
cp .env.example .env        # opzionale: cambia password Grafana, formati, ritmo
docker compose up -d --build
```

- Grafana: <http://localhost:3000> (visibile senza login; per modificare usa admin / `GF_ADMIN_PASSWORD`).
- Al primo avvio lo scraper recupera gli **ultimi 30 giorni**. Fa circa una richiesta ogni 1,5 s e ne serve una per ogni mazzo, quindi il recupero richiede **qualche ora** (indicativamente 3-4). Procede dagli eventi più recenti ai più vecchi, quindi la dashboard si riempie subito con i dati recenti. Se si interrompe, riparte da dove era arrivato. Per uno storico più lungo alza `MTG_META_SCRAPER_BACKFILL_DAYS` (es. `183` per 6 mesi: circa una giornata). L'aggiornamento giornaliero non cancella i dati vecchi, quindi lo storico si allunga da solo.
- Dopo il recupero iniziale l'aggiornamento gira ogni giorno all'ora `MTG_META_SCRAPER_UPDATE_HOUR_UTC` (UTC). Raccoglie i tornei nuovi e ricontrolla quelli degli ultimi `MTG_META_SCRAPER_RECHECK_DAYS` giorni.

Log: `docker compose logs -f scraper`.

## Comandi

```bash
docker compose exec scraper python -m mtg_meta_scraper validate   # confronta le nostre quote con quelle del sito
```

Il container `scraper` esegue già da solo recupero storico e aggiornamento giornaliero: **non** lanciare `python -m mtg_meta_scraper backfill` o `python -m mtg_meta_scraper update` mentre è in esecuzione, altrimenti le richieste al sito raddoppiano. Per lanciarli a mano: `docker compose stop scraper`, poi `docker compose run --rm scraper python -m mtg_meta_scraper update` (o `backfill`), poi `docker compose start scraper`.

Se lo scraper trova 100 pagine di fila mancanti o in errore (sito giù, blocco, HTML cambiato) si ferma da solo per non martellare il sito e riprova al giro successivo.

Postgres (`5432`) e Grafana (`3000`) sono esposti solo su `127.0.0.1`.

`validate` confronta, per ogni formato, i primi 10 archetipi del "Metagame Breakdown – Last 2 Months" del sito con le nostre percentuali sugli ultimi 60 giorni. Segnala `SCARTO` oltre ±2 punti ed esce con codice 1. Finché il recupero storico non è completo, o se copre meno di 60 giorni (come con il default di 30), gli scarti sono attesi.

## Configurazione

| Variabile | Default | Significato |
|---|---|---|
| `MTG_META_SCRAPER_FORMATS` | `ST,PI,MO,LE,VI,PAU,EDH,cEDH` | Formati da raccogliere |
| `MTG_META_SCRAPER_BACKFILL_DAYS` | `30` | Profondità del recupero storico |
| `MTG_META_SCRAPER_REQUEST_INTERVAL` | `1.5` | Secondi tra due richieste (non abbassarlo troppo) |
| `MTG_META_SCRAPER_RECHECK_DAYS` | `3` | Giorni di tornei da ricontrollare a ogni aggiornamento |
| `MTG_META_SCRAPER_UPDATE_HOUR_UTC` | `6` | Ora dell'aggiornamento giornaliero |
| `GF_ADMIN_PASSWORD` | `admin` | Password admin di Grafana |

I **pesi della performance** si trovano in un solo posto, la funzione `placement_points` in `mtg_meta_scraper/db/metrics.sql`. Le funzioni vengono ricreate a ogni avvio dello scraper.

## Sviluppo

```bash
uv sync
docker compose up -d db
uv run pytest
```

I test dei parser usano pagine reali di MTGTop8 salvate in `tests/fixtures/`. I test del database usano il database `mtg_meta_scraper_test`, ricreato da zero a ogni esecuzione applicando le migrazioni, e vengono saltati se Postgres non è raggiungibile.

### Database e migrazioni

Le tabelle sono modelli SQLAlchemy in `mtg_meta_scraper/db/tables.py`; lo schema è gestito con Alembic. Lo scraper applica da solo le migrazioni mancanti a ogni avvio (anche `python -m mtg_meta_scraper init-db`).

Per cambiare lo schema:

```bash
# 1. modifica mtg_meta_scraper/db/tables.py
# 2. genera la migrazione (confronta i modelli con il DB indicato da DATABASE_URL, default localhost)
uv run alembic revision --autogenerate -m "aggiungi colonna X"
# 3. rileggi il file creato in mtg_meta_scraper/db/migrations/versions/ e applicala
uv run alembic upgrade head
```

Il test `test_models_match_migrations` fallisce se i modelli e le migrazioni non coincidono.

Le funzioni SQL usate da Grafana (`metrics.sql`) restano fuori dalle migrazioni: vengono ricreate a ogni avvio. La vista `deck_facts` dipende però dalle colonne di `deck`, `event` e `archetype`: una migrazione che le rinomina o cambia tipo deve prima eseguire `DROP VIEW IF EXISTS deck_facts CASCADE` (verrà ricreata al riavvio).

Un database creato prima dell'introduzione di Alembic (con il vecchio `schema.sql`) ha nomi di indice diversi: se è vuoto conviene ricrearlo (`docker compose down -v`, **cancella i dati**), altrimenti va allineato a mano e poi marcato con `uv run alembic stamp head`.

Struttura:

- `mtg_meta_scraper/http.py`: client HTTP lento e con tentativi ripetuti.
- `mtg_meta_scraper/parsing/`: lettura delle pagine evento, formato e home.
- `mtg_meta_scraper/db/`: modelli ORM (`tables.py`), migrazioni Alembic (`migrations/`), metriche SQL e repository.
- `mtg_meta_scraper/pipeline.py`: recupero storico e aggiornamento.
- `mtg_meta_scraper/validate.py`: confronto con il sito.
- `grafana/`: datasource e dashboard caricati in automatico.
