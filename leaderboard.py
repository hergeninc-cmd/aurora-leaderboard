"""
Aurora Creative League — standings
Runs hourly after main.py. Reads every scored creative card, computes
writer and editor standings, and pushes data.json to the public
GitHub Pages repo that serves the leaderboard.

Only names and percentages leave the building — no dollar figures.

Secrets: NOTION_TOKEN, NOTION_DB_ID, LEADERBOARD_TOKEN
Optional: LEADERBOARD_REPO (default hergeninc-cmd/aurora-leaderboard)
"""

import os
import re
import sys
import json
import base64
import datetime
import requests
from collections import defaultdict

import dna  # resolver, query_all, page_title, prop helpers

TOKEN = os.environ.get("LEADERBOARD_TOKEN", "")
REPO = os.environ.get("LEADERBOARD_REPO", "hergeninc-cmd/aurora-leaderboard")
MIN_SPEND = 75
WRITERS = {"LC": "Lan", "AP": "Adam", "AURORA": "Dawid", "ABRIL": "April"}
CODE_RE = re.compile(r"\b(LC|AP|AURORA|ABRIL)", re.I)
H = dna.NOTION_HEADERS


def writer_of(title):
    m = CODE_RE.search(title or "")
    return WRITERS.get(m.group(1).upper()) if m else None


def find_editor_prop(db):
    for name, p in db["properties"].items():
        if "edit" in name.lower() and p["type"] in ("people", "select", "multi_select", "rich_text"):
            return name, p["type"]
    return None, None


def editor_of(card, prop, ptype):
    if not prop:
        return None
    p = card["properties"].get(prop, {})
    if ptype == "people":
        return (p.get("people") or [{}])[0].get("name")
    if ptype == "select":
        return (p.get("select") or {}).get("name")
    if ptype == "multi_select":
        return (p.get("multi_select") or [{}])[0].get("name")
    if ptype == "rich_text":
        return "".join(x.get("plain_text", "") for x in p["rich_text"]).strip() or None
    return None


def standings(cards, key_fn, previous):
    g = defaultdict(list)
    for c in cards:
        k = key_fn(c)
        if k:
            g[k].append(c)
    win_spend_total = 0.0
    rows = []
    for name, cs in g.items():
        spend = sum((dna.prop_number(c, "Spend 3d") or 0) for c in cs)
        rev = sum((dna.prop_number(c, "Spend 3d") or 0) * (dna.prop_number(c, "ROAS 3d") or 0) for c in cs)
        winning = [c for c in cs if dna.prop_select(c, "Auto Status") in ("WINNER", "SLEEPER")]
        wspend = sum((dna.prop_number(c, "Spend 3d") or 0) for c in winning)
        win_spend_total += wspend
        hooks = [dna.prop_number(c, "Hook Rate") for c in cs if dna.prop_number(c, "Hook Rate")]
        rows.append({
            "name": name,
            "photo": f"{name.lower().split()[0]}.jpg",
            "_wspend": wspend,
            "winners": sum(1 for c in cs if dna.prop_select(c, "Auto Status") == "WINNER"),
            "live": len(cs),
            "roas": round(rev / spend, 2) if spend else 0.0,
            "hook": round(sum(hooks) / len(hooks), 3) if hooks else 0.0,
        })
    prev = {p["name"]: p.get("share", 0) for p in previous}
    for r in rows:
        r["share"] = round(r["_wspend"] / win_spend_total, 3) if win_spend_total else 0.0
        d = round((r["share"] - prev.get(r["name"], r["share"])) * 100)
        r["delta"] = f"{d:+d}" if d else "0"
        del r["_wspend"]
    rows.sort(key=lambda r: -r["share"])
    return rows


def hall_of_fame(cards, title_prop):
    best = None
    for c in cards:
        if (dna.prop_number(c, "Spend 3d") or 0) >= 150:
            r = dna.prop_number(c, "ROAS 3d") or 0
            if not best or r > best[0]:
                best = (r, c)
    if not best:
        return None
    name = dna.page_title(best[1], title_prop)
    return {"ad": name.split(" ")[0], "writer": writer_of(name) or "", "roas": best[0],
            "spend_note": "best ROAS at scale this season"}


def github_get(path):
    r = requests.get(f"https://api.github.com/repos/{REPO}/contents/{path}",
                     headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}, timeout=60)
    return r.json() if r.status_code == 200 else None


def github_put(path, content, sha=None):
    body = {"message": f"standings {datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC",
            "content": base64.b64encode(content.encode()).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(f"https://api.github.com/repos/{REPO}/contents/{path}",
                     headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
                     json=body, timeout=60)
    return r.status_code in (200, 201), r.text[:200]


def main():
    if not TOKEN:
        print("LEADERBOARD_TOKEN not set — skipping standings push")
        return
    db = dna.resolve_db()
    title_prop = dna.find_title_prop(db)
    eprop, etype = find_editor_prop(db)
    print(f"Editor property: {eprop or 'none found'}")
    cards = dna.query_all(dna.NOTION_DB, {"property": "Auto Status", "select": {"is_not_empty": True}})
    cards = [c for c in cards if (dna.prop_number(c, "Spend 3d") or 0) >= MIN_SPEND]

    existing = github_get("data.json")
    previous = {}
    if existing:
        try:
            previous = json.loads(base64.b64decode(existing["content"]).decode())
        except Exception:
            previous = {}

    data = {
        "synced": datetime.datetime.utcnow().strftime("%b %d, %H:%M UTC"),
        "writers": standings(cards, lambda c: writer_of(dna.page_title(c, title_prop)), previous.get("writers", [])),
        "editors": standings(cards, lambda c: editor_of(c, eprop, etype), previous.get("editors", [])),
        "hall_of_fame": hall_of_fame(cards, title_prop),
    }
    ok, msg = github_put("data.json", json.dumps(data, indent=1), existing.get("sha") if existing else None)
    print(f"Writers: {[(w['name'], w['share']) for w in data['writers']]}")
    print(f"Editors: {[(e['name'], e['share']) for e in data['editors']]}")
    print("Pushed data.json" if ok else f"Push failed: {msg}")


if __name__ == "__main__":
    main()
