"""Build a static version of the site (no Python server needed) into ./public.

    python build_static.py

Creates public/index.html and public/data.txt, which can be uploaded to any static host
(Cloudflare Pages, Netlify, GitHub Pages). The static version shows currently licensed sponsors and
Companies House details; the register history ("Removed", "first seen") needs the Flask app.
"""
import base64
import gzip
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "sponsors.db"
OUT = HERE / "public"
SITE = "https://www.ukvisasponsorfinder.com"


def export(conn):
    date = conn.execute("SELECT MAX(date) FROM snapshots").fetchone()[0]
    ch_date = conn.execute("SELECT MAX(data_date) FROM companies_house").fetchone()[0]
    routes = [r[0] for r in conn.execute("SELECT route FROM licences GROUP BY route ORDER BY COUNT(*) DESC")]
    ratings = [r[0] for r in conn.execute("SELECT type_rating FROM licences GROUP BY type_rating ORDER BY COUNT(*) DESC")]
    ri = {r: i for i, r in enumerate(routes)}
    ti = {r: i for i, r in enumerate(ratings)}
    lic = {}
    for sid, route, tr in conn.execute("SELECT sponsor_id, route, type_rating FROM licences WHERE active=1"):
        lic.setdefault(sid, []).extend([ri[route], ti[tr]])
    statuses, sics, rows = [], [], []

    def ix(lst, v):
        if v not in lst:
            lst.append(v)
        return lst.index(v)

    for sid, name, town, county, num, st, inc, sic in conn.execute(
            """SELECT s.id, s.name, s.town, s.county, h.number, h.status, h.incorporated, h.sic
               FROM sponsors s LEFT JOIN companies_house h ON h.sponsor_id = s.id
               WHERE s.removed_on IS NULL ORDER BY ltrim(s.name, '"''(&.-*# ') COLLATE NOCASE"""):
        row = [name, town or "", county or "", lic.get(sid, [])]
        if num:
            row += [num, ix(statuses, st), inc or "", ix(sics, sic if sic and sic != "None Supplied" else "")]
        rows.append(row)
    data = {"date": date, "ch_date": ch_date, "routes": routes, "ratings": ratings,
            "statuses": statuses, "sics": sics, "rows": rows}
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.b64encode(gzip.compress(raw, 9)), len(rows)


def main():
    OUT.mkdir(exist_ok=True)
    blob, n = export(sqlite3.connect(DB))
    (OUT / "data.txt").write_bytes(blob)
    html = (HERE / "templates" / "index.html").read_text()
    html = html.replace("<!--SCRIPT-->", '<script>window.SF_STATIC = "data.txt";</script>')
    (OUT / "index.html").write_text(html)
    (OUT / "privacy.html").write_text((HERE / "templates" / "privacy.html").read_text())
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE}/</loc></url>\n  <url><loc>{SITE}/privacy.html</loc></url>\n</urlset>\n")
    print(f"Built public/ with {n:,} sponsors ({len(blob) / 1e6:.1f} MB data file).")


if __name__ == "__main__":
    main()
