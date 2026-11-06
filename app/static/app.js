"use strict";

// --- Regole Dunkest (docs/regolamento) ---------------------------------------
// Slot come nell'app Dunkest: 0-4 quintetto, 5 sesto uomo, 6-9 panchina, 10 allenatore
const COURT = [0, 1, 2, 3, 4], SIXTH = 5, BENCH = [6, 7, 8, 9], COACH = 10, N_SLOTS = 11;
const ROLE_LIMIT = { G: 4, F: 4, C: 2, HC: 1 };
const ROLE_NAME = { G: "Guardia", F: "Ala", C: "Centro", HC: "Allenatore" };
const ROLE_LETTER = { G: "G", F: "A", C: "C", HC: "HC" };   // come sui cartellini Dunkest
const FORMATIONS = [[2, 2, 1], [1, 2, 2], [2, 1, 2], [1, 3, 1], [3, 1, 1]]; // G-F-C del quintetto
const BUDGET = 95;
const MAX_SAME_TEAM = 3;
const CAPTAIN = 1.5;
const SIMULATIONS = 20000;
// Plusvalenza giocatori ricavata dai dati 2020-2025 (docs/plusvalenze.md): esatta ~75%, entro ±0,1 nel 99%.
// Squadra a riposo: nessuna variazione. Sotto i 4 crediti non si scende. Allenatori: formula diversa, non ancora nota.
const PLUS = { a: 0.025, b: 1.77, c: 0.5, min: -1.5, max: 1.5, floor: 4 };
const STORE_KEY = "dunkest-confronto-v2";

let PLAYERS = new Map();   // id -> giocatore (listone + forma + partite giornata)
let STYLES = {};           // sigla squadra -> colori del numero di maglia
let MATCHDAYS = [];
let state;                 // inizializzato in fondo, quando tutte le funzioni sono definite

// --- utilità -----------------------------------------------------------------
const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => { const n = parseFloat(String(v ?? "").replace(",", ".")); return Number.isFinite(n) ? n : null; };
const fmt = (n, d = 1) => n == null ? "–" : n.toLocaleString("it-IT", { minimumFractionDigits: d, maximumFractionDigits: d });
const api = async url => { const r = await fetch(url); if (!r.ok) throw new Error((await r.json()).detail || r.statusText); return r.json(); };

const ICON = {
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  swap: '<path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
};
const icon = name => `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;
const CLOSE = `<button class="icon-btn" data-close title="Chiudi" aria-label="Chiudi">${icon("x")}</button>`;

const slotKind = i => i < 5 ? "Q" : i === SIXTH ? "6" : i < COACH ? "P" : "HC";
const slotLabel = i => i < 5 ? `Titolare ${i + 1}` : i === SIXTH ? "Sesto uomo" : i < COACH ? `Panchina ${i - 5}` : "Allenatore";
const slotShort = i => i < 5 ? `T${i + 1}` : i === SIXTH ? "6°" : i < COACH ? `P${i - 5}` : "All.";
const emptySlot = () => ({ id: null, mode: "single", val: "", min: "", max: "" });

function newTeam(name) {
  return { name, budget: BUDGET, slots: Array.from({ length: N_SLOTS }, emptySlot), captain: null, formation: FORMATIONS[0] };
}

function saveState() { localStorage.setItem(STORE_KEY, JSON.stringify(state)); }
function loadState() {
  try { const s = JSON.parse(localStorage.getItem(STORE_KEY)); if (s) s.teams = s.teams.map(migrateTeam); return s; }
  catch { return null; }
}

// squadre salvate con la prima versione (slot per ruolo) -> slot per posizione
function migrateTeam(t) {
  if (!t?.slots?.length || t.slots[0].role === undefined) return t;
  const team = newTeam(t.name);
  team.budget = t.budget ?? BUDGET;
  const court = t.slots.filter(s => s.status === "Q"), six = t.slots.filter(s => s.status === "6");
  const bench = t.slots.filter(s => s.status === "P"), coach = t.slots.filter(s => s.status === "HC");
  const put = (s, i) => { if (s && i != null) team.slots[i] = { id: s.id, mode: s.mode, val: s.val, min: s.min, max: s.max }; };
  court.forEach((s, k) => put(s, COURT[k]));
  six.forEach(s => put(s, SIXTH));
  bench.forEach((s, k) => put(s, BENCH[k]));
  coach.forEach(s => put(s, COACH));
  if (t.captain != null) { const k = court.indexOf(t.slots[t.captain]); if (k >= 0) team.captain = k; }
  return team;
}

// riferimento "ingenuo" (non e' una stima): media recente x partite della giornata
function reference(p) {
  if (!p) return null;
  const base = p.form?.last5 ?? p.form?.season_avg ?? (p.avg_pts || null);
  if (base == null) return null;
  return Math.round(base * p.games.length * 10) / 10;
}

// --- maglie ------------------------------------------------------------------
function injectFonts() {
  const css = Object.keys(STYLES).map(a =>
    `@font-face{font-family:"num-${a}";src:url(/assets/font/${a}.ttf) format("truetype");font-display:swap}`).join("");
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
}

const EMPTY_SHIRT = `<svg viewBox="0 0 100 162" class="shirt-empty" aria-hidden="true">
  <path d="M30 6 Q50 34 70 6 L86 10 Q83 44 97 56 L97 154 Q50 162 3 154 L3 56 Q17 44 14 10 Z"/>
  <text x="50" y="98" text-anchor="middle">+</text></svg>`;

// la prima volta il server scarica la maglia da Dunkest: se fallisce riprova una volta, poi la nasconde
window.jerseyRetry = img => {
  if (img.dataset.retried) { img.style.visibility = "hidden"; return; }
  img.dataset.retried = "1";
  setTimeout(() => { img.src = img.src.split("?")[0] + "?r=1"; }, 1200);
};

function jerseyHtml(p, big = false) {
  if (!p) return `<div class="jersey empty${big ? " big" : ""}">${EMPTY_SHIRT}</div>`;
  const coach = p.pos === "HC", st = STYLES[p.team] || {};
  const vars = `--num-color:${st.number_color || "#fff"};--num-border:${st.border_color || "transparent"};--num-font:'num-${esc(p.team)}'`;
  return `<div class="jersey${big ? " big" : ""}" style="${vars}">
    <img src="/assets/jersey/${coach ? "coaches" : "players"}/${esc(p.team)}.png" alt="" loading="lazy" onerror="jerseyRetry(this)">
    ${!coach && p.jersey ? `<span class="num">${esc(p.jersey)}</span>` : ""}</div>`;
}

// --- caricamento dati ----------------------------------------------------------
async function init() {
  const st = await api("/api/status");
  if (!st.fantasy) { showBanner("Dati fantasy non ancora scaricati: lancia <b>aggiorna.bat</b> e poi ricarica la pagina."); return; }
  if (st.mapped === 0) showBanner("Abbinamento giocatori-statistiche non ancora fatto: medie e storico arriveranno quando aggiorna.bat termina.");
  STYLES = await api("/api/team-styles").catch(() => ({}));
  injectFonts();
  const md = await api("/api/matchdays");
  MATCHDAYS = md.matchdays;
  if (!state.matchday || !MATCHDAYS.some(m => m.id === state.matchday)) state.matchday = md.current;
  const sel = $("#matchday");
  sel.innerHTML = MATCHDAYS.map(m => {
    const d = m.first_start ? new Date(m.first_start).toLocaleDateString("it-IT", { day: "numeric", month: "short" }) : "";
    return `<option value="${m.id}">G${m.number}${d ? " · " + d : ""}${m.id === md.current ? " (corrente)" : ""}</option>`;
  }).join("");
  sel.value = state.matchday;
  sel.onchange = async () => { state.matchday = +sel.value; saveState(); await loadPlayers(); renderAll(); };
  await loadPlayers();
  renderAll();
}

async function loadPlayers() {
  const data = await api(`/api/players?matchday=${state.matchday}`);
  PLAYERS = new Map(data.players.map(p => [p.id, p]));
  const md = MATCHDAYS.find(m => m.id === state.matchday);
  const estAt = data.estimate_at ? ` · stime del ${new Date(data.estimate_at).toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : " · stime non calcolate per questa giornata";
  $("#data-status").textContent = `${md ? md.n_matches : 0} partite in giornata${estAt}`;
}

function showBanner(html) { const b = $("#banner"); b.innerHTML = html; b.hidden = false; }

// --- calcoli -------------------------------------------------------------------
function slotFactor(team, i) {
  let f = { Q: 1, "6": 1, P: 0.5, HC: 1 }[slotKind(i)];
  if (team.captain === i) f *= CAPTAIN;
  return f;
}

// intervallo della stima mostrato e usato come range: 30-70% (10-90% se le stime sono vecchie, senza q30/q70)
const band = e => e.q30 != null ? { lo: e.q30, hi: e.q70, label: "30–70%" } : { lo: e.q10, hi: e.q90, label: "10–90%" };
// i punteggi previsti si mostrano interi: i decimali sarebbero una precisione che la stima non ha
const pts = v => fmt(v, 0);

// stima del modello per lo slot (solo in modalità Stima e se disponibile per la giornata)
// se l'utente ha modificato il box score dello slot (finestra "Box score stimato") vale la stima modificata
const estOf = s => s.id && s.mode === "estimate" ? editedEstimate(s) ?? PLAYERS.get(s.id)?.estimate ?? null : null;

function slotRange(s) {
  if (s.mode === "estimate") { const e = estOf(s); return e ? [band(e).lo, band(e).hi] : null; }
  if (s.mode === "range") {
    const a = num(s.min), b = num(s.max);
    if (a == null || b == null) return null;
    return [Math.min(a, b), Math.max(a, b)];
  }
  const v = num(s.val);
  return v == null ? null : [v, v];
}

// valore atteso: la media della stima (non il centro del range, che e' asimmetrico), altrimenti il centro
function slotExp(s) {
  const e = estOf(s);
  if (e) return e.mean;
  const r = slotRange(s);
  return r ? (r[0] + r[1]) / 2 : null;
}

// cambio modalità: Fisso e Range tengono i valori inseriti a mano, Stima mostra quelli del modello
function setMode(s, mode) {
  if (mode === "estimate" && !(s.id && PLAYERS.get(s.id)?.estimate)) return;
  s.mode = mode;
}

function teamTotals(team) {
  let min = 0, max = 0, exp = 0, missing = 0, ranged = false;
  team.slots.forEach((s, i) => {
    if (!s.id) return;
    const r = slotRange(s);
    if (!r) { missing++; return; }
    const f = slotFactor(team, i);
    min += f * r[0]; max += f * r[1]; exp += f * slotExp(s);
    if (r[0] !== r[1]) ranged = true;
  });
  return { min, max, exp, missing, ranged };
}

function plusFor(p, pts) {
  if (!p || p.pos === "HC" || pts == null) return null;
  if (!p.games.length) return 0;
  let v = Math.round(PLUS.a * (pts - PLUS.b * p.cr - PLUS.c) * 10) / 10;
  v = Math.min(PLUS.max, Math.max(PLUS.min, v));
  return Math.round((Math.max(PLUS.floor, p.cr + v) - p.cr) * 10) / 10;
}
const breakEven = p => PLUS.b * p.cr + PLUS.c;   // punti per non perdere valore
function plusRange(p, s) {
  const r = slotRange(s);
  if (!r || !p || p.pos === "HC") return null;
  return [plusFor(p, r[0]), plusFor(p, r[1])];
}
const fmtPlus = v => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(Math.abs(v));
function plusText(pr) {
  if (!pr) return "";
  return pr[0] === pr[1] ? `${fmtPlus(pr[0])} cr` : `${fmtPlus(pr[0])} / ${fmtPlus(pr[1])} cr`;
}

// distribuzione triangolare con picco al centro del range
function triangular(a, b) {
  if (a === b) return a;
  const c = (a + b) / 2, u = Math.random();
  return u < (c - a) / (b - a) ? a + Math.sqrt(u * (b - a) * (c - a)) : b - Math.sqrt((1 - u) * (b - a) * (b - c));
}

