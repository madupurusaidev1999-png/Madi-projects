"""Match sponsors to Companies House records using the free monthly bulk file.

Adds company number, status, incorporation date, postcode and business type (SIC) to each
sponsor whose register name matches a Companies House company name.

Source: https://download.companieshouse.gov.uk/en_output.html (updated monthly, live companies only).
Run monthly:  python match_companies_house.py
"""
import csv
import io
import re
import sqlite3
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

BASE = "https://download.companieshouse.gov.uk/"
DB = Path(__file__).with_name("sponsors.db")
CACHE = Path(__file__).with_name("ch_cache")

SUFFIXES = [
    (r"\bpublic limited company\b", "plc"),
    (r"\blimited liability partnership\b", "llp"),
    (r"\blimited\b", "ltd"),
    (r"\bcompany\b", "co"),
    (r"\bcyfyngedig\b", "ltd"),
    (r"\bccc\b", "plc"),
]


def norm(name):
    s = re.split(r"\s(?:t/as?|t\.a\.|trading as)\s", name.lower())[0]
    s = s.replace("&", " and ").replace("+", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pat, rep in SUFFIXES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"^the ", "", s)
    return s


def score(rec, town):
    """Pick the best record when several live companies share a name."""
    number, _, status, _, category, _, post_town, _ = rec
    s = 0
    if status == "Active":
        s += 4
    if category != "Other company type" and not number[:2] in ("FC", "NF", "SF", "BR"):
        s += 2  # a normal UK company rather than an overseas branch registration
    pt = post_town.lower()
    if pt and town and (pt in town or any(w in pt for w in town.split() if len(w) > 3)):
        s += 1
    return s


def part_urls():
    html = urllib.request.urlopen(BASE + "en_output.html", timeout=60).read().decode()
    return sorted(set(re.findall(r"BasicCompanyData-\d{4}-\d{2}-\d{2}-part\d+_\d+\.zip", html)))


def iso(d):
    try:
        return datetime.strptime(d.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def main():
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS companies_house (
        sponsor_id INTEGER PRIMARY KEY REFERENCES sponsors(id),
        number TEXT, ch_name TEXT, status TEXT, incorporated TEXT,
        category TEXT, postcode TEXT, post_town TEXT, sic TEXT, data_date TEXT
    );""")
    wanted = {}
    for sid, name in conn.execute("SELECT id, name FROM sponsors"):
        wanted.setdefault(norm(name), []).append(sid)
    print(f"{len(wanted):,} distinct sponsor names to match")

    CACHE.mkdir(exist_ok=True)
    found = {}
    files = part_urls()
    data_date = re.search(r"\d{4}-\d{2}-\d{2}", files[0]).group(0)
    for f in files:
        path = CACHE / f
        if not path.exists():
            print("Downloading", f)
            with urllib.request.urlopen(BASE + f, timeout=600) as r, open(path, "wb") as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
                head = [h.strip() for h in next(reader)]
                col = {h: i for i, h in enumerate(head)}
                for row in reader:
                    if len(row) < len(head):
                        continue
                    k = norm(row[col["CompanyName"]])
                    if k in wanted:
                        g = lambda h: row[col[h]].strip() if h in col else ""
                        rec = (g("CompanyNumber"), g("CompanyName"), g("CompanyStatus"),
                               iso(g("IncorporationDate")), g("CompanyCategory"),
                               g("RegAddress.PostCode"), g("RegAddress.PostTown"), g("SICCode.SicText_1"))
                        found.setdefault(k, []).append(rec)
        print(f"  {f}: {len(found):,} matched so far")

    conn.execute("DELETE FROM companies_house")
    n = 0
    towns = dict(conn.execute("SELECT id, lower(town || ' ' || county) FROM sponsors"))
    for k, recs in found.items():
        for sid in wanted[k]:
            rec = max(recs, key=lambda r: score(r, towns.get(sid, "")))
            conn.execute("INSERT INTO companies_house VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, *rec, data_date))
            n += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM sponsors").fetchone()[0]
    print(f"Matched {n:,} of {total:,} sponsors ({n / total:.0%}) to Companies House data dated {data_date}.")


if __name__ == "__main__":
    main()
