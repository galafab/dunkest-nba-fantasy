"""
Dati anagrafici e draft dei giocatori da Basketball Reference.

Scarica (una pagina ogni 4 secondi: Basketball Reference blocca chi supera 20
richieste al minuto; le pagine restano in cache in data/bref_cache, quindi
rilanciare lo script non riscarica niente):
  - l'indice giocatori per lettera (26 pagine): altezza, peso, data di nascita;
  - le pagine del draft dal 1996 (una per anno): anno e numero di scelta.
    Comprendono anche i rookie appena scelti, che nell'indice ancora non ci sono;
  - per quei rookie (se sono nel listone) la pagina personale: altezza, peso, nascita.
Poi abbina i giocatori ai nostri per nome normalizzato + anni compatibili e, usando
Basketball Reference come ponte, completa il collegamento id fantasy -> id statistiche
(fantasy_players.stats_player_id) dove mancava.

Tabelle create:
  bref_players   giocatori Basketball Reference in attivita' dal 2019 in poi
  bref_draft     scelte al draft dal 1996 (chi non c'e' e ha esordito dopo il 1996 = non scelto)
  players_bio    abbinamento: nostro player_id (o fantasy_id per chi non ha statistiche) -> bref_id

    python bref_anagrafica.py              # scarica (se serve) e abbina
    python bref_anagrafica.py --refresh    # riscarica indice e ultimo draft (nuovi giocatori)
"""

import argparse
import difflib
import html as htmllib
import re
import sqlite3
import string
import time
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "dunkest.sqlite"
CACHE = ROOT / "data" / "bref_cache"
SITE = "https://www.basketball-reference.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MIN_YEAR = 2019            # stagione 2018/19 = anno 2019 su Basketball Reference
FIRST_DRAFT = 1996
PAUSE = 4.0

# nomi scritti diversamente (nome normalizzato nostro -> bref_id), controllati a mano
MANUAL = {
    "adama bal": "balad01", "alexandre sarr": "sarral01", "ej harkless": "harklej01",
    "gregory jackson": "jacksgg01", "hansen yang": "yangha01", "mario hezonja": "hezonma01",
}