const gauss = () => {
  let u = 0, v = 0;
  while (!u) u = Math.random();
  while (!v) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};

// Range: triangolare. Stima: normale con la media del modello e deviazione dal range 10-90% (= 2,563 sd)
function simulate(team) {
  const parts = team.slots.map((s, i) => {
    const r = s.id ? slotRange(s) : null;
    if (!r) return null;
    const f = slotFactor(team, i), e = estOf(s);
    if (e) { const sd = (e.q90 - e.q10) / 2.563; return () => f * (e.mean + sd * gauss()); }
    return () => f * triangular(r[0], r[1]);
  }).filter(Boolean);
  return () => parts.reduce((t, draw) => t + draw(), 0);
}

const pl = (team, i) => team.slots[i].id ? PLAYERS.get(team.slots[i].id) : null;
const used = (team, exceptSlot = null) =>
  team.slots.reduce((t, s, i) => t + (i !== exceptSlot && s.id ? (PLAYERS.get(s.id)?.cr || 0) : 0), 0);

function roleCounts(team, exceptSlot = null, slots = null) {
  const c = { G: 0, F: 0, C: 0, HC: 0 };
  (slots || team.slots.map((_, i) => i)).forEach(i => {
    if (i === exceptSlot) return;
    const p = pl(team, i); if (p) c[p.pos]++;
  });
  return c;
}


function allowedRoles(team, i) {
  if (i === COACH) return ["HC"];
  const all = roleCounts(team, i);
  ensureFormation(team);
  return ["G", "F", "C"].filter(r => all[r] < ROLE_LIMIT[r] && (i >= 5 || rowRole(team, i) === r));
}

function checks(team) {
  const out = [];
  const filled = team.slots.filter(s => s.id);
  if (filled.length < N_SLOTS) out.push(["warning", `Mancano ${N_SLOTS - filled.length} giocatori su 11`]);
  const ids = filled.map(s => s.id);
  if (new Set(ids).size !== ids.length) out.push(["critical", "Lo stesso giocatore è inserito due volte"]);
  const u = used(team);
  if (u > team.budget + 1e-9) out.push(["critical", `Crediti superati di ${fmt(u - team.budget)}`]);

  const perTeam = {};
  filled.forEach(s => { const t = PLAYERS.get(s.id)?.team; if (t) perTeam[t] = (perTeam[t] || 0) + 1; });
  const over = Object.entries(perTeam).filter(([, n]) => n > MAX_SAME_TEAM).map(([t, n]) => `${t} (${n})`);
  if (over.length) out.push(["critical", `Più di ${MAX_SAME_TEAM} della stessa squadra NBA: ${over.join(", ")}`]);

  const all = roleCounts(team);
  for (const r of ["G", "F", "C"]) if (all[r] > ROLE_LIMIT[r]) out.push(["critical", `Troppi giocatori nel ruolo ${ROLE_NAME[r]} (${all[r]}/${ROLE_LIMIT[r]})`]);
  const court = roleCounts(team, null, COURT);
  const nCourt = court.G + court.F + court.C;
  const form = `${court.G}-${court.F}-${court.C}`;
  if (nCourt === 5 && !FORMATIONS.some(f => f.join("-") === form)) out.push(["critical", `Modulo ${form} non ammesso (ammessi: ${FORMATIONS.map(f => f.join("-")).join(", ")})`]);
  if (team.captain == null) out.push(["warning", "Scegli un capitano tra i titolari"]);

  filled.forEach(s => {
    const p = PLAYERS.get(s.id);
    if (!p) out.push(["warning", "Un giocatore non è più nel listone"]);
    else if (p.games.length === 0) out.push(["warning", `${p.name} non gioca in questa giornata`]);
    else if (p.injury || p.injured) out.push(["warning", `${p.name}: infortunio${p.injury ? " – " + p.injury.description : ""}`]);
  });
  const t = teamTotals(team);
  if (t.missing) out.push(["warning", `${t.missing} giocatori senza punteggio (contano 0)`]);
  if (!out.length) out.push(["ok", `Squadra valida · modulo ${form}`]);
  return { list: out, used: u, form: nCourt === 5 ? form : null };
}

// --- disegno -------------------------------------------------------------------
function renderAll() {
  state.teams.forEach((_, i) => renderTeam(i));
  renderResult();
  LISTVIEW?.render();
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("active", +b.dataset.tab === state.tab));
  document.querySelectorAll(".team").forEach((el, i) => el.classList.toggle("visible", i === state.tab));
}

// partite della giornata: turno e avversario, "vs" in casa e "@" in trasferta come su Dunkest
const gameLabel = g => `T${g.round} ${g.home ? "vs" : "@"} ${esc(g.opp)}`;
function gamesText(p) {
  if (!p.games.length) return `<span class="bad">riposo</span>`;
  const list = p.games.map(gameLabel).join(", ");
  return p.games.length === 1 ? list : `<b>${p.games.length} partite</b> ${list}`;
}

// cella "partita" del cartellino sul campo
function gameCell(p) {
  if (!p.games.length) return `<b class="bad">—</b><span>riposo</span>`;
  if (p.games.length === 1) { const g = p.games[0]; return `<b>T${g.round}</b><span>${g.home ? "vs" : "@"} ${esc(g.opp)}</span>`; }
  return `<b>${p.games.map(g => "T" + g.round).join("·")}</b><span>${p.games.length} partite</span>`;
}

// f: moltiplicatore mostrato sul cartellino del campo (capitano ×1,5)
function scoreText(s, f = 1) {
  const r = slotRange(s);
  if (!r) return "—";
  if (estOf(s)) return pts(f * estOf(s).mean);
  return r[0] === r[1] ? pts(f * r[0]) : `${pts(f * r[0])}–${pts(f * r[1])}`;
}
// sui cartellini il punteggio è già pesato come conta nel totale: capitano ×1,5, panchina ×0,5
const capF = (team, i) => slotFactor(team, i);

// Modulo come nell'app Dunkest (G-F-C): i posti del quintetto hanno un ruolo fisso, in ordine G, A, C.
// Togliere un giocatore svuota solo il suo posto; cambiare modulo risistema i giocatori per ruolo.
const FORM_ROLES = ["G", "F", "C"];
function rowRole(team, i) {
  const [g, f] = team.formation;
  return i < g ? "G" : i < g + f ? "F" : "C";
}

// sistema il quintetto nel modulo (quello chiesto, quello attuale o il primo compatibile) con ogni giocatore nella sua riga.
// false se il modulo non è compatibile con i ruoli dei titolari.
function arrangeCourt(team, wanted = null) {
  const court = COURT.map(i => team.slots[i]);
  const byRole = { G: [], F: [], C: [] };
  court.forEach(sl => { const p = sl.id ? PLAYERS.get(sl.id) : null; if (p && byRole[p.pos]) byRole[p.pos].push(sl); });
  const fits = ([g, f, k]) => byRole.G.length <= g && byRole.F.length <= f && byRole.C.length <= k;
  const cands = [wanted, team.formation, ...FORMATIONS].filter(Boolean);
  const form = cands.find(fits);
  if (!form || (wanted && !fits(wanted))) return false;
  const capSlot = team.captain != null ? team.slots[team.captain] : null;
  const [g, f, k] = form, next = FORM_ROLES.map(() => 0);
  const out = COURT.map(() => emptySlot());
  FORM_ROLES.forEach((r, ri) => {
    const first = ri === 0 ? 0 : ri === 1 ? g : g + f;
    byRole[r].forEach((sl, n) => { out[first + n] = sl; });
  });
  // giocatori già fuori ruolo (dati vecchi): restano dove c'erano, in un posto libero qualsiasi
  const placed = new Set(out);
  court.filter(sl => sl.id && !placed.has(sl)).forEach(sl => { const k2 = out.findIndex(o => !o.id); if (k2 >= 0) out[k2] = sl; });
  COURT.forEach(i => { team.slots[i] = out[i]; });
  team.formation = form;
  if (capSlot) { const c = COURT.find(i => team.slots[i] === capSlot); team.captain = c ?? null; }
  return true;
}
// un modulo è scelto liberamente se contiene i ruoli dei titolari già in squadra
function formationFits(team, [g, f, k]) {
  const c = roleCounts(team, null, COURT);
  return c.G <= g && c.F <= f && c.C <= k;
}
const ensureFormation = team => { if (!team.formation) arrangeCourt(team); };

function courtLayout(team) {
  ensureFormation(team);
  const rows = { C: [], F: [], G: [] }, hints = {};
  COURT.forEach(i => { const r = rowRole(team, i); rows[r].push(i); if (!team.slots[i].id) hints[i] = r; });
  return { rows, hints };
}

// maglia + cartellino come su Dunkest: ruolo e cognome, crediti e partita, sotto il punteggio previsto
function slotCard(team, i, hint = null, cap = "") {
  const p = pl(team, i), s = team.slots[i];
  const badges = p ? [
    team.captain === i ? `<span class="badge cap-b" title="Capitano ×1,5">C</span>` : "",
    (p.injury || p.injured) ? `<span class="badge inj-b" title="Infortunio">!</span>` : "",
  ].join("") : "";
  const card = p ? `<div class="pcard">
        <div class="pc-head"><span class="pc-role">${ROLE_LETTER[p.pos]}</span><span class="pc-name">${esc(p.last_name)}</span></div>
        <div class="pc-body"><div><b>${fmt(p.cr)}</b><span>CR</span></div><div>${gameCell(p)}</div></div>
        <div class="pc-score${slotRange(s) ? "" : " empty"}" title="Punteggio previsto nella giornata${team.captain === i ? ", già ×1,5 da capitano" : BENCH.includes(i) ? ", già dimezzato (panchina 50%)" : ""}"><b>${scoreText(s, capF(team, i))}</b><span>pt</span></div>
      </div>`
    : `<div class="pcard empty"><div class="pc-head">${esc(hint ? ROLE_NAME[hint] : slotLabel(i))}</div></div>`;
  return `<div class="slot-wrap" data-i="${i}">
      ${cap ? `<div class="slot-cap">${cap}</div>` : ""}
      ${p ? `<button class="slot-x" data-act="remove" title="Togli ${esc(p.name)}" aria-label="Togli ${esc(p.name)}">${icon("x")}</button>` : ""}
      <button class="slot${p ? "" : " is-empty"}" data-act="slot" data-i="${i}" ${hint ? `data-hint="${hint}"` : ""} title="${esc(slotLabel(i))}">
        <div class="badges">${badges}</div>
        ${jerseyHtml(p)}
        ${card}
      </button>
    </div>`;
}

// Fisso / Range / Stima (modello: scripts/stima_partita.py)
function modeSeg(mode, attr, p) {
  const m = mode || "single", e = p?.estimate;
  const why = p?.pos === "HC" ? "Stima non disponibile per gli allenatori" : "Stima non disponibile per questa giornata";
  return `<div class="seg" role="group" aria-label="Tipo di punteggio">
      <button type="button" ${attr}="mode-single" class="${m === "single" ? "on" : ""}" title="Un solo valore previsto">Fisso</button>
      <button type="button" ${attr}="mode-range" class="${m === "range" ? "on" : ""}" title="Punteggio minimo e massimo">Range</button>
      <button type="button" ${attr}="mode-estimate" class="${m === "estimate" ? "on" : ""}" ${e ? "" : "disabled"}
        title="${e ? "Stima del modello: media e range 10–90% (se gioca)" : why}">Stima</button>
    </div>`;
}

