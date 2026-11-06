# Sponsor Finder

Search every UK company licensed to sponsor Skilled Worker (formerly Tier 2) and other work visas.
The data comes from the Home Office **Register of licensed sponsors: workers** on gov.uk.

## What it shows
| Field | Where it comes from |
|---|---|
| Company name, town, county | Government register |
| Visa route and rating (A / B / Premium / SME+) | Government register |
| Licensed / Removed | **Our own tracking**: on today's register = licensed; dropped off = removed (with date) |
| First seen | The first register date *this site* saw the company (not the official licence date) |
| Company number, status, incorporation date, business type | Companies House free monthly data, matched by company name (about 81% of sponsors) |
| History | Additions, removals, rating changes, routes added or removed, captured by daily runs |

The government does **not** publish licence grant dates or when a company last sponsored a worker,
so the site can't show them.

## Run it on your computer
Needs Python 3.10 or newer.
```bash
pip install -r requirements.txt
python update_register.py      # downloads today's register into sponsors.db (~5 seconds)
python match_companies_house.py # adds Companies House details (~2 minutes, ~500MB download, run monthly)
python app.py                  # open http://localhost:8000
```

## Keep it up to date
Run `update_register.py` once a day. The longer it runs, the more useful the Removed and History data gets.
Example cron (07:30 and 13:30 every weekday):
```
30 7,13 * * 1-5 cd /path/to/sponsor-finder && python3 update_register.py
```

## Static version (cheapest hosting)
```bash
python build_static.py         # writes public/index.html and public/data.txt
```
Upload the `public/` folder to any static host (Cloudflare Pages, Netlify, GitHub Pages). It shows current
sponsors and Companies House details. The "Removed" history needs the Flask app.

## Put it online
Any host that runs Python and keeps a file on disk works, for example a small VPS, Render or Railway with a persistent disk, or PythonAnywhere.
```bash
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```
Keep `sponsors.db` on persistent storage. If you lose it, you also lose the removal history.

## Files
- `update_register.py`: downloads the register CSV and updates the SQLite database
- `match_companies_house.py`: matches sponsors to the Companies House monthly bulk file (no API key needed)
- `build_static.py`: builds a static copy of the site into `public/`
- `app.py`: Flask web server and JSON API (`/api/search`, `/api/sponsor/<id>`, `/api/stats`)
- `templates/index.html`: the search page
