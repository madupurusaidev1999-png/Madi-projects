import re
import sqlite3
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request

DB = Path(__file__).with_name("sponsors.db")
PAGE_SIZE = 25
app = Flask(__name__)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close(_):
    if "db" in g:
        g.db.close()


def fts_query(q):
    words = re.findall(r"\w+", q.lower())
    return " ".join(f'"{w}"*' for w in words)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/stats")
def stats():
    c = db()
    snap = c.execute("SELECT date, rows FROM snapshots ORDER BY date DESC LIMIT 1").fetchone()
    return jsonify(
        updated=snap["date"] if snap else None,
        first_snapshot=c.execute("SELECT MIN(date) FROM snapshots").fetchone()[0],
        active=c.execute("SELECT COUNT(*) FROM sponsors WHERE removed_on IS NULL").fetchone()[0],
        removed=c.execute("SELECT COUNT(*) FROM sponsors WHERE removed_on IS NOT NULL").fetchone()[0],
        routes=[r[0] for r in c.execute(
            "SELECT route FROM licences GROUP BY route ORDER BY COUNT(*) DESC")],
        ch_date=c.execute("SELECT MAX(data_date) FROM companies_house").fetchone()[0],
        matched=c.execute("SELECT COUNT(*) FROM companies_house h JOIN sponsors s ON s.id = h.sponsor_id "
                          "WHERE s.removed_on IS NULL").fetchone()[0],
    )


@app.get("/api/search")
def search():
    q = request.args.get("q", "").strip()
    town = request.args.get("town", "").strip()
    route = request.args.get("route", "")
    status = request.args.get("status", "")
    rating = request.args.get("rating", "")
    page = max(1, request.args.get("page", 1, type=int))

    where, params, join = [], [], ""
    if q and fts_query(q):
        join = "JOIN sponsors_fts f ON f.rowid = s.id"
        where.append("sponsors_fts MATCH ?")
        params.append("name : " + fts_query(q))
    if town:
        where.append("(s.town LIKE ? OR s.county LIKE ?)")
        params += [f"%{town}%", f"%{town}%"]
    if status == "active":
        where.append("s.removed_on IS NULL")
    elif status == "removed":
        where.append("s.removed_on IS NOT NULL")
    if route:
        where.append("EXISTS (SELECT 1 FROM licences l WHERE l.sponsor_id=s.id AND l.route=? AND l.active=1)")
        params.append(route)
    if rating in ("A", "B"):
        where.append("EXISTS (SELECT 1 FROM licences l WHERE l.sponsor_id=s.id AND l.active=1 "
                     "AND l.type_rating LIKE ?)")
        params.append(f"%({rating} rating)%" if rating == "B" else "%(A%")

    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    order = "ORDER BY f.rank" if join else """ORDER BY ltrim(s.name, '"''(&.-*# ') COLLATE NOCASE"""
    c = db()
    total = c.execute(f"SELECT COUNT(*) FROM sponsors s {join} {sql_where}", params).fetchone()[0]
    rows = c.execute(
        f"""SELECT s.id, s.name, s.town, s.county, s.first_seen, s.last_seen, s.removed_on,
                   h.number AS ch_number, h.status AS ch_status, h.incorporated,
                   (SELECT group_concat(l.route || '|' || l.type_rating, ';;') FROM licences l
                     WHERE l.sponsor_id = s.id AND l.active = 1) AS lic
            FROM sponsors s {join} LEFT JOIN companies_house h ON h.sponsor_id = s.id {sql_where} {order} LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()

    results = []
    for r in rows:
        lic = [dict(zip(("route", "rating"), x.split("|", 1))) for x in (r["lic"] or "").split(";;") if x]
        results.append(dict(id=r["id"], name=r["name"], town=r["town"], county=r["county"],
                            first_seen=r["first_seen"], last_seen=r["last_seen"],
                            removed_on=r["removed_on"], licences=lic, ch_number=r["ch_number"],
                            ch_status=r["ch_status"], incorporated=r["incorporated"]))
    return jsonify(total=total, page=page, page_size=PAGE_SIZE, results=results)


@app.get("/api/sponsor/<int:sid>")
def sponsor(sid):
    c = db()
    s = c.execute("SELECT * FROM sponsors WHERE id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    lic = [dict(r) for r in c.execute(
        "SELECT route, type_rating, first_seen, last_seen, active FROM licences WHERE sponsor_id=? "
        "ORDER BY active DESC, route", (sid,))]
    events = [dict(r) for r in c.execute(
        "SELECT date, kind, detail FROM events WHERE sponsor_id=? ORDER BY date DESC, rowid DESC", (sid,))]
    first = c.execute("SELECT MIN(date) FROM snapshots").fetchone()[0]
    ch = c.execute("SELECT * FROM companies_house WHERE sponsor_id=?", (sid,)).fetchone()
    return jsonify(sponsor={k: s[k] for k in s.keys() if k != "key"}, licences=lic, events=events,
                   tracking_since=first, companies_house=dict(ch) if ch else None)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
