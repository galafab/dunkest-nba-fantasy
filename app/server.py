"""
Server locale dell'app Dunkest: espone i dati del database alle pagine web.

Avvio:  avvia_app.bat            (solo questo PC)
        avvia_app.bat --lan      (anche dal telefono, stessa rete Wi-Fi)
"""

import argparse
import json
import re
import socket
import sqlite3
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "dunkest.sqlite"
STATIC = Path(__file__).resolve().parent / "static"
CACHE = ROOT / "data" / "assets_cache"   # maglie e font scaricati una volta da Dunkest
ASSETS = "https://fantaking-assets.dunkest.com/dunkest"
LEAGUE_CONFIG = "https://fantaking-api.dunkest.com/api/v1/leagues/1/config"
PORT = 8000

POS = {"Guard": "G", "Forward": "F", "Center": "C", "Head Coach": "HC"}

app = FastAPI(title="Dunkest helper")


def connect(readonly=True):
    db = sqlite3.connect(DB.as_uri() + ("?mode=ro" if readonly else ""), uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def q(sql, params=()):
    with connect() as db:
        return [dict(r) for r in db.execute(sql, params)]


def table_exists(name):
    return bool(q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def has_column(table, column):
    return any(r["name"] == column for r in q(f"PRAGMA table_info({table})"))


def cached(rel, url):
    """Scarica un file pubblico di Dunkest la prima volta, poi lo serve dalla cache locale."""
    f = CACHE / rel
    if not f.exists():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
        except Exception:
            raise HTTPException(404, "Risorsa non disponibile")
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{threading.get_ident()}.tmp")  # scrittura atomica: richieste in parallelo
        tmp.write_bytes(data)
        tmp.replace(f)
    return f


def init_db():
    with connect(readonly=False) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS app_saved_teams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        data TEXT NOT NULL,
                        saved_at TEXT NOT NULL)""")


def latest_listone_md(at_most=None):
    """Matchday dell'ultima fotografia del listone (<= at_most se possibile)."""
    if at_most is not None:
        r = q("SELECT MAX(matchday_id) m FROM fantasy_listone WHERE matchday_id <= ?", (at_most,))
        if r and r[0]["m"]:
            return r[0]["m"]
    r = q("SELECT MAX(matchday_id) m FROM fantasy_listone")
    return r[0]["m"] if r else None


@app.get("/api/status")
def status():
    ok = DB.exists() and table_exists("fantasy_listone")
    info = {"db": DB.exists(), "fantasy": ok}
    if ok:
        info["listone_rows"] = q("SELECT COUNT(*) n FROM fantasy_listone")[0]["n"]
        info["mapped"] = q("SELECT COUNT(*) n FROM fantasy_players WHERE stats_player_id IS NOT NULL")[0]["n"]
    return info


@app.get("/api/matchdays")
def matchdays():
    if not table_exists("fantasy_matchdays"):
        return {"current": None, "matchdays": []}
    rows = q("""SELECT md.matchday_id id, md.number, COUNT(m.match_id) n_matches,
                       MIN(m.started_at) first_start, MAX(m.started_at) last_start
                FROM fantasy_matchdays md LEFT JOIN fantasy_matches m USING (matchday_id)
                GROUP BY md.matchday_id ORDER BY md.number""")
    return {"current": latest_listone_md(), "matchdays": rows}


def matchday_games(matchday_id):
    """team_id -> la prima partita della giornata [{round, opp, home, start}]: per Dunkest conta solo quella"""
    games = {}
    rows = q("""SELECT m.round_number, m.started_at, m.home_team_id, m.away_team_id,
                       th.abbreviation home_abbr, ta.abbreviation away_abbr
                FROM fantasy_matches m
                LEFT JOIN fantasy_nba_teams th ON th.team_id = m.home_team_id
                LEFT JOIN fantasy_nba_teams ta ON ta.team_id = m.away_team_id
                WHERE m.matchday_id = ? ORDER BY m.round_number, m.started_at""", (matchday_id,))
    for r in sorted(rows, key=lambda r: r["started_at"]):     # la prima per orario, per ogni squadra
        games.setdefault(r["home_team_id"], [{"round": r["round_number"], "opp": r["away_abbr"], "home": True, "start": r["started_at"]}])
        games.setdefault(r["away_team_id"], [{"round": r["round_number"], "opp": r["home_abbr"], "home": False, "start": r["started_at"]}])
    return games


def form_stats(stats_ids):
    """Forma dalle statistiche dunkest.com: ultima stagione + ultime 5/10 partite."""
    if not stats_ids:
        return {}
    marks = ",".join("?" * len(stats_ids))
    last_season = q("SELECT MAX(season_id) s FROM player_games")[0]["s"]
    out = {}
    for r in q(f"""SELECT player_id, COUNT(*) gp, AVG(pdk) avg, AVG(minutes) mins
                   FROM player_games WHERE season_id = ? AND player_id IN ({marks})
                   GROUP BY player_id""", (last_season, *stats_ids)):
        out[r["player_id"]] = {"season_gp": r["gp"], "season_avg": r["avg"], "season_min": r["mins"]}
    for r in q(f"""WITH g AS (
                     SELECT player_id, pdk,
                            ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY game_date DESC) rn
                     FROM player_games WHERE player_id IN ({marks}))
                   SELECT player_id, AVG(CASE WHEN rn <= 5 THEN pdk END) last5,
                          AVG(CASE WHEN rn <= 10 THEN pdk END) last10
                   FROM g WHERE rn <= 10 GROUP BY player_id""", stats_ids):
        out.setdefault(r["player_id"], {}).update(last5=r["last5"], last10=r["last10"])
    return out


@app.get("/api/players")
def players(matchday: int | None = None):
    if not table_exists("fantasy_listone"):
        raise HTTPException(503, "Dati fantasy assenti: lancia aggiorna.bat")
    md = matchday or latest_listone_md()
    snap = latest_listone_md(md)
    jersey = "p.jersey" if has_column("fantasy_players", "jersey") else "NULL"
    rows = q(f"""SELECT l.fantasy_id id, p.first_name, p.last_name, p.position, p.stats_player_id, {jersey} jersey,
                       l.quotation cr, l.avg_pts, l.popularity, l.probability_of_playing prob,
                       l.is_injured injured, l.team_id, t.abbreviation team
                FROM fantasy_listone l
                JOIN fantasy_players p USING (fantasy_id)
                LEFT JOIN fantasy_nba_teams t ON t.team_id = l.team_id
                WHERE l.matchday_id = ?""", (snap,))
    injuries = {r["fantasy_id"]: r for r in q(
        """SELECT fantasy_id, reason, description, injury_date FROM fantasy_injuries
           WHERE last_seen = (SELECT MAX(last_seen) FROM fantasy_injuries)""")}
    games = matchday_games(md)
    form = form_stats([r["stats_player_id"] for r in rows if r["stats_player_id"]])
    est, est_at = estimates(md)
    for r in rows:
        r["pos"] = POS.get(r.pop("position"), "?")
        r["name"] = f"{r['first_name']} {r['last_name']}"
        r["games"] = games.get(r["team_id"], [])
        r["injury"] = injuries.get(r["id"])
        r["form"] = form.get(r.pop("stats_player_id"), {})
        r["estimate"] = est.get(r["id"])
    return {"matchday": md, "listone_matchday": snap, "players": rows, "estimate_at": est_at}


def estimates(matchday_id):
    """Stima del modello per la giornata (scripts/stima_partita.py): media e quantili delle partite sommate."""
    if not table_exists("stima_giornata"):
        return {}, None
    q37 = "fp_q30 q30, fp_q70 q70," if has_column("stima_giornata", "fp_q30") else ""
    rows = q(f"""SELECT fantasy_id, fp_mean mean, fp_q10 q10, {q37} fp_q50 q50, fp_q90 q90, n_games, minutes, created_at
                 FROM stima_giornata WHERE matchday_id = ?""", (matchday_id,))
    return {r.pop("fantasy_id"): r for r in rows}, (rows[0]["created_at"] if rows else None)


@app.get("/api/estimate/{fantasy_id}")
def estimate_box(fantasy_id: int, matchday: int):
    """Box score medio stimato per ogni partita della giornata (scripts/stima_partita.py)."""
    if not table_exists("stima_partita"):
        return {"games": [], "day": None}
    games = q("""SELECT s.*, t.abbreviation opp FROM stima_partita s
                 LEFT JOIN fantasy_nba_teams t ON t.team_id = s.opp_id
                 WHERE s.fantasy_id = ? AND s.matchday_id = ? ORDER BY s.game_date""", (fantasy_id, matchday))
    day = q("SELECT * FROM stima_giornata WHERE fantasy_id = ? AND matchday_id = ?", (fantasy_id, matchday))
    return {"games": games, "day": day[0] if day else None}


_engine, _engine_lock = {}, threading.Lock()


def engine():
    """Modelli delle stime caricati alla prima richiesta (scripts/stima_partita.py)."""
    with _engine_lock:
        if not _engine:
            sys.path.insert(0, str(ROOT / "scripts"))
            import stima_partita as sp     # importa lightgbm prima di pandas (conflitto OpenMP su Windows)
            models, meta = sp.load_models()
            _engine.update(sp=sp, models=models, meta=meta, noise=sp.load_noise())
        return _engine


class Resim(BaseModel):
    matchday: int
    match_id: int
    minutes: float


@app.post("/api/estimate/{fantasy_id}/resimulate")
def resimulate(fantasy_id: int, body: Resim):
    """Rifà la stima di una partita con i minuti scelti dall'utente."""
    if not 1 <= body.minutes <= 48:
        raise HTTPException(422, "I minuti devono essere tra 1 e 48")
    stale = HTTPException(409, "Per ricalcolare con altri minuti rilancia aggiorna_stime.bat")
    if not table_exists("stima_input"):
        raise stale
    rows = q("SELECT * FROM stima_input WHERE fantasy_id = ? AND matchday_id = ? AND match_id = ?",
             (fantasy_id, body.matchday, body.match_id))
    if not rows:
        raise HTTPException(404, "Partita non trovata nelle stime")
    try:
        e = engine()
    except FileNotFoundError:          # data/models/minutes_noise.npz non ancora creato
        raise stale
    out = e["sp"].resimulate(rows[0], body.minutes, e["models"], e["meta"], e["noise"])
    return {k: (v.item() if hasattr(v, "item") else v) for k, v in out.items()}


class Autofill(BaseModel):
    coach_id: int
    matchday: int
    budget: float = 95
    include: list[int] = []
    exclude: list[int] = []
    objective: str = "punteggio"      # o "plusvalenza"
    top: int = 1


@app.post("/api/autofill")
def autofill(body: Autofill):
    """Rosa con il punteggio atteso piu' alto per l'allenatore scelto (scripts/compila_squadra.py)."""
    if not table_exists("stima_giornata"):
        raise HTTPException(409, "Stime non calcolate: lancia aggiorna_stime.bat")
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import compila_squadra
    with connect() as db:
        try:
            teams = compila_squadra.solve(db, body.coach_id, body.matchday, body.budget, body.include, body.exclude,
                                          body.objective, max(1, min(body.top, 20)))
            return {"objective": body.objective, "teams": teams}
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/api/players/{fantasy_id}")
def player_detail(fantasy_id: int):
    p = q("SELECT * FROM fantasy_players WHERE fantasy_id = ?", (fantasy_id,))
    if not p:
        raise HTTPException(404, "Giocatore non trovato")
    p = p[0]
    games = []
    if p["stats_player_id"]:
        games = q("""SELECT pg.game_date, s.label season, o.code opp, pg.opponent_side,
                            pg.pdk, ROUND(pg.minutes, 1) min, pg.starter, pg.pts, pg.reb, pg.ast,
                            pg.stl, pg.blk, pg.tov, pg.win
                     FROM player_games pg
                     LEFT JOIN teams o ON o.team_id = pg.opponent_team_id
                     LEFT JOIN seasons s ON s.season_id = pg.season_id
                     WHERE pg.player_id = ? ORDER BY pg.game_date DESC LIMIT 60""",
                  (p["stats_player_id"],))
    prices = q("""SELECT md.number matchday, l.quotation cr, l.popularity
                  FROM fantasy_listone l JOIN fantasy_matchdays md USING (matchday_id)
                  WHERE l.fantasy_id = ? ORDER BY md.number""", (fantasy_id,))
    injuries = q("""SELECT injury_date, reason, description, first_seen, last_seen
                    FROM fantasy_injuries WHERE fantasy_id = ? ORDER BY injury_date DESC""", (fantasy_id,))
    p["pos"] = POS.get(p.pop("position"), "?")
    return {"player": p, "games": games, "prices": prices, "injuries": injuries}


@app.get("/api/my-teams")
def my_teams():
    if not table_exists("fantasy_my_teams"):
        return []
    out = []
    for t in q("SELECT fantasy_team_id id, name FROM fantasy_my_teams ORDER BY fantasy_team_id"):
        md = q("""SELECT matchday_id, credits FROM fantasy_team_matchdays
                  WHERE fantasy_team_id = ? ORDER BY matchday_id DESC LIMIT 1""", (t["id"],))
        if not md:
            continue
        t["matchday_id"], t["credits"] = md[0]["matchday_id"], md[0]["credits"]
        t["players"] = q("""SELECT fantasy_id id, court_position, active, is_captain
                            FROM fantasy_rosters WHERE fantasy_team_id = ? AND matchday_id = ?
                            ORDER BY court_position""", (t["id"], t["matchday_id"]))
        out.append(t)
    return out


class SavedTeam(BaseModel):
    name: str
    data: dict


@app.get("/api/saved-teams")
def saved_teams():
    return [dict(r, data=json.loads(r["data"])) for r in
            q("SELECT id, name, data, saved_at FROM app_saved_teams ORDER BY saved_at DESC")]


@app.post("/api/saved-teams")
def save_team(t: SavedTeam):
    with connect(readonly=False) as db:
        db.execute("DELETE FROM app_saved_teams WHERE name = ?", (t.name,))  # stesso nome = sovrascrive
        db.execute("INSERT INTO app_saved_teams (name, data, saved_at) VALUES (?,?,?)",
                   (t.name, json.dumps(t.data), datetime.now().isoformat(timespec="seconds")))
    return {"ok": True}


@app.delete("/api/saved-teams/{team_id}")
def delete_team(team_id: int):
    with connect(readonly=False) as db:
        db.execute("DELETE FROM app_saved_teams WHERE id = ?", (team_id,))
    return {"ok": True}


@app.get("/api/team-styles")
def team_styles():
    """Colore e bordo del numero di maglia per squadra (dalla configurazione pubblica Dunkest)."""
    f = CACHE / "teams.json"
    if not f.exists():
        try:
            req = urllib.request.Request(LEAGUE_CONFIG, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                teams = json.loads(r.read())["data"]["teams"]
        except Exception:
            return {}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(teams), encoding="utf-8")
    teams = json.loads(f.read_text(encoding="utf-8"))
    return {t["abbreviation"]: {"number_color": t.get("number_color"), "border_color": t.get("number_border_color")}
            for t in teams}


@app.get("/assets/jersey/{kind}/{abbr}.png")
def jersey_image(kind: str, abbr: str):
    if kind not in ("players", "coaches") or not re.fullmatch(r"[A-Z]{2,4}", abbr):
        raise HTTPException(404)
    return FileResponse(cached(f"jerseys/{kind}/{abbr}.png", f"{ASSETS}/images/jerseys/{kind}/nba/{abbr}.png"),
                        media_type="image/png")


@app.get("/assets/font/{abbr}.ttf")
def number_font(abbr: str):
    if not re.fullmatch(r"[A-Z]{2,4}", abbr):
        raise HTTPException(404)
    return FileResponse(cached(f"fonts/{abbr}.ttf", f"{ASSETS}/fonts/nba/numbers/{abbr.lower()}.ttf"),
                        media_type="font/ttf")


@app.middleware("http")
async def no_stale_pages(request, call_next):
    """Pagina, script e stile: il browser deve sempre ricontrollare (altrimenti dopo un aggiornamento
    continua a usare la versione vecchia). Maglie e font restano in cache."""
    response = await call_next(request)
    if not request.url.path.startswith(("/assets/", "/api/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="accessibile anche da telefono sulla stessa rete")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not DB.exists():
        raise SystemExit(f"Database non trovato: {DB}. Lancia prima scarica_tutto.bat / aggiorna.bat")
    init_db()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"\n  App Dunkest su  http://localhost:{PORT}")
    if args.lan:
        print(f"  Dal telefono:   http://{lan_ip()}:{PORT}   (stessa rete Wi-Fi)")
    print("  Per chiudere: chiudi questa finestra (o Ctrl+C)\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    # reload: se server.py cambia (aggiornamenti dell'app) si riavvia da solo, senza chiudere la finestra
    uvicorn.run("server:app", app_dir=str(Path(__file__).resolve().parent), host=host, port=PORT,
                log_level="warning", reload=True, reload_dirs=[str(Path(__file__).resolve().parent)],
                reload_includes=["server.py"])


if __name__ == "__main__":
    main()
