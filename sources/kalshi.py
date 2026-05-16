"""
Kalshi source — soccer only.

Fetches open prediction market prices for upcoming soccer games.
Kalshi prices represent real-money crowd consensus — one of the highest-signal
inputs available since they reflect calibrated belief with financial stakes.

Auth: Kalshi uses RSA-signed requests (PKCS1v15 + SHA256).
  KALSHI_API_KEY_ID  — key ID (UUID string from Kalshi dashboard)
  KALSHI_PRIVATE_KEY_PATH — path to PEM file containing the RSA private key

Signal logic:
  - Each game has three markets: home win, tie, away win.
  - yes_bid_dollars (0.0–1.0) is the implied probability for that outcome.
  - We pick the team (home or away) with the highest implied probability,
    ignoring the TIE market.

API notes (as of 2026):
  - Query by series_ticker (e.g. KXEPLGAME) rather than timestamp filters.
  - Filter by occurrence_datetime for the target date (±1 day for UTC offset).
  - Price fields use _dollars suffix and are already 0.0–1.0 scale.
  - Volume is in volume_fp (float string), status is "active" not "open".

Cache: daily per-league to avoid repeated API calls on the same day.
"""

import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import cache
from models import MarketType, Pick, PickSide, Sport
from sources.base import BaseSource

KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2"

# Minimum trading volume before we treat a market's price as signal
MIN_VOLUME = 50

# Kalshi series tickers for each league's game (moneyline) markets
LEAGUE_SERIES: dict[str, str] = {
    "EPL":        "KXEPLGAME",
    "La Liga":    "KXLALIGAGAME",
    "Bundesliga": "KXBUNDESLIGAGAME",
    "Serie A":    "KXSERIEAGAME",
    "Ligue 1":    "KXLIGUE1GAME",
    "MLS":        "KXMLSGAME",
    "Liga MX":    "KXLIGAMXGAME",
}

# Patterns to extract home/away teams from market titles.
# Kalshi uses various formats — try them in order.
_TITLE_PATTERNS = [
    # "Arsenal vs Chelsea: Arsenal to win"
    re.compile(r"^(.+?)\s+vs\.?\s+(.+?)[\s:]", re.IGNORECASE),
    # "Will Arsenal beat Chelsea?"
    re.compile(r"Will (.+?) beat (.+?)\??$", re.IGNORECASE),
    # "Arsenal to win vs Chelsea"
    re.compile(r"^(.+?) to win vs\.?\s+(.+?)$", re.IGNORECASE),
    # "Arsenal - Chelsea, Arsenal win"
    re.compile(r"^(.+?)\s+[-–]\s+(.+?),", re.IGNORECASE),
]


