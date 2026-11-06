"""Download the latest UK Register of Licensed Sponsors (Workers) and load it into SQLite.

Run once a day (e.g. cron). Each run:
  * adds new sponsors (first_seen = register date)
  * refreshes last_seen for sponsors still on the register
  * marks sponsors missing from the register as removed (removed_on = register date)
  * logs every change (added / removed / rating changed / route added / route removed)
"""
import csv
import io
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

API = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
DB = Path(__file__).with_name("sponsors.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sponsors (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,           -- normalised name + town
    name TEXT NOT NULL,
    town TEXT,
    county TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_on TEXT                     -- NULL while licensed
);
CREATE TABLE IF NOT EXISTS licences (
    sponsor_id INTEGER NOT NULL REFERENCES sponsors(id),
    route TEXT NOT NULL,
    type_rating TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (sponsor_id, route)
);
CREATE TABLE IF NOT EXISTS events (
    sponsor_id INTEGER NOT NULL REFERENCES sponsors(id),
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS companies_house (
    sponsor_id INTEGER PRIMARY KEY REFERENCES sponsors(id),
    number TEXT, ch_name TEXT, status TEXT, incorporated TEXT,
    category TEXT, postcode TEXT, post_town TEXT, sic TEXT, data_date TEXT
);
CREATE INDEX IF NOT EXISTS events_sponsor ON events(sponsor_id);
CREATE TABLE IF NOT EXISTS snapshots (
    date TEXT PRIMARY KEY,
    url TEXT,
    rows INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS sponsors_fts USING fts5(
    name, town, county, content='sponsors', content_rowid='id', tokenize='unicode61'
);
"""


def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def norm_key(name, town):
    return (re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() + "|" +
            re.sub(r"[^a-z0-9]+", " ", town.lower()).strip())


def latest_csv():
    with urllib.request.urlopen(API, timeout=60) as r:
        meta = json.load(r)
    for a in meta["details"]["attachments"]:
        if a.get("url", "").lower().endswith(".csv"):
            url = a["url"]
            m = re.search(r"(\d{4}-\d{2}-\d{2})", url)
            date = m.group(1) if m else meta["public_updated_at"][:10]
            return url, date
    sys.exit("No CSV attachment found on gov.uk")


def load(text, date, url, conn):
    if conn.execute("SELECT 1 FROM snapshots WHERE date=?", (date,)).fetchone():
        print(f"Register {date} already loaded.")
        return

    # Group rows into sponsor -> {route: type_rating}
    rows = {}
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        name = clean(r.get("Organisation Name"))
        if not name:
            continue
        town, county = clean(r.get("Town/City")), clean(r.get("County"))
        k = norm_key(name, town)
        entry = rows.setdefault(k, {"name": name, "town": town, "county": county, "routes": {}})
        entry["routes"][clean(r.get("Route"))] = clean(r.get("Type & Rating"))

    c = conn.cursor()
    existing = {k: (i, rem) for i, k, rem in c.execute("SELECT id, key, removed_on FROM sponsors")}
    first_load = not existing
    seen_ids = set()

    for k, e in rows.items():
        if k in existing:
            sid, removed = existing[k]
            c.execute("UPDATE sponsors SET name=?, town=?, county=?, last_seen=?, removed_on=NULL WHERE id=?",
                      (e["name"], e["town"], e["county"], date, sid))
            if removed:
                c.execute("INSERT INTO events VALUES (?,?,?,?)", (sid, date, "re-added", "Back on the register"))
        else:
            c.execute("INSERT INTO sponsors (key,name,town,county,first_seen,last_seen) VALUES (?,?,?,?,?,?)",
                      (k, e["name"], e["town"], e["county"], date, date))
            sid = c.lastrowid
            c.execute("INSERT INTO events VALUES (?,?,?,?)",
                      (sid, date, "added", "First seen on register" if first_load else "Added to register"))
        seen_ids.add(sid)

        old = {route: (tr, act) for route, tr, act in
               c.execute("SELECT route, type_rating, active FROM licences WHERE sponsor_id=?", (sid,))}
        for route, tr in e["routes"].items():
            if route not in old:
                c.execute("INSERT INTO licences VALUES (?,?,?,?,?,1)", (sid, route, tr, date, date))
                if not first_load and k in existing:
                    c.execute("INSERT INTO events VALUES (?,?,?,?)", (sid, date, "route added", f"{route} — {tr}"))
            else:
                old_tr, act = old[route]
                if old_tr != tr:
                    c.execute("INSERT INTO events VALUES (?,?,?,?)",
                              (sid, date, "rating changed", f"{route}: {old_tr} → {tr}"))
                c.execute("UPDATE licences SET type_rating=?, last_seen=?, active=1 WHERE sponsor_id=? AND route=?",
                          (tr, date, sid, route))
        for route, (tr, act) in old.items():
            if route not in e["routes"] and act:
                c.execute("UPDATE licences SET active=0 WHERE sponsor_id=? AND route=?", (sid, route))
                c.execute("INSERT INTO events VALUES (?,?,?,?)", (sid, date, "route removed", f"{route} — {tr}"))

    # Sponsors no longer on the register
    for k, (sid, removed) in existing.items():
        if sid not in seen_ids and not removed:
            c.execute("UPDATE sponsors SET removed_on=? WHERE id=?", (date, sid))
            c.execute("UPDATE licences SET active=0 WHERE sponsor_id=?", (sid,))
            c.execute("INSERT INTO events VALUES (?,?,?,?)", (sid, date, "removed", "No longer on the register"))

    c.execute("INSERT INTO snapshots VALUES (?,?,?)", (date, url, len(rows)))
    c.execute("INSERT INTO sponsors_fts(sponsors_fts) VALUES('rebuild')")
    conn.commit()
    print(f"Loaded register {date}: {len(rows)} sponsors.")


def main():
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    if len(sys.argv) > 2:          # python update_register.py <file.csv> <YYYY-MM-DD>  (load a saved copy)
        text = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
        load(text, sys.argv[2], sys.argv[1], conn)
        return
    url, date = latest_csv()
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as r:
        text = r.read().decode("utf-8-sig")
    load(text, date, url, conn)


if __name__ == "__main__":
    main()
