# Edmonton Bike Counter Bot

A BlueSky bot that posts a daily thread of Edmonton cyclist counts, modeled on the Montreal bike counter bot (@revmontreal.bsky.social).

## What it does

Each day it fetches the previous day's bike counts from the City of Edmonton open data portal, fetches weather from Open-Meteo, formats everything into a BlueSky thread, and posts it. The first post is a header with date, weather, and total; subsequent posts list individual counter locations sorted by count descending.

## Running locally

```bash
# Test without posting (writes output.txt)
uv run python bot.py --dry-run

# Test a specific date
uv run python bot.py --dry-run --date 2026-01-15

# Post for real (requires credentials in .env)
uv run python bot.py
```

Copy `.env.example` to `.env` and fill in credentials before running.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `BSKY_HANDLE` | Yes (live only) | BlueSky handle, e.g. `yourbot.bsky.social` |
| `BSKY_APP_PASSWORD` | Yes (live only) | BlueSky app password (not account password) |
| `SOCRATA_KEY_ID` | No | Socrata API key UID — omit to use unauthenticated (may be throttled) |
| `SOCRATA_KEY_SECRET` | No | Socrata API key secret |

Socrata credentials are created at data.edmonton.ca → profile → Developer Settings → API Keys.
BlueSky app passwords are created at bsky.app → Settings → Privacy and security → App passwords.

## Data sources

- **Bike counts**: City of Edmonton SODA3 API, dataset `tq23-qn4m` (15-minute intervals, aggregated server-side by location with SoQL `GROUP BY`)
- **Weather**: Open-Meteo historical archive API — daily high/low temperature, rain (mm), snowfall (cm) for Edmonton (53.5461°N, 113.4938°W)

## Excluded locations

These counters are permanently filtered out because they have never recorded cyclists:

- Capilano Bridge
- Emily Murphy
- Funicular @ 100 St
- Hermitage South
- Queen Elizabeth Park 2
- Sir Wilfrid Laurier Park
- Whitemud Ravine Boardwalk

## Deployment

GitHub Actions runs the bot daily at 1 PM UTC (7 AM MDT) via `.github/workflows/daily.yml`. Add all four environment variables as repository secrets. The workflow can also be triggered manually via `workflow_dispatch`.

## Dependencies

Managed with `uv`. Key packages: `atproto` (BlueSky), `requests` (HTTP), `click` (CLI), `python-dotenv` (env vars).