class KalshiSource(BaseSource):
    name = "kalshi"

    def __init__(self):
        self._key_id = os.getenv("KALSHI_API_KEY_ID")
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        self._private_key = None
        if key_path:
            try:
                pem = Path(key_path).read_bytes()
                self._private_key = serialization.load_pem_private_key(pem, password=None)
            except Exception as e:
                print(f"[kalshi] failed to load private key from {key_path}: {e}")

    def _auth_headers(self, method: str, path: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}".encode()
        signature = self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return {
            "KALSHI-ACCESS-KEY":       self._key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = KALSHI_API_BASE + path
        headers = self._auth_headers("GET", path)
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fetch_picks(self, sport: Sport, date: datetime,
                    league: Optional[str] = None) -> list[Pick]:
        if sport != Sport.SOCCER:
            return []
        if not self._key_id or not self._private_key:
            print("[kalshi] KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set — skipping")
            return []

        from config import ACTIVE_SOCCER_LEAGUES
        leagues_to_fetch = (
            {league: ACTIVE_SOCCER_LEAGUES[league]}
            if league and league in ACTIVE_SOCCER_LEAGUES
            else ACTIVE_SOCCER_LEAGUES
        )

        all_picks: list[Pick] = []
        for league_name in leagues_to_fetch:
            picks = self._fetch_league(league_name, date)
            all_picks.extend(picks)

        return all_picks

    def _fetch_league(self, league_name: str, date: datetime) -> list[Pick]:
        date_str = date.strftime("%Y-%m-%d")
        cache_key = f"SOCCER_{league_name.replace(' ', '_')}"

        cached = cache.get(self.name, cache_key, date)
        if cached:
            print(f"[kalshi] using cached data for {league_name} {date_str}")
            return self._parse_cached(cached, league_name, date)

        markets = self._fetch_markets(league_name, date)
        if not markets:
            print(f"[kalshi] no markets found for {league_name} {date_str}")
            cache.set(self.name, cache_key, date, "[]")
            return []

        cache.set(self.name, cache_key, date, json.dumps(markets))
        picks = self._build_picks(markets, league_name, date)
        print(f"[kalshi] {len(picks)} picks for {league_name}")
        return picks

    def _fetch_markets(self, league_name: str, date: datetime) -> list[dict]:
        series_ticker = LEAGUE_SERIES.get(league_name)
        if not series_ticker:
            return []

        try:
            data = self._get("/markets", params={"series_ticker": series_ticker, "limit": 200})
            all_markets = data.get("markets", [])
        except Exception as e:
            print(f"[kalshi] API error for {league_name}: {e}")
            return []

        # Filter to markets whose game falls on the target date (±1 day for UTC offset).
        # occurrence_datetime is the scheduled game kick-off in UTC.
        target_dates = {
            (date + timedelta(d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)
        }
        matched = []
        for m in all_markets:
            occ = m.get("occurrence_datetime") or m.get("expected_expiration_time") or ""
            if any(d in occ for d in target_dates):
                matched.append(m)

        return matched

    def _parse_cached(self, cached_str: str, league_name: str,
                      date: datetime) -> list[Pick]:
        try:
            markets = json.loads(cached_str)
        except Exception:
            return []
        return self._build_picks(markets, league_name, date)

    def _build_picks(self, markets: list[dict], league_name: str,
                     date: datetime) -> list[Pick]:
        picks = []
        for m in markets:
            pick = self._market_to_pick(m, league_name, date)
            if pick:
                picks.append(pick)
        return picks

    def _market_to_pick(self, market: dict, league_name: str,
                        date: datetime) -> Pick | None:
        try:
            ticker = market.get("ticker", "")
            # Skip tie markets — we only pick team outcomes
            if ticker.endswith("-TIE"):
                return None

            title = market.get("title", "")
            volume = float(market.get("volume_fp") or 0)
            if volume < MIN_VOLUME:
                return None

            # Prices are in dollars (0.0–1.0 scale) as of 2026 API
            yes_price = (
                market.get("last_price_dollars")
                or market.get("yes_bid_dollars")
                or market.get("yes_ask_dollars")
            )
            if yes_price is None:
                return None
            implied_prob = float(yes_price)

            # Parse actual kickoff time from occurrence_datetime
            game_time = date
            occ = market.get("occurrence_datetime") or market.get("expected_expiration_time")
            if occ:
                try:
                    game_time = datetime.fromisoformat(
                        occ.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    pass

            # yes_sub_title identifies which team this market resolves for
            pick_team = market.get("yes_sub_title")
            home_team, away_team = self._parse_teams(title)
            if not home_team or not away_team:
                print(f"[kalshi] could not parse teams from: {title!r}")
                return None

            if not pick_team:
                pick_team = home_team if implied_prob >= 0.5 else away_team

            side = PickSide.HOME if pick_team == home_team else PickSide.AWAY

            price_pct = int(implied_prob * 100)
            notes = (
                f"Kalshi: {league_name} | {pick_team} win {price_pct}¢ | vol {int(volume):,}"
            )

            return Pick(
                source=self.name,
                sport=Sport.SOCCER,
                home_team=home_team,
                away_team=away_team,
                game_time=game_time,
                market_type=MarketType.GAME,
                pick_side=side,
                pick_team=pick_team,
                implied_prob=implied_prob,
                raw_odds=str(price_pct),
                notes=notes,
                fetched_at=datetime.utcnow(),
                league=league_name,
            )
        except Exception as e:
            print(f"[kalshi] skipping market: {e}")
            return None

    def _parse_teams(self, title: str) -> tuple[str | None, str | None]:
        for pattern in _TITLE_PATTERNS:
            m = pattern.search(title)
            if m:
                return m.group(1).strip(), m.group(2).strip()
        return None, None
