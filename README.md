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
- Al primo avvio lo scraper recupera gli **ultimi 6 mesi**. Fa circa una richiesta ogni 1,5 s e ne serve una per ogni mazzo, quindi il recupero richiede **molte ore** (indicativamente una giornata). Procede dagli eventi più recenti ai più vecchi, quindi la dashboard si riempie subito con i dati recenti. Se si interrompe, riparte da dove era arrivato.
- Dopo il recupero iniziale l'aggiornamento gira ogni giorno all'ora `MTGMETA_UPDATE_HOUR_UTC` (UTC). Raccoglie i tornei nuovi e ricontrolla quelli degli ultimi `MTGMETA_RECHECK_DAYS` giorni.

Log: `docker compose logs -f scraper`.

## Comandi

```bash
docker compose exec scraper mtgmeta update     # aggiornamento manuale
docker compose exec scraper mtgmeta backfill   # riprende/completa il recupero storico
docker compose exec scraper mtgmeta validate   # confronta le nostre quote con quelle del sito
```

`validate` confronta, per ogni formato, i primi 10 archetipi del "Metagame Breakdown – Last 2 Months" del sito con le nostre percentuali sugli ultimi 60 giorni. Segnala `SCARTO` oltre ±2 punti ed esce con codice 1. Finché il recupero storico non è completo gli scarti sono attesi.

## Configurazione

| Variabile | Default | Significato |
|---|---|---|
| `MTGMETA_FORMATS` | `ST,PI,MO,LE,VI,PAU,EDH,cEDH` | Formati da raccogliere |
| `MTGMETA_BACKFILL_DAYS` | `183` | Profondità del recupero storico |
| `MTGMETA_REQUEST_INTERVAL` | `1.5` | Secondi tra due richieste (non abbassarlo troppo) |
| `MTGMETA_RECHECK_DAYS` | `3` | Giorni di tornei da ricontrollare a ogni aggiornamento |
| `MTGMETA_UPDATE_HOUR_UTC` | `6` | Ora dell'aggiornamento giornaliero |
| `GF_ADMIN_PASSWORD` | `admin` | Password admin di Grafana |

I **pesi della performance** si trovano in un solo posto, la funzione `placement_points` in `src/mtgmeta/db/metrics.sql`. Le funzioni vengono ricreate a ogni avvio dello scraper.

## Sviluppo

```bash
uv sync
docker compose up -d db
uv run pytest
```

I test dei parser usano pagine reali di MTGTop8 salvate in `tests/fixtures/`. I test del database usano il database `mtgmeta_test`, creato in automatico, e vengono saltati se Postgres non è raggiungibile.

Struttura:

- `src/mtgmeta/http.py`: client HTTP lento e con tentativi ripetuti.
- `src/mtgmeta/parsing/`: lettura delle pagine evento, formato e home.
- `src/mtgmeta/db/`: schema, metriche SQL e repository.
- `src/mtgmeta/pipeline.py`: recupero storico e aggiornamento.
- `src/mtgmeta/validate.py`: confronto con il sito.
- `grafana/`: datasource e dashboard caricati in automatico.
