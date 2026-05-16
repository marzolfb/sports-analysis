import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import AggregatedPick, EdgeFlag, MarketType, Pick, PickSide, Sport

DB_PATH = Path(__file__).parent / "data" / "picks.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection):
    """Apply schema migrations that CREATE TABLE IF NOT EXISTS can't handle."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(games)").fetchall()}
    if "league" not in existing:
        conn.execute("ALTER TABLE games ADD COLUMN league TEXT")


def init_db():
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            id          TEXT PRIMARY KEY,
            sport       TEXT NOT NULL,
            home_team   TEXT NOT NULL,
            away_team   TEXT NOT NULL,
            game_time   TEXT NOT NULL,
            league      TEXT,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS picks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id      TEXT NOT NULL REFERENCES games(id),
            source       TEXT NOT NULL,
            market_type  TEXT NOT NULL,
            pick_side    TEXT NOT NULL,
            pick_team    TEXT,
            line         REAL,
            implied_prob REAL,
            confidence   REAL,
            raw_odds     TEXT,
            notes        TEXT,
            fetched_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            game_id     TEXT PRIMARY KEY REFERENCES games(id),
            home_score  INTEGER,
            away_score  INTEGER,
            winner      TEXT,
            settled_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS aggregated_picks (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id               TEXT NOT NULL REFERENCES games(id),
            market_type           TEXT NOT NULL,
            pick_side             TEXT NOT NULL,
            pick_team             TEXT,
            composite_score       REAL,
            source_count          INTEGER,
            sources_agreeing      TEXT,
            sources_disagreeing   TEXT,
            edge_flag             TEXT,
            edge_zone_hit         INTEGER,
            kalshi_notes          TEXT,
            recommended_stake     INTEGER,
            created_at            TEXT NOT NULL
        );
        """)
        _migrate(conn)


def _game_id(sport: str, home: str, away: str, game_time: datetime,
             league: Optional[str] = None) -> str:
    date = game_time.strftime("%Y%m%d")
    home_slug = home.replace(" ", "").upper()[:6]
    away_slug = away.replace(" ", "").upper()[:6]
    league_slug = league.replace(" ", "").upper()[:6] if league else ""
    prefix = f"{league_slug}_" if league_slug else ""
    return f"{prefix}{sport}_{away_slug}_{home_slug}_{date}"


def upsert_pick(pick: Pick) -> str:
    game_id = _game_id(pick.sport, pick.home_team, pick.away_team, pick.game_time,
                       getattr(pick, "league", None))
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO games (id, sport, home_team, away_team, game_time, league, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (game_id, pick.sport, pick.home_team, pick.away_team,
              pick.game_time.isoformat(), getattr(pick, "league", None), now))
        conn.execute("""
            INSERT INTO picks
              (game_id, source, market_type, pick_side, pick_team, line,
               implied_prob, confidence, raw_odds, notes, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (game_id, pick.source, pick.market_type, pick.pick_side,
              pick.pick_team, pick.line, pick.implied_prob, pick.confidence,
              pick.raw_odds, pick.notes,
              (pick.fetched_at or datetime.utcnow()).isoformat()))
    return game_id


def save_aggregated(agg: AggregatedPick):
    game_id = _game_id(agg.sport, agg.home_team, agg.away_team, agg.game_time,
                       getattr(agg, "league", None))
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT INTO aggregated_picks
              (game_id, market_type, pick_side, pick_team, composite_score,
               source_count, sources_agreeing, sources_disagreeing,
               edge_flag, edge_zone_hit, kalshi_notes, recommended_stake, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (game_id, agg.market_type, agg.pick_side, agg.pick_team,
              agg.composite_score, agg.source_count,
              json.dumps(agg.sources_agreeing), json.dumps(agg.sources_disagreeing),
              agg.edge_flag, int(agg.edge_zone_hit),
              json.dumps(agg.kalshi_notes), agg.recommended_stake_level, now))


def load_picks_for_date(date: str) -> list[dict]:
    """date: YYYY-MM-DD"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT p.*, g.sport, g.home_team, g.away_team, g.game_time
            FROM picks p JOIN games g ON p.game_id = g.id
            WHERE g.game_time LIKE ?
            ORDER BY g.game_time
        """, (f"{date}%",)).fetchall()
    return [dict(r) for r in rows]


def load_aggregated_for_date(date: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT a.*, g.sport, g.home_team, g.away_team, g.game_time
            FROM aggregated_picks a JOIN games g ON a.game_id = g.id
            WHERE g.game_time LIKE ?
            ORDER BY a.composite_score DESC
        """, (f"{date}%",)).fetchall()
    return [dict(r) for r in rows]


def log_outcome(sport: str, home: str, away: str, game_time: datetime,
                home_score: int, away_score: int):
    game_id = _game_id(sport, home, away, game_time)
    if home_score > away_score:
        winner = "HOME"
    elif away_score > home_score:
        winner = "AWAY"
    else:
        winner = "TIE"
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO outcomes (game_id, home_score, away_score, winner, settled_at)
            VALUES (?, ?, ?, ?, ?)
        """, (game_id, home_score, away_score, winner, datetime.utcnow().isoformat()))


def accuracy_by_source() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                p.source,
                p.market_type,
                COUNT(*) AS total,
                SUM(CASE
                    WHEN (p.pick_side IN ('HOME','AWAY') AND p.pick_side = o.winner) THEN 1
                    WHEN (p.pick_side = 'OVER' AND o.home_score + o.away_score > p.line) THEN 1
                    WHEN (p.pick_side = 'UNDER' AND o.home_score + o.away_score < p.line) THEN 1
                    ELSE 0 END) AS wins
            FROM picks p
            JOIN games g ON p.game_id = g.id
            JOIN outcomes o ON g.id = o.game_id
            GROUP BY p.source, p.market_type
            ORDER BY p.source, p.market_type
        """).fetchall()
    return [dict(r) for r in rows]