function scoreInputs(p, s, attr) {
  const ref = reference(p);
  if (s.mode === "estimate") {
    const e = estOf(s);
    if (!e) return `<span class="small muted">non disponibile</span>`;
    const games = e.n_games > 1 ? ` · ${e.n_games} gare` : "";
    return `<button type="button" class="est-val${e.edited ? " edited" : ""}" ${attr}="est-box" title="Apri il box score stimato da cui viene il punteggio">
        <span class="lbl">${e.edited ? "Stima modificata" : "Stima"}${games} ${icon("info")}</span><b>${pts(e.mean)}</b>
        <span class="sub">${e.edited && e.q30 === e.q70 && e.q10 === e.q90 ? "dal box score modificato" : `${band(e).label}: ${pts(band(e).lo)}–${pts(band(e).hi)}`}</span></button>`;
  }
  return s.mode === "range"
    ? `<input type="text" inputmode="decimal" ${attr}="min" value="${esc(s.min)}" placeholder="min" aria-label="Minimo">
       <span class="range-sep">–</span>
       <input type="text" inputmode="decimal" ${attr}="max" value="${esc(s.max)}" placeholder="max" aria-label="Massimo">`
    : `<input type="text" inputmode="decimal" ${attr}="val" value="${esc(s.val)}" placeholder="${p.estimate ? "stima " + pts(p.estimate.mean) : ref != null ? "rif. " + fmt(ref) : "punti"}" aria-label="Punteggio">`;
}

function scoreRow(team, i) {
  const p = pl(team, i), s = team.slots[i];
  if (!p) return "";
  const info = [esc(p.team), ROLE_NAME[p.pos], `${fmt(p.cr)} cr`, p.pos === "HC" ? "" : gamesText(p)].filter(Boolean).join(" · ");
  const form = [
    p.form?.last5 != null ? `<span title="Media punti Dunkest nelle ultime 5 partite giocate (anche della stagione scorsa)">media ult. 5: <b>${fmt(p.form.last5)}</b></span>` : "",
    p.pos !== "HC" && p.games.length ? `<span title="Punti da fare nella giornata perché il valore in crediti non scenda">pareggio crediti: <b>${fmt(breakEven(p), 0)}</b> pt</span>` : "",
  ].filter(Boolean).join(" · ");
  return `<div class="srow" data-i="${i}">
      <button class="who-j" data-act="slot" title="${esc(slotLabel(i))}">${jerseyHtml(p)}<span class="role">${slotShort(i)}</span></button>
      <div class="who"><button class="link" data-act="slot">${esc(p.name)}</button>${team.captain === i ? ` <span class="badge cap-b inline" title="Capitano ×1,5">C</span>` : ""}
        <div class="meta">${info}</div>${form ? `<div class="meta">${form}</div>` : ""}</div>
      <div class="score">${modeSeg(s.mode, "data-act", p)}<div class="score-inputs">${scoreInputs(p, s, "data-act")}</div></div>
      <div class="contrib-cell"><span class="lbl" data-factor="${i}">Conta</span><span class="contrib" data-contrib="${i}"></span><span class="dplus" data-plus="${i}"></span></div>
    </div>`;
}

function benchHtml(team) {
  return `<div class="bench">
      <div class="group-title bt-6" title="Sesto uomo: conta al 100%">6° · 100%</div>
      <div class="group-title bt-p">Panchina · 50%</div>
      <div class="group-title bt-hc">Allenatore</div>
      ${slotCard(team, SIXTH, null, "6° · 100%")}
      ${BENCH.map(i => slotCard(team, i, null, `${slotShort(i)} · 50%`)).join("")}
      ${slotCard(team, COACH, null, "All.")}
    </div>`;
}

function courtHtml(team) {
  const { rows, hints } = courtLayout(team);
  return `<div class="court">${["C", "F", "G"].filter(r => rows[r].length).map(r =>
    `<div class="court-row">${rows[r].sort((a, b) => a - b).map(i => slotCard(team, i, hints[i])).join("")}</div>`).join("")}</div>`;
}

function renderTeam(ti) {
  ensureFormation(state.teams[ti]);
  ensureCaptain(state.teams[ti]);
  const team = state.teams[ti], el = $(`#team-${ti}`), c = checks(team);
  el.innerHTML = `
    <div class="team-head">
      <span class="tag">${ti === 0 ? "A" : "B"}</span>
      <input type="text" class="tname" data-act="name" value="${esc(team.name)}" aria-label="Nome squadra" title="Clicca per rinominare">
    </div>
    <div class="team-actions">
      <button class="btn primary" data-act="load-mine">${icon("user")}Mia squadra</button>
      <button class="btn" data-act="autofill" title="Crea la squadra con il punteggio previsto più alto: scegli allenatore e, se vuoi, giocatori da avere o da escludere">${icon("bolt")}Squadra automatica</button>
      <button class="btn" data-act="save">${icon("save")}Salva</button>
      <button class="btn" data-act="copy">${icon("copy")}Copia in ${ti === 0 ? "B" : "A"}</button>
      <button class="btn ghost danger" data-act="clear" title="Svuota la squadra">${icon("trash")}</button>
    </div>
    <div class="kpis">
      <label class="kpi"><span class="k">Crediti</span><input type="number" step="0.1" data-act="budget" value="${team.budget}"></label>
      <div class="kpi"><span class="k">Usati</span><b>${fmt(c.used)}</b></div>
      <div class="kpi ${c.used > team.budget ? "over" : "good"}"><span class="k">Restano</span><b>${fmt(team.budget - c.used)}</b></div>
      <label class="kpi"><span class="k">Modulo</span><select data-act="formation" aria-label="Modulo del quintetto" title="Modulo (guardie-ali-centri) del quintetto">${FORMATIONS.map(fm => {
        const ok = formationFits(team, fm);
        return `<option value="${fm.join("-")}" ${team.formation.join("-") === fm.join("-") ? "selected" : ""} ${ok ? "" : "disabled"}>${fm.join("-")}${ok ? "" : " (non compatibile)"}</option>`;
      }).join("")}</select></label>
    </div>
    ${courtHtml(team)}
    ${benchHtml(team)}
    <div class="scores-head">
      <div class="group-title">Punteggi previsti</div>
      <div class="all-modes"><span class="small muted">Imposta tutti:</span>
        <button class="btn sm" data-act="all-single">Fisso</button><button class="btn sm" data-act="all-range">Range</button><button class="btn sm" data-act="all-estimate" title="Tutti i giocatori con una stima per questa giornata">Stima</button></div>
    </div>
    <p class="legend"><b>Fisso</b> un valore scritto da te · <b>Range</b> minimo e massimo scritti da te ·
      <b>Stima</b> del modello: media e, sotto, l'intervallo 30–70%, dove cade il punteggio 4 volte su 10 (se il giocatore gioca).
      <b>Conta</b> = punteggio × peso dello slot (panchina ×0,5, capitano ×1,5): è quello che entra nel totale.</p>
    <div class="scores">${team.slots.map((_, i) => scoreRow(team, i)).join("") || `<p class="muted small empty-note">Aggiungi giocatori toccando le maglie.</p>`}</div>
    <ul class="checks">${c.list.map(([k, t]) => `<li class="${k}"><span class="ck">${k === "ok" ? "✓" : k === "warning" ? "!" : "✕"}</span>${esc(t)}</li>`).join("")}</ul>
    <div class="team-total"><span>Totale previsto</span><span class="total-num" data-total></span></div>
    <div class="team-plus"><span title="Somma delle plusvalenze previste dei giocatori (allenatore escluso)">Variazione crediti prevista</span><span data-tplus></span></div>`;
  updateNumbers(ti);
}

// aggiorna solo contributi e totale (senza ridisegnare: non perde il focus mentre scrivi)
function updateNumbers(ti) {
  const team = state.teams[ti], el = $(`#team-${ti}`);
  team.slots.forEach((s, i) => {
    const cell = el.querySelector(`[data-contrib="${i}"]`);
    const chip = el.querySelector(`.slot[data-i="${i}"] .pc-score`);
    if (chip) {
      chip.querySelector("b").textContent = scoreText(s, capF(team, i));
      chip.classList.toggle("empty", !slotRange(s));
      chip.classList.toggle("edited", !!estOf(s)?.edited);
    }
    if (!cell) return;
    const r = slotRange(s), f = slotFactor(team, i), e = estOf(s);
    cell.textContent = !r ? "" : e ? pts(f * e.mean) : r[0] === r[1] ? pts(f * r[0]) : `${pts(f * r[0])}–${pts(f * r[1])}`;
    cell.className = "contrib" + (f < 1 ? " dim" : "");
    cell.title = e ? `× ${fmt(f, 2)} · ${band(e).label} ${pts(f * band(e).lo)}–${pts(f * band(e).hi)}` : `× ${fmt(f, 2)}`;
    const fac = el.querySelector(`[data-factor="${i}"]`);
    fac.textContent = f === 1 ? "Conta" : team.captain === i ? `Conta ×${fmt(f, 1)} cap.` : `Conta ×${fmt(f, 1)}`;
  });
  const t = teamTotals(team);
  el.querySelector("[data-total]").textContent = t.ranged ? `${pts(t.exp)} (${pts(t.min)}–${pts(t.max)})` : pts(t.exp);

  let lo = 0, hi = 0, any = false;
  team.slots.forEach((s, i) => {
    const p = pl(team, i), cell = el.querySelector(`[data-plus="${i}"]`);
    if (!cell || !p) return;
    if (p.pos === "HC") { cell.textContent = slotRange(s) ? "crediti: in arrivo" : ""; cell.className = "dplus"; cell.title = "Plusvalenza allenatori: formula in lavorazione"; return; }
    const pr = plusRange(p, s);
    cell.textContent = pr ? "crediti " + plusText(pr).replace(/ cr$/, "") : "";
    cell.className = "dplus" + (pr ? (pr[0] + pr[1] > 0 ? " up" : pr[0] + pr[1] < 0 ? " down" : "") : "");
    cell.title = pr ? `Crediti dopo la giornata: ${fmt(p.cr + pr[0])}${pr[0] !== pr[1] ? " – " + fmt(p.cr + pr[1]) : ""}` : "";
    if (pr) { lo += pr[0]; hi += pr[1]; any = true; }
  });
  const tp = el.querySelector("[data-tplus]");
  tp.textContent = any ? plusText([Math.round(lo * 10) / 10, Math.round(hi * 10) / 10]) : "–";
  tp.className = any ? (lo + hi > 0 ? "up" : lo + hi < 0 ? "down" : "") : "";
}

function renderResult() {
  const [A, B] = state.teams;
  const ta = teamTotals(A), tb = teamTotals(B);
  const card = (team, t, cls, tag) => `
    <div class="res-card ${cls}">
      <div><b>${tag}</b> · ${esc(team.name)}</div>
      <div class="big">${pts(t.exp)}</div>
      <div class="small muted">${t.ranged ? `range ${pts(t.min)} – ${pts(t.max)}` : "valore singolo"}${t.missing ? ` · ${t.missing} senza punteggio` : ""}</div>
    </div>`;
  let verdict;
  if (ta.exp || tb.exp) {
    const diff = ta.exp - tb.exp;
    verdict = Math.abs(diff) < 0.5 ? `<div class="verdict">Parità di punteggio atteso.</div>`
      : `<div class="verdict"><b>${esc(diff >= 0 ? A.name : B.name)}</b> è avanti di <b>${pts(Math.abs(diff))}</b> punti attesi.</div>`;
    if (ta.ranged || tb.ranged) {
      const sa = simulate(A), sb = simulate(B);
      let wins = 0, ties = 0;
      for (let k = 0; k < SIMULATIONS; k++) { const d = sa() - sb(); if (Math.abs(d) < 1e-9) ties++; else if (d > 0) wins++; }
      const pa = (wins + ties / 2) / SIMULATIONS;
      const wa = Math.round(pa * 100), wb = 100 - wa;
      verdict += `
        <div class="win">
          <div class="win-labels"><span><i class="dot a"></i><b>A</b> ${esc(A.name)} · <b>${wa}%</b></span>
            <span><b>${wb}%</b> · ${esc(B.name)} <b>B</b><i class="dot b"></i></span></div>
          <div class="winbar" role="img" aria-label="Probabilità di vittoria: A ${wa}%, B ${wb}%">
            <div class="wa" style="flex:${Math.max(wa, 1)}"></div><div class="wb" style="flex:${Math.max(wb, 1)}"></div></div>
          <div class="small muted">Probabilità di vittoria su ${SIMULATIONS.toLocaleString("it-IT")} simulazioni dentro i range</div>
        </div>`;
    }
  } else verdict = `<div class="verdict small muted">Inserisci i punteggi previsti dei giocatori per vedere il confronto.</div>`;
  $("#result").innerHTML = `<h2>Confronto</h2><div class="res-grid">${card(A, ta, "a", "A")}${card(B, tb, "b", "B")}</div>${verdict}`;
}

