"""
Scarica le statistiche NBA di Dunkest (dunkest.com) in un database SQLite locale.

Logica (uguale per scarico completo e aggiornamento):
  1. per ogni stagione selezionata scarica la tabella riassuntiva (1 chiamata,
     tutti i giocatori con le partite giocate "gp");
  2. per ogni giocatore confronta gp con quello salvato all'ultimo scarico:
     se e' cambiato (o il giocatore e' nuovo) riscarica il suo storico partite.
Quindi lo script e' riprendibile: se si interrompe, rilanciandolo riparte da
dove era arrivato, e le stagioni gia' complete costano 1 sola chiamata.

Esempi:
  python dunkest_sync.py --seasons all            # tutte le regular season
  python dunkest_sync.py                          # solo stagione in corso
  python dunkest_sync.py --seasons all --playoffs # anche i playoff
  python dunkest_sync.py --seasons 19,25          # stagioni specifiche
  python dunkest_sync.py --list-seasons
"""

import argparse
import json
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "https://www.dunkest.com"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "dunkest.sqlite"
LOG_FILE = ROOT / "data" / "sync.log"
USER_AGENT = "Mozilla/5.0 (personal fantasy stats sync; low rate)"

# Usate solo se la pagina del profilo non e' raggiungibile.
FALLBACK_SEASONS = {
    1: "Regular Season 18/19", 2: "Playoffs 18/19", 3: "Regular Season 19/20",
    4: "Playoffs 19/20", 5: "Regular Season 20/21", 6: "Playoffs 20/21",
    7: "Regular Season 21/22", 8: "Playoffs 21/22", 9: "Regular Season 22/23",
    10: "Playoffs 22/23", 13: "Regular Season 23/24", 16: "Playoffs 23/24",
    19: "Regular Season 24/25", 22: "Playoffs 24/25", 25: "Regular Season 25/26",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons (
    season_id   INTEGER PRIMARY KEY,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('regular', 'playoffs'))
);
CREATE TABLE IF NOT EXISTS teams (
    team_id     INTEGER PRIMARY KEY,
    code        TEXT,
    name        TEXT
);
CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,   -- id statistiche dunkest.com
    fantasy_id  INTEGER,               -- id app fantasy (diverso!), quando noto
    first_name  TEXT,
    last_name   TEXT,
    slug        TEXT,
    position_id INTEGER,
    position    TEXT
);
CREATE TABLE IF NOT EXISTS player_seasons (
    player_id   INTEGER NOT NULL,
    season_id   INTEGER NOT NULL,
    team_id     INTEGER,               -- squadra a fine stagione
    position    TEXT,
    cr          REAL,
    gp          INTEGER,
    raw_json    TEXT,                  -- riga completa della tabella (totali)
    PRIMARY KEY (player_id, season_id)
);
CREATE TABLE IF NOT EXISTS player_games (
    game_id          INTEGER NOT NULL,
    player_id        INTEGER NOT NULL,
    season_id        INTEGER NOT NULL,
    game_date        TEXT,
    team_id          INTEGER,          -- ricavato (vedi rebuild_derived)
    opponent_team_id INTEGER,
    opponent_side    TEXT,             -- 'home' / 'visitor' = dove gioca l'avversario
    win              INTEGER,
    overtime         INTEGER,
    valid_dunkest    INTEGER,
    pdk              REAL,
    minutes          REAL,
    starter          INTEGER,
    pts INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, blka INTEGER,
    fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER, ftm INTEGER, fta INTEGER,
    oreb INTEGER, dreb INTEGER, tov INTEGER, pf INTEGER,
    fouls_received INTEGER,
    plus_minus     INTEGER,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_pg_player ON player_games (player_id, season_id);
CREATE INDEX IF NOT EXISTS ix_pg_season ON player_games (season_id, game_date);
CREATE TABLE IF NOT EXISTS games (
    game_id      INTEGER PRIMARY KEY,
    season_id    INTEGER,
    game_date    TEXT,
    home_team_id INTEGER,
    away_team_id INTEGER,
    overtime     INTEGER
);
CREATE TABLE IF NOT EXISTS dunkest_weeks (
    season_id   INTEGER NOT NULL,
    week        INTEGER NOT NULL,      -- giornata Dunkest
    player_id   INTEGER NOT NULL,
    gp          INTEGER,
    cr          REAL,                  -- valore riportato dall'API (sembra quello di fine stagione)
    pdk         REAL,                  -- punti Dunkest totali nella giornata
    plus        REAL,                  -- plusvalenza/minusvalenza della giornata
    minutes     REAL,
    raw_json    TEXT,
    PRIMARY KEY (season_id, week, player_id)
);
CREATE TABLE IF NOT EXISTS dunkest_weeks_done (
    season_id   INTEGER NOT NULL,
    week        INTEGER NOT NULL,
    n_players   INTEGER,
    fetched_at  TEXT,
    PRIMARY KEY (season_id, week)
);
CREATE TABLE IF NOT EXISTS sync_state (
    player_id   INTEGER NOT NULL,
    season_id   INTEGER NOT NULL,
    gp          INTEGER NOT NULL,      -- gp della tabella al momento dello scarico
    n_games     INTEGER NOT NULL,      -- partite effettivamente ricevute
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (player_id, season_id)
);
"""


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class Client:
    def __init__(self, delay, headers=None):
        self.delay = delay
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self.last = 0.0
        self.calls = 0

    def get(self, path, params=None, as_json=True):
        url = path if path.startswith("http") else BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        for attempt in range(6):
            # ritmo leggero: delay +/- 30% tra una richiesta e l'altra
            wait = self.delay * random.uniform(0.7, 1.3) - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8")
                self.calls += 1
                return json.loads(body) if as_json else body
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                status = getattr(e, "code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                pause = 10 * 3 ** attempt  # 10s, 30s, 90s, 270s, ...
                log(f"  errore ({e}); riprovo tra {pause}s")
                time.sleep(pause)
        raise RuntimeError(f"troppi errori su {url}")


def discover_seasons(client):
    try:
        html = client.get("/integrations/player-stats",
                          {"lang": "it", "source": "dunkest", "player_id": 13, "iframe": "yes"},
                          as_json=False)
        found = {int(v): t for v, t in
                 re.findall(r'<option value="(\d+)" title="((?:Regular Season|Playoffs)[^"]*)"', html)}
        if found:
            return found
    except Exception as e:
        log(f"impossibile leggere l'elenco stagioni ({e}), uso quello interno")
    return dict(FALLBACK_SEASONS)


def season_type(label):
    return "playoffs" if label.lower().startswith("playoff") else "regular"


def fetch_season_table(client, season_id):
    params = [("season_id", season_id), ("mode", "nba"), ("stats_type", "tot"),
              ("date_from", "2000-01-01"), ("date_to", "2100-01-01")]
    params += [("teams[]", t) for t in range(1, 31)]
    params += [("positions[]", p) for p in (1, 2, 3)]
    params += [("player_search", ""), ("min_cr", 0), ("max_cr", 100),
               ("sort_by", "pdk"), ("sort_order", "desc")]
    return client.get("/api/stats/table", params)


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def save_table_rows(db, season_id, rows):
    for p in rows:
        pid = int(p["id"])
        db.execute("INSERT OR REPLACE INTO teams (team_id, code, name) VALUES (?,?,?)",
                   (to_int(p["team_id"]), p["team_code"], p["team_name"]))
        db.execute("""INSERT INTO players (player_id, first_name, last_name, slug, position_id, position)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT (player_id) DO UPDATE SET first_name=excluded.first_name,
                        last_name=excluded.last_name, slug=excluded.slug,
                        position_id=excluded.position_id, position=excluded.position""",
                   (pid, p["first_name"], p["last_name"], p.get("slug"),
                    to_int(p["position_id"]), p["position"]))
        db.execute("""INSERT OR REPLACE INTO player_seasons
                      (player_id, season_id, team_id, position, cr, gp, raw_json)
                      VALUES (?,?,?,?,?,?,?)""",
                   (pid, season_id, to_int(p["team_id"]), p["position"], to_float(p["cr"]),
                    to_int(p["gp"]), json.dumps(p, ensure_ascii=False)))
    db.commit()


def save_player_games(db, player_id, season_id, gp, games):
    with db:
        db.execute("DELETE FROM player_games WHERE player_id=? AND season_id=?", (player_id, season_id))
        for g in games:
            minutes = (to_int(g.get("min")) or 0) + (to_int(g.get("sec")) or 0) / 60
            db.execute("""INSERT OR REPLACE INTO player_games VALUES
                (?,?,?,?,NULL,?,?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?)""", (
                to_int(g["gameId"]), player_id, season_id, g.get("gameDate"),
                to_int(g.get("opponentTeamId")), g.get("opponentTeamPosition"),
                int(bool(g.get("playerTeamWin"))), int(bool(g.get("gameOt"))),
                int(bool(g.get("gameValidForDunkest"))),
                to_float(g.get("pdk")), round(minutes, 2), to_int(g.get("starter")),
                to_int(g.get("pts")), to_int(g.get("reb")), to_int(g.get("ast")),
                to_int(g.get("stl")), to_int(g.get("blk")), to_int(g.get("blka")),
                to_int(g.get("fgm")), to_int(g.get("fga")), to_int(g.get("tpm")),
                to_int(g.get("tpa")), to_int(g.get("ftm")), to_int(g.get("fta")),
                to_int(g.get("oreb")), to_int(g.get("dreb")), to_int(g.get("tov")),
                to_int(g.get("pf")), to_int(g.get("foulsReceived")), to_int(g.get("plusMinus")),
            ))
        db.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?,?,?,?)",
                   (player_id, season_id, gp, len(games), datetime.now().isoformat(timespec="seconds")))


def rebuild_derived(db):
    """Ricava la tabella games e la squadra del giocatore in ogni partita.
    L'API indica solo l'avversario e se gioca in casa: 'home' => avversario in casa."""
    with db:
        db.execute("DELETE FROM games")
        db.execute("""
            INSERT INTO games (game_id, season_id, game_date, home_team_id, away_team_id, overtime)
            SELECT game_id, MAX(season_id), MAX(game_date),
                   MAX(CASE WHEN opponent_side='home'    THEN opponent_team_id END),
                   MAX(CASE WHEN opponent_side='visitor' THEN opponent_team_id END),
                   MAX(overtime)
            FROM player_games GROUP BY game_id""")
        db.execute("""
            UPDATE player_games SET team_id = (
                SELECT CASE WHEN player_games.opponent_side='home' THEN g.away_team_id
                            ELSE g.home_team_id END
                FROM games g WHERE g.game_id = player_games.game_id)""")
        # ripiego se manca l'altro lato della partita: squadra di fine stagione
        db.execute("""
            UPDATE player_games SET team_id = (
                SELECT ps.team_id FROM player_seasons ps
                WHERE ps.player_id = player_games.player_id AND ps.season_id = player_games.season_id)
            WHERE team_id IS NULL""")


def sync_season(client, db, season_id, label, max_players=None):
    log(f"== {label} (season_id={season_id})")
    rows = fetch_season_table(client, season_id)
    save_table_rows(db, season_id, rows)
    done = {pid: gp for pid, gp in
            db.execute("SELECT player_id, gp FROM sync_state WHERE season_id=?", (season_id,))}
    todo = [p for p in rows if done.get(int(p["id"])) != to_int(p["gp"])][:max_players]
    log(f"   {len(rows)} giocatori in tabella, {len(todo)} da (ri)scaricare")
    t0 = time.time()
    for i, p in enumerate(todo, 1):
        pid, gp = int(p["id"]), to_int(p["gp"])
        try:
            games = client.get("/api/player/games", {"player_id": pid, "season_id": season_id})
        except urllib.error.HTTPError as e:
            log(f"   salto {p['first_name']} {p['last_name']} (id {pid}): HTTP {e.code}")
            continue
        save_player_games(db, pid, season_id, gp, games)
        if i % 25 == 0 or i == len(todo):
            eta = (time.time() - t0) / i * (len(todo) - i)
            log(f"   {i}/{len(todo)}  {p['first_name']} {p['last_name']}: {len(games)} partite"
                f"  (fine stagione tra ~{eta / 60:.0f} min)")
    return len(todo)


def week_rounds(client, season_id, week):
    return client.get("/api/stats/dunkest/week/rounds", {"season_id": season_id, "week_number": week})


def sync_weeks(client, db, season_id, refresh_last=0):
    """Statistiche Dunkest per giornata (cr, pdk, plus). Disponibili dalla 20/21."""
    if not week_rounds(client, season_id, 1):
        log("   giornate: nessun dato per giornata in questa stagione")
        return
    lo, hi = 1, 150  # ricerca binaria dell'ultima giornata disponibile
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if week_rounds(client, season_id, mid):
            lo = mid
        else:
            hi = mid - 1
    done = {w for (w,) in db.execute("SELECT week FROM dunkest_weeks_done WHERE season_id=?", (season_id,))}
    todo = [w for w in range(1, lo + 1) if w not in done or w > lo - refresh_last]
    log(f"   giornate: {lo} disponibili, {len(todo)} da scaricare")
    for i, w in enumerate(todo, 1):
        params = [("season_id", season_id), ("mode", "dunkest"), ("stats_type", "tot"), ("weeks[]", w)]
        params += [("rounds[]", r) for r in range(1, 5)]
        params += [("teams[]", t) for t in range(1, 31)]
        params += [("positions[]", p) for p in (1, 2, 3)]
        params += [("player_search", ""), ("min_cr", 0), ("max_cr", 100), ("sort_by", "pdk"), ("sort_order", "desc")]
        rows = client.get("/api/stats/table", params)
        with db:
            db.execute("DELETE FROM dunkest_weeks WHERE season_id=? AND week=?", (season_id, w))
            for p in rows:
                db.execute("INSERT OR REPLACE INTO dunkest_weeks VALUES (?,?,?,?,?,?,?,?,?)", (
                    season_id, w, int(p["id"]), to_int(p.get("gp")), to_float(p.get("cr")),
                    to_float(p.get("pdk")), to_float(p.get("plus")), to_float(p.get("min")),
                    json.dumps(p, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO dunkest_weeks_done VALUES (?,?,?,?)",
                       (season_id, w, len(rows), datetime.now().isoformat(timespec="seconds")))
        if i % 10 == 0 or i == len(todo):
            log(f"   giornate: {i}/{len(todo)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="current",
                    help="'current' (default), 'all', oppure elenco di id es. 19,25")
    ap.add_argument("--playoffs", action="store_true", help="includi anche i playoff")
    ap.add_argument("--delay", type=float, default=1.5, help="secondi medi tra le richieste (default 1.5)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--list-seasons", action="store_true")
    ap.add_argument("--max-players", type=int, help="limita i giocatori per stagione (per test)")
    ap.add_argument("--no-weeks", action="store_true", help="non scaricare le statistiche per giornata")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # nomi accentati in console

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    client = Client(args.delay)
    seasons = discover_seasons(client)

    if args.list_seasons:
        for sid, label in sorted(seasons.items()):
            print(f"{sid:>4}  {label}")
        return

    types = {"regular", "playoffs"} if args.playoffs else {"regular"}
    eligible = {s: l for s, l in seasons.items() if season_type(l) in types}
    if args.seasons == "all":
        selected = sorted(eligible)
    elif args.seasons == "current":
        # l'ultima stagione di ogni tipo richiesto
        selected = sorted({max(s for s in eligible if season_type(eligible[s]) == t)
                           for t in types if any(season_type(l) == t for l in eligible.values())})
    else:
        selected = [int(x) for x in args.seasons.split(",")]

    db = sqlite3.connect(args.db, timeout=60)
    db.executescript(SCHEMA)
    for sid, label in seasons.items():
        db.execute("INSERT OR REPLACE INTO seasons VALUES (?,?,?)", (sid, label, season_type(label)))
    db.commit()

    log(f"Avvio sync stagioni {selected} (delay {args.delay}s) -> {args.db}")
    t0 = time.time()
    fetched = 0
    try:
        latest = max(selected) if selected else None
        for sid in selected:
            fetched += sync_season(client, db, sid, seasons.get(sid, f"stagione {sid}"), args.max_players)
            if not args.no_weeks:
                # la stagione piu' recente puo' ancora cambiare: riscarica le ultime 2 giornate
                sync_weeks(client, db, sid, refresh_last=2 if sid == latest else 0)
    except KeyboardInterrupt:
        log("Interrotto: rilancia lo stesso comando per riprendere da qui.")
    finally:
        rebuild_derived(db)
        n = db.execute("SELECT COUNT(*) FROM player_games").fetchone()[0]
        db.close()
        log(f"Fine: {fetched} storici scaricati, {client.calls} chiamate, "
            f"{(time.time() - t0) / 60:.1f} min. Righe partita nel db: {n}")


if __name__ == "__main__":
    sys.exit(main())