# season_id Dunkest -> anno di fine stagione (convenzione Basketball Reference)
SEASON_YEAR = {1: 2019, 3: 2020, 5: 2021, 7: 2022, 9: 2023, 13: 2024, 19: 2025, 25: 2026}
FANTASY_YEAR = 2027        # stagione del listone attuale (2026/27)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bref_players (
    bref_id     TEXT PRIMARY KEY,
    name        TEXT,
    year_min    INTEGER,
    year_max    INTEGER,
    pos         TEXT,
    height_cm   REAL,
    weight_kg   REAL,
    birth_date  TEXT,
    colleges    TEXT
);
CREATE TABLE IF NOT EXISTS bref_draft (
    bref_id     TEXT PRIMARY KEY,
    name        TEXT,
    draft_year  INTEGER,
    pick        INTEGER,               -- scelta assoluta (1-60)
    team        TEXT,
    college     TEXT
);
CREATE TABLE IF NOT EXISTS players_bio (
    player_id   INTEGER,               -- id statistiche (NULL se solo fantasy)
    fantasy_id  INTEGER,               -- id app fantasy, quando noto
    bref_id     TEXT,                  -- NULL = non trovato
    method      TEXT,                  -- exact / manual / fuzzy / none
    UNIQUE (player_id, fantasy_id)
);
"""

INDEX_ROW_RE = re.compile(r'<tr\s*>\s*<th[^>]*data-append-csv="([^"]+)"[^>]*>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'data-stat="(\w+)"(?:\s+csk="([^"]*)")?\s*>(.*?)</t[dh]>', re.S)
DRAFT_ROW_RE = re.compile(r"<tr\s*>(.*?)</tr>", re.S)
BREF_ID_RE = re.compile(r"/players/\w/(\w+)\.html")
TAG_RE = re.compile(r"<[^>]+>")


def fetch(path, cache_name, refresh):
    f = CACHE / cache_name
    if f.exists() and not refresh:
        return f.read_text(encoding="utf-8"), False
    req = urllib.request.Request(SITE + path, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(html, encoding="utf-8")
    time.sleep(PAUSE)
    return html, True


def cells(body):
    return {k: (csk, htmllib.unescape(TAG_RE.sub("", v)).strip()) for k, csk, v in CELL_RE.findall(body)}


def parse_index(html):
    for bref_id, body in INDEX_ROW_RE.findall(html):
        c = cells(body)
        get = lambda k, i=1: c.get(k, ("", ""))[i]
        year_max = int(get("year_max") or 0)
        if year_max < MIN_YEAR:
            continue
        bd = get("birth_date", 0)
        yield dict(
            bref_id=bref_id, name=htmllib.unescape(TAG_RE.sub("", body.split("</th>")[0]).strip()),
            year_min=int(get("year_min") or 0), year_max=year_max, pos=get("pos"),
            height_cm=round(float(get("height", 0)) * 2.54, 1) if get("height", 0) else None,
            weight_kg=round(float(get("weight")) * 0.4536, 1) if get("weight") else None,
            birth_date=f"{bd[:4]}-{bd[4:6]}-{bd[6:]}" if len(bd) == 8 else None,
            colleges=get("colleges"))


def parse_draft(html, year):
    for body in DRAFT_ROW_RE.findall(html):
        if 'data-stat="pick_overall"' not in body or "<td" not in body:
            continue
        m = BREF_ID_RE.search(body)
        c = cells(body)
        if not m or not c.get("pick_overall", ("", ""))[1].isdigit():
            continue
        yield dict(bref_id=m.group(1), name=c["player"][1], draft_year=year,
                   pick=int(c["pick_overall"][1]), team=c.get("team_id", ("", ""))[1],
                   college=c.get("college_name", ("", ""))[1])


def parse_player_page(html, bref_id, year):
    """Anagrafica dalla pagina personale (per i rookie non ancora nell'indice)."""
    meta = html[html.find('id="meta"'):][:8000]
    name = re.search(r"<h1>\s*<span>(.*?)</span>", meta, re.S)
    size = re.search(r"\((\d+)cm,(?:&nbsp;|\s)*(\d+)kg\)", meta)
    birth = re.search(r'data-birth="(\d{4}-\d{2}-\d{2})"', meta)
    pos = re.search(r"Position:\s*</strong>(.*?)(?:&#9642;|</p>)", meta, re.S)
    words = TAG_RE.sub("", pos.group(1)) if pos else ""
    letters = [k for k, w in (("G", "Guard"), ("F", "Forward"), ("C", "Center")) if w in words]
    return dict(bref_id=bref_id, name=htmllib.unescape(name.group(1)) if name else None,
                year_min=year, year_max=year, pos="-".join(letters) or None,
                height_cm=float(size.group(1)) if size else None,
                weight_kg=float(size.group(2)) if size else None,
                birth_date=birth.group(1) if birth else None, colleges=None)


def rookie_pages(db, refresh):
    """Rookie del listone abbinati solo tramite il draft: scarica la loro pagina personale."""
    todo = db.execute("""
        SELECT DISTINCT b.bref_id, d.draft_year FROM players_bio b JOIN bref_draft d USING (bref_id)
        WHERE b.bref_id NOT IN (SELECT bref_id FROM bref_players)""").fetchall()
    rows = []
    for i, (bid, year) in enumerate(todo, 1):
        html, downloaded = fetch(f"/players/{bid[0]}/{bid}.html", f"player_{bid}.html", refresh)
        rows.append(parse_player_page(html, bid, year + 1))
        if downloaded and (i % 10 == 0 or i == len(todo)):
            print(f"pagine rookie: {i}/{len(todo)}", flush=True)
    db.executemany("""INSERT OR REPLACE INTO bref_players VALUES
        (:bref_id, :name, :year_min, :year_max, :pos, :height_cm, :weight_kg, :birth_date, :colleges)""",
                   rows)
    db.commit()
    return rows


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.translate(str.maketrans("", "", string.punctuation))
    words = [w for w in s.split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return " ".join(words)


def our_players(db):
    """{(player_id, fantasy_id): (nome, anni giocati)}; i giocatori solo-fantasy giocano nel 2027."""
    ours = {}
    for pid, fn, ln, seasons in db.execute("""
            SELECT p.player_id, p.first_name, p.last_name, group_concat(ps.season_id)
            FROM players p LEFT JOIN player_seasons ps USING (player_id) GROUP BY p.player_id"""):
        years = {SEASON_YEAR[int(s)] for s in (seasons or "").split(",") if s and int(s) in SEASON_YEAR}
        ours[(pid, None)] = (f"{fn} {ln}", years)
    for fid, fn, ln, pid, pos in db.execute(
            "SELECT fantasy_id, first_name, last_name, stats_player_id, position FROM fantasy_players"):
        if pos == "Head Coach":
            continue
        if pid and (pid, None) in ours:
            n, y = ours.pop((pid, None))
            ours[(pid, fid)] = (n, y | {FANTASY_YEAR})
        elif not pid:
            ours[(None, fid)] = (f"{fn} {ln}", {FANTASY_YEAR})
    return ours


def match(db):
    # candidati: indice giocatori + scelti agli ultimi draft non ancora nell'indice (rookie;
    # i draft piu' vecchi non nell'indice sono giocatori mai arrivati in NBA)
    bref = db.execute("""
        SELECT bref_id, name, year_min, year_max FROM bref_players
        UNION ALL
        SELECT bref_id, name, draft_year + 1, draft_year + 1 FROM bref_draft
        WHERE bref_id NOT IN (SELECT bref_id FROM bref_players) AND draft_year + 1 >= ?""",
                      (FANTASY_YEAR - 1,)).fetchall()
    by_name = {}
    for bid, name, y0, y1 in bref:
        by_name.setdefault(norm(name), []).append((bid, y0, y1))
    names = list(by_name)

    def compatible(cands, years):
        # Basketball Reference aggiorna year_max solo quando il giocatore gioca: tolleranza +1
        return [c for c in cands if not years or any(c[1] <= y <= c[2] + 1 for y in years)]

    rows, stats = [], {"exact": 0, "manual": 0, "fuzzy": 0, "none": 0}
    for (pid, fid), (name, years) in our_players(db).items():
        n = norm(name)
        cands = compatible(by_name.get(n, []), years)
        if n in MANUAL:
            bid, method = MANUAL[n], "manual"
        elif len(cands) == 1:
            bid, method = cands[0][0], "exact"
        elif cands:                                   # omonimi con anni compatibili: da vedere a mano
            bid, method = None, "none"
        elif years == {FANTASY_YEAR}:                 # rookie: niente somiglianze, troppo rischioso
            bid, method = None, "none"
        else:
            close = difflib.get_close_matches(n, names, n=3, cutoff=0.85)
            fuzzy = [c for cn in close for c in compatible(by_name[cn], years)]
            bid, method = (fuzzy[0][0], "fuzzy") if fuzzy else (None, "none")
        stats[method] += 1
        rows.append((pid, fid, bid, method, name))
    db.execute("DELETE FROM players_bio")
    db.executemany("INSERT INTO players_bio (player_id, fantasy_id, bref_id, method) VALUES (?,?,?,?)",
                   [r[:4] for r in rows])
    db.commit()
    return rows, stats


def bridge_fantasy_ids(db):
    """Giocatori fantasy senza id statistiche ma con lo stesso bref_id di un giocatore con statistiche."""
    links = db.execute("""
        SELECT f.fantasy_id, s.player_id, f.bref_id
        FROM players_bio f JOIN players_bio s ON s.bref_id = f.bref_id
        WHERE f.player_id IS NULL AND f.fantasy_id IS NOT NULL AND s.player_id IS NOT NULL
          AND f.method IN ('exact', 'manual') AND s.method IN ('exact', 'manual')""").fetchall()
    for fid, pid, _ in links:
        db.execute("""UPDATE fantasy_players SET stats_player_id = ?
                      WHERE fantasy_id = ? AND stats_player_id IS NULL""", (pid, fid))
        db.execute("UPDATE players SET fantasy_id = ? WHERE player_id = ? AND fantasy_id IS NULL",
                   (fid, pid))
    db.commit()
    return links


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    db = sqlite3.connect(DB, timeout=30)
    db.executescript(SCHEMA)

    players = []
    for letter in string.ascii_lowercase:
        html, _ = fetch(f"/players/{letter}/", f"{letter}.html", args.refresh)    # cambia ogni stagione
        players += list(parse_index(html))
    db.execute("DELETE FROM bref_players")
    db.executemany("""INSERT INTO bref_players VALUES
        (:bref_id, :name, :year_min, :year_max, :pos, :height_cm, :weight_kg, :birth_date, :colleges)""",
                   players)
    print(f"indice: {len(players)} giocatori in attivita' dal {MIN_YEAR}", flush=True)

    draft = {}
    for year in range(FIRST_DRAFT, date.today().year + 1):
        # i draft passati non cambiano: con --refresh si riscarica solo quello dell'anno
        html, downloaded = fetch(f"/draft/NBA_{year}.html", f"draft_{year}.html",
                                 args.refresh and year == date.today().year)
        picks = list(parse_draft(html, year))
        for p in picks:
            draft.setdefault(p["bref_id"], p)
        if downloaded:
            print(f"draft {year}: {len(picks)} scelte", flush=True)
    db.execute("DELETE FROM bref_draft")
    db.executemany("""INSERT INTO bref_draft VALUES
        (:bref_id, :name, :draft_year, :pick, :team, :college)""", list(draft.values()))
    db.commit()
    print(f"draft {FIRST_DRAFT}-{date.today().year}: {len(draft)} scelte")

    match(db)
    rookies = rookie_pages(db, refresh=False)    # pagina personale: solo se mai scaricata
    print(f"rookie con pagina personale: {len(rookies)} "
          f"({sum(r['height_cm'] is not None for r in rookies)} con altezza/peso, "
          f"{sum(r['birth_date'] is not None for r in rookies)} con data di nascita)")
    links = bridge_fantasy_ids(db)
    print(f"\ncollegamenti id fantasy -> statistiche aggiunti: {len(links)}")
    for fid, pid, bid in links:
        print(f"  fantasy {fid} -> stats {pid} ({bid})")

    rows, stats = match(db)         # di nuovo, con i collegamenti nuovi
    print(f"\nabbinamento: {stats}")
    for label, lst in (
            ("non trovati con statistiche", [r for r in rows if r[3] == "none" and r[0] is not None]),
            ("non trovati del listone 2026/27", [r for r in rows if r[3] == "none" and r[0] is None]),
            ("abbinati per somiglianza", [r for r in rows if r[3] == "fuzzy"])):
        print(f"\n{label} ({len(lst)}):")
        for pid, fid, bid, _, name in sorted(lst, key=lambda r: r[4]):
            print(f"  {name:30s} stats={pid} fantasy={fid} -> {bid}")


if __name__ == "__main__":
    main()