// il capitano c'è sempre: se manca o non è più un titolare, diventa il primo titolare in campo
function ensureCaptain(team) {
  if (team.captain != null && team.captain < 5 && team.slots[team.captain]?.id) return;
  const first = COURT.find(i => team.slots[i]?.id);
  team.captain = first ?? null;
}

function refresh(ti) { ensureCaptain(state.teams[ti]); saveState(); renderTeam(ti); renderResult(); }

// --- eventi squadra ----------------------------------------------------------------
function onTeamEvent(ti, e) {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act, team = state.teams[ti];
  const holder = t.closest("[data-i]"), i = holder ? +holder.dataset.i : null, s = i != null ? team.slots[i] : null;
  const live = ["val", "min", "max"].includes(act), onChange = ["budget", "name", "formation"].includes(act);
  if (e.type === "input" && !live) return;
  if (e.type === "change" && !onChange) return;
  if (e.type === "click" && (live || onChange)) return;

  switch (act) {
    case "val": case "min": case "max":
      s[act] = t.value; saveState(); updateNumbers(ti); renderResult(); return;
    case "slot": s.id ? openSheet(ti, i) : openPicker(ti, i, t.dataset.hint); return;
    case "est-box": openEstimate(ti, i); return;
    case "remove": team.slots[i] = emptySlot(); if (team.captain === i) team.captain = null; break;
    case "mode-single": case "mode-range": case "mode-estimate": setMode(s, act.slice(5)); break;
    case "all-single": case "all-range": case "all-estimate": team.slots.forEach(x => setMode(x, act.slice(4))); break;
    case "formation": arrangeCourt(team, t.value.split("-").map(Number)); break;
    case "budget": team.budget = num(t.value) ?? BUDGET; break;
    case "name": team.name = t.value || (ti ? "Squadra B" : "Squadra A"); saveState(); renderResult(); return;
    case "copy": state.teams[1 - ti] = { ...structuredClone(team), name: team.name + " (copia)" }; saveState(); renderAll(); return;
    case "clear":
      askDialog({ title: "Svuotare la squadra?", text: `"${team.name}" verrà svuotata e il nome ripristinato.`, ok: "Svuota", danger: true })
        .then(yes => { if (yes) { state.teams[ti] = newTeam(ti ? "Squadra B" : "Squadra A"); refresh(ti); } });
      return;
    case "save": saveTeam(ti); return;
    case "load-mine": loadMine(ti); return;
    case "autofill": autoFill(ti); return;
  }
  refresh(ti);
}

for (const ti of [0, 1]) {
  const el = $(`#team-${ti}`);
  for (const type of ["click", "change", "input"]) el.addEventListener(type, e => onTeamEvent(ti, e));
}
$("#tabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.tab = +b.dataset.tab; saveState(); renderAll();
});

// --- finestra modale -----------------------------------------------------------
const dlg = $("#player-modal");
function openModal(html, bind) {
  // contenitore nuovo a ogni apertura: niente gestori rimasti dalle finestre precedenti
  const old = $("#modal-body"), body = old.cloneNode(false);
  old.replaceWith(body);
  body.innerHTML = html;
  body.onclick = e => { if (e.target.closest("[data-close]")) dlg.close(); };
  bind?.(body);
  if (!dlg.open) dlg.showModal();
}
dlg.addEventListener("close", () => { const t = $("#tooltip"); t.hidden = true; if (t.matches(":popover-open")) t.hidePopover(); });

// conferma / richiesta di testo dentro l'app (niente finestre del browser); risolve con true / il testo, o null se annullata
function askDialog({ title, text = "", value = null, ok = "Conferma", danger = false }) {
  return new Promise(resolve => {
    const d = document.createElement("dialog");
    d.className = "ask";
    d.innerHTML = `<form method="dialog"><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ""}
      ${value != null ? `<input type="text" class="ask-input" value="${esc(value)}">` : ""}
      <div class="ask-btns"><button type="button" class="btn" data-no>Annulla</button>
      <button type="submit" class="btn ${danger ? "danger" : "primary"}">${esc(ok)}</button></div></form>`;
    const input = d.querySelector("input");
    let result = null;
    d.querySelector("form").onsubmit = () => { result = input ? input.value.trim() || null : true; };
    d.querySelector("[data-no]").onclick = () => d.close();
    d.addEventListener("close", () => { d.remove(); resolve(result); });
    document.body.append(d);
    d.showModal();
    input?.select();
  });
}

// apre la finestra in caricamento; se la richiesta fallisce lo dice invece di restare in "Caricamento…"
async function fetchForModal(url) {
  openModal(`<p class="muted">Caricamento…</p>`);
  try { return await api(url); }
  catch (err) {
    const old = /not found/i.test(err.message) ? " Probabilmente il server è di una versione precedente: chiudi e riavvia <b>avvia_app.bat</b>." : "";
    openModal(`<div class="modal-head"><h2>Dati non disponibili</h2>${CLOSE}</div><p>Errore: ${esc(err.message)}.${old}</p>`);
    return null;
  }
}

// --- elenco giocatori: unico componente per la scheda "Elenco giocatori" e per la scelta dallo slot ------
// plusvalenza attesa in crediti: formula di docs/plusvalenze.md sulla stima media, pesata con la probabilità di giocare
// (-0,1 se non gioca pur avendo la squadra in campo; squadra a riposo 0), come nel solver di "Squadra automatica"
function plusExp(p) {
  if (p.pos === "HC" || !p.estimate) return null;
  if (!p.games.length) return 0;
  const pr = p.prob ?? 1;
  return pr * plusFor(p, p.estimate.mean) + (1 - pr) * -0.1;
}
const gameShort = p => !p.games.length ? `<span class="bad">riposo</span>`
  : p.games.length === 1 ? gameLabel(p.games[0]) : `${p.games.length} partite`;

const PCOLS = [
  { k: "name", label: "Giocatore", val: p => p.name, dir: 1 },
  { k: "game", label: "Partita", val: p => p.games.length, cell: gameShort },
  { k: "cr", label: "Crediti", val: p => p.cr, cell: p => fmt(p.cr) },
  { k: "min", label: "Minuti", opt: true, val: p => p.estimate?.minutes, cell: p => p.estimate?.minutes != null ? fmt(p.estimate.minutes, 0) : "–" },
  { k: "est", label: "Stima", val: p => p.estimate?.mean, cell: p => p.estimate ? `<b>${pts(p.estimate.mean)}</b>` : "–" },
  { k: "band", label: "Intervallo", opt: true, val: p => p.estimate && band(p.estimate).hi,
    cell: p => p.estimate ? `${pts(band(p.estimate).lo)}–${pts(band(p.estimate).hi)}` : "–" },
  { k: "prob", label: "Gioca", val: p => p.prob, cell: p => p.prob != null ? fmt(p.prob * 100, 0) + "%" : "–" },
  { k: "plus", label: "Plus attesa", val: plusExp,
    cell: p => { const v = plusExp(p); return v == null ? "–" : `<span class="${v > 0.04 ? "up" : v < -0.04 ? "down" : ""}">${fmtPlus(Math.round(v * 10) / 10)}</span>`; } },
  { k: "avg", label: "Media", opt: true, val: p => p.form?.season_avg ?? p.avg_pts, cell: p => fmt(p.form?.season_avg ?? p.avg_pts) },
  { k: "last5", label: "Ult. 5", opt: true, val: p => p.form?.last5, cell: p => fmt(p.form?.last5) },
];

// host: elemento dove disegnare. o.onPick(id): modo scelta (clic sulla riga inserisce); senza, clic = scheda del giocatore.
// o.roles: ruoli selezionabili; o.start: ruoli attivi all'inizio; o.free: crediti residui (filtro "entro i crediti");
// o.taken: id non selezionabili; o.filter: ulteriore condizione sui giocatori. Ritorna { render } per aggiornare i dati.
function playerList(host, o = {}) {
  const roles = o.roles ?? ["G", "F", "C", "HC"];
  const f = { q: "", roles: new Set(o.start ?? roles), team: "", playing: false, healthy: false, affordable: o.free != null,
    sort: "est", dir: -1 };
  const teams = [...new Set([...PLAYERS.values()].map(p => p.team).filter(Boolean))].sort();
  const LIMIT = 300;
  host.innerHTML = `
    <div class="pfilters">
      <input type="search" class="psearch" placeholder="Cerca nome o sigla squadra (es. BOS)" autocomplete="off">
      <div class="chips">${roles.map(r => `<button class="chip${f.roles.has(r) ? " on" : ""}" data-role="${r}">${ROLE_NAME[r]}</button>`).join("")}</div>
      <select class="pteam"><option value="">Tutte le squadre</option>${teams.map(t => `<option>${esc(t)}</option>`).join("")}</select>
      <label><input type="checkbox" data-f="playing"> solo chi gioca in giornata</label>
      <label><input type="checkbox" data-f="healthy"> senza infortunati</label>
      ${o.free != null ? `<label><input type="checkbox" data-f="affordable" checked> entro i crediti (${fmt(o.free)})</label>` : ""}
      <span class="pcount small muted"></span>
    </div>
    <div class="plist"><table class="ptable${o.onPick ? " pick" : ""}"><thead><tr>${PCOLS.map(c =>
      `<th data-sort="${c.k}" class="${c.opt ? "c-opt" : ""}">${c.label}<span class="arr"></span></th>`).join("")}</tr></thead>
      <tbody></tbody></table></div>`;

  const render = () => {
    const q = f.q.trim().toLowerCase(), col = PCOLS.find(c => c.k === f.sort);
    const list = [...PLAYERS.values()].filter(p => f.roles.has(p.pos) && (!o.filter || o.filter(p))
      && (!q || p.name.toLowerCase().includes(q) || (p.team || "").toLowerCase() === q)
      && (!f.team || p.team === f.team)
      && (!f.playing || p.games.length > 0)
      && (!f.healthy || !(p.injury || p.injured))
      && (!f.affordable || p.cr <= o.free + 1e-9));
    const key = p => { const v = col.val(p); return v == null || Number.isNaN(v) ? null : v; };
    list.sort((a, b) => {
      const x = key(a), y = key(b);
      if (x == null || y == null) return (x == null) - (y == null);   // senza valore sempre in fondo
      return (typeof x === "string" ? x.localeCompare(y, "it") : x - y) * f.dir || a.name.localeCompare(b.name, "it");
    });
    const total = list.length;
    host.querySelector(".pcount").textContent = total > LIMIT ? `primi ${LIMIT} di ${total}` : `${total} giocatori`;
    host.querySelector("tbody").innerHTML = list.slice(0, LIMIT).map(p => {
      const taken = o.taken?.has(p.id);
      const warn = (p.injury || p.injured) ? ` <span class="warn" title="${esc(p.injury ? p.injury.reason + " – " + p.injury.description : "infortunato")}">⚠</span>` : "";
      return `<tr data-pid="${p.id}" class="${taken ? "taken" : ""}">${PCOLS.map(c => c.k === "name"
        ? `<td class="pname">${jerseyHtml(p)}<div><b>${esc(p.name)}</b>${warn}<div class="small muted">${esc(p.team)} · ${p.pos}${taken ? " · già in squadra" : ""}</div></div></td>`
        : `<td class="num${c.opt ? " c-opt" : ""}">${c.cell(p)}</td>`).join("")}</tr>`;
    }).join("") || `<tr><td colspan="${PCOLS.length}" class="muted">Nessun giocatore con questi filtri.</td></tr>`;
    host.querySelectorAll("th[data-sort]").forEach(th => {
      const on = th.dataset.sort === f.sort;
      th.classList.toggle("on", on);
      th.querySelector(".arr").textContent = on ? (f.dir > 0 ? " ▲" : " ▼") : "";
    });
  };

  const search = host.querySelector(".psearch");
  search.addEventListener("input", () => { f.q = search.value; render(); });
  host.querySelector(".pteam").addEventListener("change", e => { f.team = e.target.value; render(); });
  host.querySelectorAll("[data-f]").forEach(cb => cb.addEventListener("change", () => { f[cb.dataset.f] = cb.checked; render(); }));
  host.querySelector(".chips").addEventListener("click", e => {
    const c = e.target.closest("[data-role]"); if (!c) return;
    if (f.roles.has(c.dataset.role) && f.roles.size > 1) f.roles.delete(c.dataset.role); else f.roles.add(c.dataset.role);
    host.querySelectorAll("[data-role]").forEach(x => x.classList.toggle("on", f.roles.has(x.dataset.role)));
    render();
  });
  host.querySelector("thead").addEventListener("click", e => {
    const th = e.target.closest("th[data-sort]"); if (!th) return;
    const k = th.dataset.sort;
    f.dir = f.sort === k ? -f.dir : (PCOLS.find(c => c.k === k).dir ?? -1);   // il nome parte A→Z, i numeri dal più alto
    f.sort = k; render();
  });
  host.querySelector("tbody").addEventListener("click", e => {
    const tr = e.target.closest("[data-pid]"); if (!tr || tr.classList.contains("taken")) return;
    if (o.onPick) o.onPick(+tr.dataset.pid); else openPlayer(+tr.dataset.pid);
  });
  render();
  if (o.onPick && matchMedia("(pointer: fine)").matches) search.focus();
  return { render };
}

// --- scelta giocatore per uno slot -------------------------------------------------------
function openPicker(ti, i, hint = null) {
  const team = state.teams[ti];
  const roles = allowedRoles(team, i);
  if (!roles.length) {
    openModal(`<div class="modal-head"><h2>${esc(slotLabel(i))}</h2>${CLOSE}</div>
      <p>Nessun ruolo disponibile per questo slot: la rosa ha già 4 guardie, 4 ali e 2 centri, oppure il quintetto non ammetterebbe un modulo valido.</p>`);
    return;
  }
  const taken = new Set(team.slots.filter((s, k) => s.id && k !== i).map(s => s.id));
  const start = hint && roles.includes(hint) ? [hint] : roles;   // slot vuoto nella riga di un ruolo
  openModal(`<div class="modal-head"><h2>${esc(slotLabel(i))}</h2>${CLOSE}</div><div class="plhost"></div>`, body =>
    playerList(body.querySelector(".plhost"), { roles, start, taken, free: team.budget - used(team, i),
      onPick: id => { team.slots[i] = { ...emptySlot(), id }; dlg.close(); refresh(ti); } }));
}

// --- pagina "Elenco giocatori" -------------------------------------------------------------
let PAGE = "compare", LISTVIEW = null;
function setPage(page) {
  PAGE = page;
  $("#page-compare").hidden = page !== "compare";
  $("#page-list").hidden = page !== "list";
  document.querySelectorAll("#pages button").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  if (page === "list") { LISTVIEW ??= playerList($("#page-list")); LISTVIEW.render(); }
}
$("#pages").addEventListener("click", e => { const b = e.target.closest("[data-page]"); if (b) setPage(b.dataset.page); });

function sheetPlus(p, s) {
  if (p.pos === "HC") return `Variazione crediti: <i>formula degli allenatori in lavorazione</i>`;
  if (!p.games.length) return `Squadra a riposo in questa giornata: crediti invariati`;
  const pr = plusRange(p, s), be = `per non perdere crediti servono circa <b>${fmt(breakEven(p), 0)}</b> punti`;
  if (!pr) return `Plusvalenza: ${be}`;
  const after = pr[0] === pr[1] ? fmt(p.cr + pr[0]) : `${fmt(p.cr + pr[0])}–${fmt(p.cr + pr[1])}`;
  return `Plusvalenza prevista <b class="${pr[0] + pr[1] > 0 ? "up" : pr[0] + pr[1] < 0 ? "down" : ""}">${plusText(pr)}</b>
    → <b>${after}</b> crediti · ${be}`;
}

// --- scheda dello slot (giocatore gia' scelto) -----------------------------------------
function openSheet(ti, i) {
  const team = state.teams[ti], s = team.slots[i], p = pl(team, i);
  ensureFormation(team);
  // un posto del quintetto ha un ruolo fisso: si scambia solo se i ruoli coincidono (o il posto è vuoto e del ruolo giusto)
  const okIn = (k, who) => k >= 5 || !who || rowRole(team, k) === who.pos;
  const targets = team.slots.map((_, k) => k).filter(k => k !== i && (k === COACH) === (i === COACH)
    && okIn(k, p) && okIn(i, pl(team, k)));
  const draw = () => openModal(`
    <div class="modal-head">
      <div class="sheet-head">${jerseyHtml(p, true)}
        <div><h2>${esc(p.name)}</h2>
          <div class="muted">${esc(p.team)} · ${ROLE_NAME[p.pos]} · ${fmt(p.cr)} cr · ${esc(slotLabel(i))}</div>
          ${p.pos !== "HC" ? `<div class="meta">${gamesText(p)}</div>` : ""}
          ${(p.injury || p.injured) ? `<div class="meta"><span class="warn">⚠ ${esc(p.injury ? p.injury.reason + " – " + p.injury.description : "infortunato")}</span></div>` : ""}
        </div></div>
      ${CLOSE}
    </div>
    <div class="sheet-section">
      <div class="group-title">Punteggio previsto nella giornata</div>
      <div class="score">${modeSeg(s.mode, "data-s", p)}<div class="score-inputs">${scoreInputs(p, s, "data-s")}</div></div>
      <div class="sheet-plus" data-sheet-plus>${sheetPlus(p, s)}</div>
      ${i < 5 ? `<label class="cap"><input type="checkbox" data-s="captain" ${team.captain === i ? "checked" : ""}> Capitano (×1,5)</label>` : ""}
    </div>
    ${i === COACH ? "" : `<div class="sheet-section">
      <div class="group-title">Scambia posizione con</div>
      <p class="legend">Sposta ${esc(p.last_name)} in un altro slot della squadra: chi lo occupa prende il suo posto
        (${esc(slotLabel(i).toLowerCase())}). Utile per decidere chi parte titolare e chi va in panchina.</p>
      ${[["Quintetto · 100%", targets.filter(k => k < 5)], ["Sesto uomo · 100%", targets.filter(k => k === SIXTH)],
         ["Panchina · 50%", targets.filter(k => BENCH.includes(k))]].filter(([, ks]) => ks.length).map(([title, ks]) => `
        <div class="move-group"><span class="small muted">${title}</span>
          <div class="chips">${ks.map(k => {
            const q = pl(team, k);
            return `<button class="chip" data-move="${k}" title="${esc(p.last_name)} va in ${esc(slotLabel(k).toLowerCase())}${q ? ", " + esc(q.last_name) + " va in " + esc(slotLabel(i).toLowerCase()) : ""}">
              ${icon("swap")}${q ? esc(q.last_name) : "slot vuoto"} <span class="muted">${slotShort(k)}</span></button>`;
          }).join("")}</div></div>`).join("")}
    </div>`}
    <div class="sheet-actions">
      <button class="btn primary" data-s="detail">${icon("info")}Scheda completa</button>
      <button class="btn" data-s="change">${icon("swap")}Cambia giocatore</button>
      <button class="btn ghost danger" data-s="remove">${icon("trash")}Rimuovi</button>
    </div>`, body => {
    body.addEventListener("input", e => {
      const k = e.target.dataset.s;
      if (["val", "min", "max"].includes(k)) {
        s[k] = e.target.value; saveState(); updateNumbers(ti); renderResult();
        body.querySelector("[data-sheet-plus]").innerHTML = sheetPlus(p, s);
      }
    });
    body.addEventListener("change", e => {
      if (e.target.dataset.s === "captain") { team.captain = i; e.target.checked = true; refresh(ti); }   // il capitano non si toglie: si sceglie un altro
    });
    body.addEventListener("click", e => {
      const b = e.target.closest("[data-s], [data-move]"); if (!b) return;
      const k = b.dataset.s;
      if (["mode-single", "mode-range", "mode-estimate"].includes(k)) { setMode(s, k.slice(5)); refresh(ti); draw(); }
      else if (k === "change") openPicker(ti, i);
      else if (k === "remove") { team.slots[i] = emptySlot(); if (team.captain === i) team.captain = null; dlg.close(); refresh(ti); }
      else if (k === "detail") openPlayer(p.id);
      else if (k === "est-box") openEstimate(ti, i);
      else if (b.dataset.move != null) {
        const j = +b.dataset.move;
        [team.slots[i], team.slots[j]] = [team.slots[j], team.slots[i]];
        if (team.captain === i) team.captain = j < 5 ? j : null;
        else if (team.captain === j) team.captain = i < 5 ? i : null;
        dlg.close(); refresh(ti);
      }
    });
  });
  draw();
}

// --- squadre salvate / mie squadre -------------------------------------------------
async function saveTeam(ti) {
  const team = state.teams[ti];
  const name = await askDialog({ title: "Salva la squadra", text: "Nome con cui salvarla:", value: team.name, ok: "Salva" });
  if (!name) return;
  team.name = name;
  await fetch("/api/saved-teams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, data: team }) });
  refresh(ti);
}

function pickFromList(title, items, onPick) {
  openModal(`<div class="modal-head"><h2>${esc(title)}</h2>${CLOSE}</div>
    ${items.length ? `<ul class="checks">${items.map((it, k) => `<li class="list-item">
      <button class="btn grow" data-k="${k}">${esc(it.label)}</button></li>`).join("")}</ul>`
      : `<p class="muted">Niente da mostrare.</p>`}`, body => {
    body.addEventListener("click", e => {
      const k = e.target.closest("[data-k]")?.dataset.k;
      if (k != null) { dlg.close(); onPick(items[+k]); }
    });
  });
}

async function loadMine(ti) {
  const mine = await api("/api/my-teams");
  pickFromList("Le mie squadre Dunkest", mine.map(t => ({ ...t, label: `${t.name} · ${fmt(t.credits)} crediti` })), it => {
    const team = newTeam(it.name);
    team.budget = it.credits ?? BUDGET;
    it.players.forEach(rp => {   // court_position Dunkest: 1-5 quintetto, 6 sesto uomo, 7-10 panchina, 11 allenatore
      const k = rp.court_position - 1;
      if (k < 0 || k >= N_SLOTS || !PLAYERS.has(rp.id)) return;
      team.slots[k] = { ...emptySlot(), id: rp.id };
      if (rp.is_captain && k < 5) team.captain = k;
    });
    team.formation = null; state.teams[ti] = team; refresh(ti);
  });
}

// --- squadra automatica (scripts/compila_squadra.py) ----------------------------------------
// Finestra per scegliere allenatore, giocatori da avere e da escludere; poi la rosa con il punteggio atteso
// più alto nella giornata (probabilità di giocare × stima), con quintetto, sesto uomo e capitano; rispetta
// crediti, ruoli, moduli e massimo 3 per squadra NBA (allenatore compreso).
const AUTO = {};   // per squadra: scelte della finestra, ricordate finché la pagina resta aperta

// scelta di un giocatore con ricerca; onPick riceve l'id, back riapre la finestra chiamante
function pickPlayer(title, filter, onPick, back) {
  const roles = ["G", "F", "C", "HC"].filter(r => [...PLAYERS.values()].some(p => p.pos === r && filter(p)));
  openModal(`<div class="modal-head"><h2>${esc(title)}</h2>${CLOSE}</div><div class="plhost"></div>
    <div class="sheet-actions"><button class="btn" data-back>Indietro</button></div>`, body => {
    playerList(body.querySelector(".plhost"), { roles, filter, onPick });
    body.querySelector("[data-back]").onclick = back;
  });
}

// riempie gli slot di una squadra con un risultato del solver (quintetto, sesto uomo, panchina, capitano, allenatore)
function fillFromResult(team, r) {
  [...r.starters, r.sixth, ...r.bench].forEach((id, k) => {
    team.slots[k] = { ...emptySlot(), id, mode: PLAYERS.get(id)?.estimate ? "estimate" : "single" };
  });
  if (team.slots[COACH].id !== r.coach) team.slots[COACH] = { ...emptySlot(), id: r.coach };
  team.captain = r.starters.indexOf(r.captain);
  team.formation = null;
}
const applyAuto = (ti, r) => fillFromResult(state.teams[ti], r);

// scala campo + panchina perché stiano nella finestra senza scorrere (tra 0,35 e 0,8)
function fitAutoCourt(body) {
  const box = body.querySelector(".auto-court"), z = body.querySelector(".auto-zoom");
  if (!box || !z) return;
  z.style.zoom = 1;
  const natural = z.offsetHeight, top = box.getBoundingClientRect().top;
  const room = window.innerHeight - top - 90;          // spazio sotto fino al fondo, con i pulsanti
  z.style.zoom = Math.max(0.35, Math.min(0.8, room / natural));
}

// classifica delle squadre migliori: campo a sinistra, nomi per ruolo a destra
function showAutoResults(ti, out, back) {
  const team = state.teams[ti];
  let k = 0;
  const draw = () => {
    const r = out.teams[k], tmp = newTeam("anteprima");
    tmp.budget = team.budget;
    fillFromResult(tmp, r);
    const names = { G: [], F: [], C: [] };
    [[r.starters, "T"], [[r.sixth], "6°"], [r.bench, "P"]].forEach(([ids, kind]) => ids.forEach(id => {
      const p = PLAYERS.get(id); if (p) names[p.pos].push({ p, kind: id === r.captain ? "T · C" : kind });
    }));
    const coach = PLAYERS.get(r.coach);
    const byPlus = out.objective === "plusvalenza";
    const sgn = v => (v >= 0 ? "+" : "") + fmt(v);
    openModal(`<div class="modal-head"><h2>Squadre migliori · ${byPlus ? "plusvalenza" : "punteggio atteso"}</h2>${CLOSE}</div>
      <div class="rank-tabs">${out.teams.map((t, n) => `<button class="rank-tab${n === k ? " on" : ""}" data-k="${n}"
        title="Squadra ${n + 1}: punteggio atteso ${fmt(t.expected, 0)}, plusvalenza ${sgn(t.plus)} crediti">
        <span class="rk-n">${n + 1}</span><span class="rk-v"><b>${fmt(t.expected, 0)}</b> pt</span>
        <span class="rk-p ${t.plus > 0 ? "up" : t.plus < 0 ? "down" : ""}">${sgn(t.plus)} cr</span></button>`).join("")}</div>
      <div class="auto-res">
        <div class="auto-court"><div class="auto-zoom">${courtHtml(tmp)}${benchHtml(tmp)}</div></div>
        <div class="auto-names">
          <div class="group-title">Allenatore</div>
          <div class="auto-name"><span>${esc(coach?.name ?? "")}</span><span class="muted">${esc(coach?.team ?? "")} · ${fmt(coach?.cr)} cr</span></div>
          ${[["G", "Guardie"], ["F", "Ali"], ["C", "Centri"]].map(([role, title]) => `
            <div class="group-title">${title}</div>
            ${names[role].map(({ p, kind }) => `<div class="auto-name"><span><b class="auto-k">${kind}</b> ${esc(p.name)}</span>
              <span class="muted">${esc(p.team)} · ${fmt(p.cr)} cr${p.estimate ? " · stima " + pts(p.estimate.mean) : ""}${(() => {
                const d = p.estimate ? plusFor(p, p.estimate.mean) : null;   // di quanto sale (o scende) il valore dopo la giornata
                return d == null ? "" : ` · <b class="auto-plus ${d > 0 ? "up" : d < 0 ? "down" : ""}" title="Variazione prevista dei crediti, al punteggio medio stimato">${fmtPlus(d)} cr</b>`;
              })()}</span></div>`).join("")}`).join("")}
          <div class="auto-sum">Punteggio atteso <b>${fmt(r.expected, 0)}</b> · plusvalenza <b>${sgn(r.plus)}</b> cr · crediti usati <b>${fmt(r.credits)}</b>
            <div class="small muted">T titolare · 6° sesto uomo · P panchina (50%) · C capitano (×1,5)</div></div>
        </div>
      </div>
      <div class="sheet-actions"><button class="btn primary" data-use>Usa questa squadra</button>
        <button class="btn" data-back>Indietro</button></div>`, body => {
      fitAutoCourt(body);
      body.addEventListener("click", e => {
        const c = e.target.closest("[data-k]");
        if (c) { k = +c.dataset.k; draw(); return; }
        if (e.target.closest("[data-use]")) { applyAuto(ti, r); dlg.close(); refresh(ti); }
        else if (e.target.closest("[data-back]")) back();
      });
    });
  };
  draw();
}

function autoFill(ti) {
  const team = state.teams[ti];
  const af = AUTO[ti] ??= { include: [], exclude: [], objective: "punteggio", top: 1 };
  if (team.slots[COACH].id) af.coach = team.slots[COACH].id;
  af.error = "";
  const md = MATCHDAYS.find(m => m.id === state.matchday);
  const chips = (key, other) => af[key].map(id => `<span class="chip on">${esc(PLAYERS.get(id)?.name ?? id)}
      <button class="mini" data-rm="${key}:${id}" title="Togli" aria-label="Togli">✕</button></span>`).join(" ") || `<span class="muted small">nessuno</span>`;
  const draw = () => {
    const coach = af.coach ? PLAYERS.get(af.coach) : null;
    openModal(`<div class="modal-head"><h2>Squadra automatica</h2>${CLOSE}</div>
      <p class="legend">Compila la squadra con il punteggio previsto più alto nella giornata ${md ? "G" + md.number : ""}:
        probabilità di giocare × stima, panchina al 50%, capitano ×1,5. Rispetta crediti, ruoli, moduli e massimo
        ${MAX_SAME_TEAM} per squadra NBA (allenatore compreso). I giocatori già in squadra vengono sostituiti.</p>
      <div class="sheet-section"><div class="group-title">1 · Allenatore (obbligatorio)</div>
        ${coach ? `<p><b>${esc(coach.name)}</b> <span class="muted">${esc(coach.team)} · ${fmt(coach.cr)} cr</span>
            · restano <b>${fmt(team.budget - coach.cr)}</b> crediti per i 10 giocatori</p>`
          : `<p class="bad">Scegli prima l'allenatore: la squadra viene compilata con i crediti che restano.</p>`}
        <button class="btn${coach ? "" : " primary"}" data-a="coach">${coach ? "Cambia allenatore" : "Scegli l'allenatore"}</button></div>
      <div class="sheet-section"><div class="group-title">2 · Giocatori da avere in squadra (facoltativo)</div>
        <div class="chips">${chips("include")}</div>
        <button class="btn sm" data-a="include">+ Aggiungi giocatore</button></div>
      <div class="sheet-section"><div class="group-title">3 · Giocatori da non avere (facoltativo)</div>
        <div class="chips">${chips("exclude")}</div>
        <button class="btn sm" data-a="exclude">+ Aggiungi giocatore</button></div>
      <div class="sheet-section"><div class="group-title">4 · Obiettivo</div>
        <div class="chips">
          <button class="chip${af.objective === "punteggio" ? " on" : ""}" data-o="punteggio" title="Squadra con il punteggio atteso più alto nella giornata">Punteggio atteso</button>
          <button class="chip${af.objective === "plusvalenza" ? " on" : ""}" data-o="plusvalenza" title="Squadra che guadagna più crediti (somma delle plusvalenze attese); a parità, il punteggio più alto">Plusvalenza (crediti)</button></div>
        <div class="chips" style="margin-top:8px">
          <button class="chip${af.top === 1 ? " on" : ""}" data-top="1">Solo la migliore</button>
          <button class="chip${af.top === 10 ? " on" : ""}" data-top="10" title="Le 10 migliori, diverse tra loro per almeno 2 giocatori">Le prime 10</button></div></div>
      ${af.error ? `<p class="bad">${esc(af.error)}</p>` : ""}
      <div class="sheet-actions"><button class="btn primary" data-a="run" ${coach ? "" : "disabled"}>${icon("bolt")}Crea la squadra</button>
        <button class="btn" data-close>Annulla</button></div>`, body => {
      body.addEventListener("click", async e => {
        const ob = e.target.closest("[data-o]"), tp = e.target.closest("[data-top]");
        if (ob) { af.objective = ob.dataset.o; draw(); return; }
        if (tp) { af.top = +tp.dataset.top; draw(); return; }
        const rm = e.target.closest("[data-rm]");
        if (rm) { const [k, id] = rm.dataset.rm.split(":"); af[k] = af[k].filter(x => x !== +id); draw(); return; }
        const a = e.target.closest("[data-a]")?.dataset.a;
        if (a === "coach") pickPlayer("Allenatore", p => p.pos === "HC", id => { af.coach = id; draw(); }, draw);
        else if (a === "include" || a === "exclude") {
          const other = a === "include" ? "exclude" : "include";
          pickPlayer(a === "include" ? "Giocatore da avere" : "Giocatore da non avere",
            p => p.pos !== "HC" && !af[a].includes(p.id) && !af[other].includes(p.id),
            id => { af[a].push(id); draw(); }, draw);
        } else if (a === "run") run(body);
      });
    });
  };
  const run = async body => {
    const btn = body.querySelector('[data-a="run"]');
    btn.disabled = true; btn.textContent = af.top > 1 ? "Calcolo delle prime squadre… (circa 10 secondi)" : "Calcolo…";
    const r = await fetch("/api/autofill", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coach_id: af.coach, matchday: state.matchday, budget: team.budget, include: af.include, exclude: af.exclude, objective: af.objective, top: af.top }) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { af.error = out.detail || r.statusText; draw(); return; }
    af.error = "";
    if (out.teams.length === 1) { applyAuto(ti, out.teams[0]); dlg.close(); refresh(ti); }
    else showAutoResults(ti, out, draw);
  };
  draw();
}

// --- box score stimato e modificabile (scripts/stima_partita.py) ---------------------------
// Il box score mostrato è la "partita tipo": tra le 2.000 partite simulate, quella più vicina alla stima
// (statistiche e punteggio). Numeri interi, bonus pieni, titolare e vittoria sì/no.
// Voci con il peso nella formula Dunkest (docs/punteggio.md); le triple contano solo per il bonus triple.
const BOX = [
  { k: "pts", label: "Punti segnati", w: 1 },
  { k: "dreb", label: "Rimbalzi difensivi", w: 1 },
  { k: "oreb", label: "Rimbalzi offensivi", w: 1.25 },
  { k: "ast", label: "Assist", w: 1.5 },
  { k: "stl", label: "Recuperi", w: 1.5 },
  { k: "blk", label: "Stoppate", w: 1.5 },
  { k: "blka", label: "Stoppate subite", w: -0.5 },
  { k: "tov", label: "Palle perse", w: -1.5 },
  { k: "miss_fg", label: "Tiri dal campo sbagliati", w: -1 },
  { k: "miss_ft", label: "Liberi sbagliati", w: -1 },
  { k: "tpm", label: "Triple segnate", w: null },
  { k: "starter", label: "Titolare", w: 1, yesno: ["sì", "no"] },
  { k: "win", label: "Vittoria della squadra", w: null, yesno: ["vince", "perde"] },
];
const repBox = row => Object.fromEntries(BOX.map(r => [r.k, row[`rep_${r.k}`]]));
const fmtSigned = v => (v < 0 ? "−" : "") + fmt(Math.abs(v));
const fmtWeight = w => "×" + (w < 0 ? "−" : "") + Math.abs(w).toLocaleString("it-IT", { maximumFractionDigits: 2 });
const showVal = (r, v) => r.yesno ? r.yesno[v ? 0 : 1] : fmt(v, 0);

// punteggio Dunkest di un box score (stessa formula di scripts/stima_dati.py)
function dunkestScore(v) {
  const linear = v.pts + v.dreb + 1.25 * v.oreb + 1.5 * v.ast + 1.5 * v.stl + 1.5 * v.blk - 0.5 * v.blka
    - 1.5 * v.tov - v.miss_fg - v.miss_ft + v.starter;
  const cats = [["punti", v.pts], ["rimbalzi", v.oreb + v.dreb], ["assist", v.ast], ["recuperi", v.stl], ["stoppate", v.blk]]
    .filter(([, x]) => x >= 10).map(([n]) => n);
  const dbl = cats.length >= 4 ? 50 : cats.length === 3 ? 10 : cats.length === 2 ? 5 : 0;
  const thr = v.tpm >= 5 ? 5 : v.tpm >= 4 ? 4 : v.tpm >= 3 ? 3 : 0;
  const base = linear + dbl + thr, winB = v.win ? 0.05 * Math.abs(base) : 0;
  return { cats, dbl, thr, winB, total: base + winB };
}
const doubleText = s => s.cats.length >= 2
  ? `${["", "", "doppia doppia", "tripla doppia", "quadrupla doppia"][Math.min(s.cats.length, 4)]}: ${s.cats.join(", ")}`
  : `<span class="muted">nessuna</span>`;

const emptyMod = () => ({ minutes: null, base: null, stats: {} });
const modIsEmpty = m => m.minutes == null && !Object.keys(m.stats).length;
// voci cambiate a mano: conta solo il punteggio di questo box score (valore unico)
const isScenario = m => !!m && Object.keys(m.stats).length > 0;

// una partita: stima originale, stima rifatta con altri minuti (mod.base), voci cambiate (mod.stats)
function gameView(orig, mod) {
  const src = mod?.base || orig;
  const v = { ...repBox(src), ...(mod?.stats || {}) }, s = dunkestScore(v), scen = isScenario(mod);
  return scen ? { src, v, s, scen, mean: s.total, q10: s.total, q30: s.total, q70: s.total, q90: s.total }
    : { src, v, s, scen, mean: src.fp_mean, q10: src.fp_q10, q30: src.fp_q30, q70: src.fp_q70, q90: src.fp_q90 };
}

// stima della giornata con le modifiche dello slot (più partite: medie sommate, quantili combinati come normali)
function editedEstimate(s) {
  const ed = s.edit;
  if (!ed || ed.matchday !== state.matchday || !Object.keys(ed.mods).length) return null;
  const gs = ed.orig.map(o => gameView(o, ed.mods[o.match_id]));
  const mean = gs.reduce((t, g) => t + g.mean, 0);
  const q = (k, sign) => gs.some(g => g[k] == null) ? null
    : gs.length === 1 ? gs[0][k] : mean + sign * Math.sqrt(gs.reduce((t, g) => t + (g[k] - g.mean) ** 2, 0));
  return { mean, q10: q("q10", -1), q30: q("q30", -1), q70: q("q70", 1), q90: q("q90", 1), n_games: gs.length, edited: true };
}

async function openEstimate(ti, i) {
  const team = state.teams[ti], s = team.slots[i], p = PLAYERS.get(s.id);
  let orig = s.edit?.matchday === state.matchday ? s.edit.orig : null;
  if (!orig) {
    const d = await fetchForModal(`/api/estimate/${s.id}?matchday=${state.matchday}`);
    if (!d) return;
    orig = d.games;
  }
  if (!orig.length) { openModal(`<div class="modal-head"><h2>${esc(p.name)}</h2>${CLOSE}</div><p class="muted">Nessuna stima per questa giornata.</p>`); return; }
  const full = orig[0].rep_pts != null;   // stime calcolate prima della "partita tipo": solo vista ridotta
  let g = 0, editing = false, busy = false, error = "", notice = "";
  const mods = () => s.edit?.matchday === state.matchday ? s.edit.mods : {};
  const getMod = mid => {
    if (s.edit?.matchday !== state.matchday) s.edit = { matchday: state.matchday, orig, mods: {} };
    return s.edit.mods[mid] ??= emptyMod();
  };
  const commit = () => {
    if (s.edit) {
      for (const [k, m] of Object.entries(s.edit.mods)) if (modIsEmpty(m)) delete s.edit.mods[k];
      if (!Object.keys(s.edit.mods).length) delete s.edit;
    }
    refresh(ti);
  };

  const rowsHtml = (o, m, view) => {
    const show = editing || !!m, ov = gameView(o, null);
    const line = (label, w, origTxt, modTxt, ptsVal, cls = "") => `<tr class="${cls}"><td>${label}</td><td class="w">${w}</td>
      <td>${origTxt}</td>${show ? `<td class="mod">${modTxt}</td>` : ""}<td class="pt">${ptsVal == null ? "" : fmtSigned(ptsVal)}</td></tr>`;
    const changed = k => m?.stats[k] != null;
    const reset = k => changed(k) ? ` <button class="mini" data-reset="${k}" title="Torna al valore della stima">↺</button>` : "";
    const input = r => r.yesno
      ? `<select data-k="${r.k}" class="${changed(r.k) ? "changed" : ""}">${[1, 0].map(x => `<option value="${x}" ${view.v[r.k] === x ? "selected" : ""}>${r.yesno[x ? 0 : 1]}</option>`).join("")}</select>${reset(r.k)}`
      : `<input type="text" inputmode="numeric" class="box-in${changed(r.k) ? " changed" : ""}" data-k="${r.k}" value="${fmt(view.v[r.k], 0)}" aria-label="${r.label}">${reset(r.k)}`;
    const modVal = r => editing ? input(r) : `<span class="${changed(r.k) ? "changed-v" : ""}">${showVal(r, view.v[r.k])}</span>`;

    const minCell = editing
      ? `<div class="min-edit"><input type="text" inputmode="numeric" class="box-in${m?.minutes != null ? " changed" : ""}" data-min value="${fmt(m?.minutes ?? o.rep_minutes, 0)}" aria-label="Minuti">
         <button class="btn sm recalc" data-recalc title="Rifà tutta la stima della partita con questi minuti">↻ Ricalcola stima</button>
         ${m?.minutes != null ? `<button class="mini" data-reset="minutes" title="Torna ai minuti della stima">↺</button>` : ""}</div>`
      : m?.minutes != null ? `<span class="changed-v">${fmt(m.minutes, 0)}</span>` : fmt(view.src.rep_minutes, 0);
    let html = line(editing ? `Minuti <div class="small muted">cambiali e premi Ricalcola: si rifà tutta la stima</div>` : "Minuti",
      "", fmt(o.rep_minutes, 0), minCell, null, "minutes");
    html += BOX.map(r => line(r.label, r.w == null ? (r.k === "tpm" ? "bonus" : "") : fmtWeight(r.w), showVal(r, ov.v[r.k]), modVal(r),
      r.w == null ? null : r.w * view.v[r.k])).join("");
    const cur = view.s;
    html += line("Bonus doppie", "+5 / +10 / +50", doubleText(ov.s), doubleText(cur), cur.dbl, "bonus");
    html += line("Bonus triple <span class=\"muted small\">(3, 4, 5+ triple)</span>", "+3 / +4 / +5", `${fmt(ov.v.tpm, 0)} triple`, `${fmt(view.v.tpm, 0)} triple`, cur.thr, "bonus");
    html += line("Bonus vittoria <span class=\"muted small\">(del punteggio)</span>", "+5%", ov.v.win ? "vince" : "perde", view.v.win ? "vince" : "perde", cur.winB, "bonus");
    return html;
  };

  const footHtml = (o, m, view) => {
    const show = editing || !!m, ov = gameView(o, null);
    const ob = band({ q10: o.fp_q10, q30: o.fp_q30, q70: o.fp_q70, q90: o.fp_q90 });
    const vb = band({ q10: view.q10, q30: view.q30, q70: view.q70, q90: view.q90 });
    const est = (mean, b) => `${pts(mean)} <span class="muted small">(${pts(b.lo)}–${pts(b.hi)})</span>`;
    const nowEst = view.scen ? `<span class="muted small">= box score</span>` : est(view.mean, vb);
    return show
      ? `<tr><td>Punteggio Dunkest di questa partita</td><td></td><td class="pt">${pts(ov.s.total)}</td><td class="mod"></td><td class="pt">${pts(view.s.total)}</td></tr>
         <tr class="rng"><td>Stima del giocatore <span class="muted small">media di 2.000 partite (intervallo ${ob.label})</span></td><td></td><td class="pt">${est(o.fp_mean, ob)}</td><td class="mod"></td><td class="pt">${nowEst}</td></tr>`
      : `<tr><td>Punteggio Dunkest di questa partita</td><td></td><td></td><td class="pt">${pts(view.s.total)}</td></tr>
         <tr class="rng"><td>Stima del giocatore <span class="muted small">media di 2.000 partite (intervallo ${ob.label})</span></td><td></td><td></td><td class="pt">${est(o.fp_mean, ob)}</td></tr>`;
  };

  const draw = () => {
    const o = orig[g], m = full ? mods()[o.match_id] : null, show = editing || !!m;
    const date = new Date(o.game_date + "T12:00").toLocaleDateString("it-IT", { weekday: "short", day: "numeric", month: "short" });
    const tabs = orig.length > 1 ? `<div class="chips game-tabs">${orig.map((x, k) => `<button class="chip${k === g ? " on" : ""}" data-game="${k}">
        ${x.home ? "vs" : "@"} ${esc(x.opp)}${mods()[x.match_id] ? " ✎" : ""}</button>`).join("")}</div>` : "";
    const table = full
      ? (() => {
        const view = gameView(o, m);
        return `<div class="table-wrap box-wrap"><table class="games box">
          <thead><tr><th>Voce</th><th class="w">Peso</th><th>${show ? "Partita tipo" : "Valore"}</th>${show ? `<th class="mod">Modificato</th>` : ""}<th>Punti</th></tr></thead>
          <tbody>${rowsHtml(o, m, view)}</tbody><tfoot>${footHtml(o, m, view)}</tfoot></table></div>`;
      })()
      : `<p><b>Per vedere il box score della stima e modificarlo rilancia aggiorna_stime.bat</b>: queste stime sono state
          calcolate prima. Stima attuale: <b>${pts(o.fp_mean)}</b> punti.</p>`;
    openModal(`
      <div class="modal-head">
        <div class="sheet-head">${jerseyHtml(p, true)}
          <div><h2>${esc(p.name)}</h2>
            <div class="muted">Box score stimato · ${o.home ? "vs" : "@"} ${esc(o.opp)} · ${date} · ${esc(team.name)}</div>
            ${orig.length > 1 ? (() => {   // il punteggio della giornata è la somma delle partite
              const per = orig.map(x => ({ x, v: full ? gameView(x, mods()[x.match_id]).mean : x.fp_mean }));
              return `<div class="meta">${orig.length} partite in giornata, ognuna con il suo box score:
                stima della giornata <b>${pts(per.reduce((t, r) => t + r.v, 0))}</b> punti =
                ${per.map(r => `${pts(r.v)} (${r.x.home ? "vs" : "@"} ${esc(r.x.opp)})`).join(" + ")}</div>`;
            })() : ""}
          </div></div>
        ${CLOSE}
      </div>
      ${tabs}
      ${full ? `<div class="box-actions">
        <button class="btn${editing ? " primary" : ""}" data-edit>${editing ? "Fatto" : "Modifica"}</button>
        ${m ? `<button class="btn ghost danger" data-revert>Torna alla stima originale</button>` : ""}
        ${busy ? `<span class="small muted">ricalcolo della stima…</span>` : ""}
        ${notice && !busy ? `<span class="small notice">${notice}</span>` : ""}
        ${error ? `<span class="small bad">${esc(error)}</span>` : ""}
      </div>` : ""}
      ${table}
      ${full ? `<p class="legend"><b>Partita tipo</b>: tra le 2.000 partite simulate (se il giocatore gioca), quella più vicina
        alla stima nelle statistiche e nel punteggio; la stima vera è la media, in fondo. Con <b>Modifica</b>: cambiando i
        <b>minuti</b> e premendo <b>Ricalcola</b> la stima della partita viene rifatta dal modello (le voci scritte da te restano);
        cambiando le <b>altre voci</b> si ricalcola solo il punteggio Dunkest di questo box score, con i bonus che scattano sui
        valori scritti. Vale solo per questo giocatore in questa squadra e si perde se lo togli o lo cambi.</p>` : ""}`, bind);
  };

  // aggiorna punti, bonus e totali senza ridisegnare (non perde il focus mentre scrivi)
  const update = body => {
    const o = orig[g], m = mods()[o.match_id], view = gameView(o, m);
    const fresh = document.createElement("tbody");
    fresh.innerHTML = rowsHtml(o, m, view);
    const src = fresh.querySelectorAll("tr");
    body.querySelector("tbody").querySelectorAll("tr").forEach((tr, k) => {
      tr.querySelector(".pt").textContent = src[k].querySelector(".pt").textContent;
      if (tr.classList.contains("bonus")) tr.querySelector(".mod").innerHTML = src[k].querySelector(".mod").innerHTML;
    });
    body.querySelector("tfoot").innerHTML = footHtml(o, m, view);
    const tab = body.querySelector(`[data-game="${g}"]`);
    if (tab) tab.textContent = `${o.home ? "vs" : "@"} ${o.opp}${m ? " ✎" : ""}`;
    refresh(ti);
  };

  const resim = async minutes => {
    const o = orig[g], m = getMod(o.match_id);
    m.minutes = minutes; error = "";
    if (minutes == null) { m.base = null; notice = "Tornato ai minuti e alla stima originali"; commit(); draw(); return; }
    busy = true; draw();
    try {
      const r = await fetch(`/api/estimate/${s.id}/resimulate`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ matchday: state.matchday, match_id: o.match_id, minutes }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      m.base = j;
      notice = `Stima rifatta con ${fmt(minutes, 0)} minuti: ricalcolate tutte le voci non scritte da te`;
    } catch (err) { error = err.message; m.minutes = null; m.base = null; }
    busy = false; commit(); draw();
  };

  const applyMinutes = body => {
    const o = orig[g], v = num(body.querySelector("[data-min]").value);
    if (v == null) { resim(null); return; }
    const mins = Math.round(v);
    if (mins < 1 || mins > 48) { error = "I minuti devono essere tra 1 e 48"; draw(); return; }
    resim(mins === Math.round(o.rep_minutes) && mods()[o.match_id]?.minutes == null ? null : mins);
  };

  // valore scritto: intero; se torna uguale a quello della stima la modifica sparisce
  const setStat = (k, raw) => {
    const o = orig[g], m = getMod(o.match_id), base = repBox(m.base || o)[k];
    const v = num(raw);
    if (v == null || v < 0 || Math.round(v) === base) delete m.stats[k]; else m.stats[k] = Math.round(v);
  };

  function bind(body) {
    body.addEventListener("click", e => {
      const t = e.target.closest("[data-edit], [data-revert], [data-game], [data-reset], [data-recalc]");
      if (!t) return;
      const o = orig[g];
      if (t.hasAttribute("data-recalc")) applyMinutes(body);
      else if (t.hasAttribute("data-edit")) { editing = !editing; notice = ""; draw(); }
      else if (t.hasAttribute("data-revert")) { if (s.edit) delete s.edit.mods[o.match_id]; error = ""; notice = "Tornato alla stima originale"; commit(); draw(); }
      else if (t.dataset.game != null) { g = +t.dataset.game; error = ""; notice = ""; draw(); }
      else if (t.dataset.reset === "minutes") resim(null);
      else if (t.dataset.reset) { delete getMod(o.match_id).stats[t.dataset.reset]; commit(); draw(); }
    });
    body.addEventListener("input", e => {
      const t = e.target;
      if (t.hasAttribute("data-min")) {   // minuti scritti ma non applicati: Ricalcola si evidenzia
        const o = orig[g], cur = mods()[o.match_id]?.minutes ?? o.rep_minutes, v = num(t.value);
        body.querySelector("[data-recalc]").classList.toggle("pending", v != null && Math.round(v) !== Math.round(cur));
        return;
      }
      if (!t.dataset.k || t.tagName === "SELECT") return;
      setStat(t.dataset.k, t.value);
      t.classList.toggle("changed", mods()[orig[g].match_id]?.stats[t.dataset.k] != null);
      update(body);
    });
    body.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.hasAttribute("data-min")) applyMinutes(body); });
    body.addEventListener("change", e => {
      const t = e.target;
      if (t.tagName === "SELECT" && t.dataset.k) { setStat(t.dataset.k, t.value); commit(); draw(); }
      else if (t.dataset.k) {                 // casella lasciata: mostra il valore intero e il pulsante per tornare indietro
        const m = mods()[orig[g].match_id], v = m?.stats[t.dataset.k];
        t.value = fmt(v ?? repBox(m?.base || orig[g])[t.dataset.k], 0);
        const has = t.parentElement.querySelector(`[data-reset="${t.dataset.k}"]`);
        if (v != null && !has) t.insertAdjacentHTML("afterend", ` <button class="mini" data-reset="${t.dataset.k}" title="Torna al valore della stima">↺</button>`);
        else if (v == null && has) has.remove();
        commit();
      }
    });
  }
  draw();
}

// --- scheda giocatore completa ------------------------------------------------------
async function openPlayer(id) {
  const p = PLAYERS.get(id);
  const d = await fetchForModal(`/api/players/${id}`);
  if (!d) return;
  const games = d.games, last = games.slice(0, 20).reverse();
  const season = games.length ? games[0].season : null;
  const inSeason = games.filter(g => g.season === season);
  const avg = arr => arr.length ? arr.reduce((t, x) => t + x, 0) / arr.length : null;
  const seasonAvg = avg(inSeason.map(g => g.pdk));
  const stats = [
    ["Crediti", fmt(p?.cr)], ["Partite giornata", p ? p.games.length : "–"],
    [`Media ${season || ""}`, fmt(seasonAvg)], ["Ultime 5", fmt(avg(games.slice(0, 5).map(g => g.pdk)))],
    ["Ultime 10", fmt(avg(games.slice(0, 10).map(g => g.pdk)))], ["Minuti", fmt(avg(inSeason.map(g => g.min)))],
  ];
  openModal(`
    <div class="modal-head">
      <div class="sheet-head">${p ? jerseyHtml(p, true) : ""}
        <div><h2>${esc(p?.name || d.player.first_name + " " + d.player.last_name)}</h2>
        <div class="muted">${esc(p?.team || "")} · ${d.player.pos} ${p && p.pos !== "HC" ? "· " + gamesText(p) : ""}</div></div></div>
      ${CLOSE}
    </div>
    <div class="stat-row">${stats.map(([l, v]) => `<div class="stat"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>
    ${p?.injury ? `<p class="meta"><span class="warn">⚠ ${esc(p.injury.reason)} – ${esc(p.injury.description)} (${esc(p.injury.injury_date)})</span></p>` : ""}
    ${last.length ? `<div class="chart-title">PDK nelle ultime ${last.length} partite</div><div class="chart">${barChart(last, seasonAvg)}</div>`
      : `<p class="muted">Nessuna partita nello storico statistiche (rookie, allenatore, o abbinamento non ancora fatto).</p>`}
    ${d.prices.length > 1 ? `<div class="chart-title">Crediti per giornata: ${d.prices.map(x => `G${x.matchday} ${fmt(x.cr)}`).join(" · ")}</div>` : ""}
    ${games.length ? `<div class="table-wrap"><table class="games">
      <thead><tr><th>Data</th><th>Avv.</th><th>PDK</th><th>Min</th><th>Pt</th><th>Rb</th><th>As</th><th>Rec</th><th>Stp</th><th>PP</th></tr></thead>
      <tbody>${games.slice(0, 40).map(g => `<tr><td>${esc(g.game_date)}</td><td>${g.opponent_side === "home" ? "@" : ""}${esc(g.opp)}</td>
        <td><b>${fmt(g.pdk)}</b></td><td>${fmt(g.min, 0)}</td><td>${g.pts}</td><td>${g.reb}</td><td>${g.ast}</td><td>${g.stl}</td><td>${g.blk}</td><td>${g.tov}</td></tr>`).join("")}
      </tbody></table></div>` : ""}`, body => bindChartTooltip(body, last));
}

// barre verticali ancorate alla linea dello zero, angoli arrotondati sul lato del dato
function barChart(games, avgValue) {
  const W = 640, H = 170, padL = 30, padR = 8, padT = 10, padB = 22;
  const vals = games.map(g => g.pdk);
  const hi = Math.max(10, ...vals), lo = Math.min(0, ...vals);
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const step = (W - padL - padR) / games.length, bw = Math.max(4, step - 2), r = Math.min(4, bw / 2);
  const bars = games.map((g, k) => {
    const x = padL + k * step + (step - bw) / 2, y0 = y(0), y1 = y(g.pdk);
    const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0)), rr = Math.min(r, h);
    const d = g.pdk >= 0
      ? `M${x},${y0} V${top + rr} Q${x},${top} ${x + rr},${top} H${x + bw - rr} Q${x + bw},${top} ${x + bw},${top + rr} V${y0} Z`
      : `M${x},${y0} V${y0 + h - rr} Q${x},${y0 + h} ${x + rr},${y0 + h} H${x + bw - rr} Q${x + bw},${y0 + h} ${x + bw},${y0 + h - rr} V${y0} Z`;
    return `<path class="bar" d="${d}"/><rect x="${padL + k * step}" y="${padT}" width="${step}" height="${H - padT - padB}" fill="transparent" data-k="${k}"/>`;
  }).join("");
  const ticks = [lo, (lo + hi) / 2, hi].map(v => Math.round(v));
  const avgLine = avgValue != null
    ? `<line class="avg" x1="${padL}" x2="${W - padR}" y1="${y(avgValue)}" y2="${y(avgValue)}"/>
       <text x="${W - padR}" y="${y(avgValue) - 4}" text-anchor="end">media ${fmt(avgValue)}</text>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="PDK per partita">
    ${ticks.map(v => `<text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("")}
    <line class="axis" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>
    ${bars}${avgLine}
    <text x="${padL}" y="${H - 4}">${esc(games[0].game_date)}</text>
    <text x="${W - padR}" y="${H - 4}" text-anchor="end">${esc(games[games.length - 1].game_date)}</text>
  </svg>`;
}

function bindChartTooltip(root, games) {
  const tip = $("#tooltip");
  root.querySelectorAll(".chart rect[data-k]").forEach(el => {
    el.addEventListener("mousemove", e => {
      const g = games[+el.dataset.k];
      tip.innerHTML = `<b>${fmt(g.pdk)} PDK</b><br>${esc(g.game_date)} ${g.opponent_side === "home" ? "@" : "vs "}${esc(g.opp)}<br>${fmt(g.min, 0)} min · ${g.pts} pt · ${g.reb} rb · ${g.ast} as`;
      tip.hidden = false;
      if (!tip.matches(":popover-open")) tip.showPopover();
      tip.style.left = Math.min(e.clientX + 12, innerWidth - 200) + "px";
      tip.style.top = (e.clientY + 12) + "px";
    });
    el.addEventListener("mouseleave", () => { tip.hidden = true; if (tip.matches(":popover-open")) tip.hidePopover(); });
  });
}

state = loadState() || { matchday: null, tab: 0, teams: [newTeam("Squadra A"), newTeam("Squadra B")] };
init().catch(err => showBanner(`Errore: ${esc(err.message)}`));
