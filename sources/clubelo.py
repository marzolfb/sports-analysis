"""
Club ELO helper — NOT a BaseSource.

Fetches Club ELO ratings from api.clubelo.com and exposes match win probability.
Only covers European top-flight leagues; MLS and Liga MX have no data.

Usage:
    elo = ClubElo.for_date(date)
    prob = elo.match_probability("Arsenal", "Chelsea")  # → 0.52 (home win prob)
"""

import csv
import io
from datetime import datetime
from functools import lru_cache

import requests

_API_URL = "http://api.clubelo.com/{date}"

# Home advantage in ELO points (standard Club ELO adjustment)
_HOME_ADVANTAGE = 100


class ClubElo:
    def __init__(self, ratings: dict[str, float]):
        # normalise club names to lowercase for fuzzy lookup
        self._ratings = {k.lower(): v for k, v in ratings.items()}

    @classmethod
    def for_date(cls, date: datetime) -> "ClubElo":
        return _fetch(date.strftime("%Y-%m-%d"))

    def get(self, team: str) -> float | None:
        key = team.strip().lower()
        if key in self._ratings:
            return self._ratings[key]
        # partial match fallback
        for stored_key, rating in self._ratings.items():
            if key in stored_key or stored_key in key:
                return rating
        return None

    def match_probability(self, home: str, away: str) -> float | None:
        """Return home-win probability (0–1) using ELO difference formula."""
        home_elo = self.get(home)
        away_elo = self.get(away)
        if home_elo is None or away_elo is None:
            return None
        diff = (home_elo + _HOME_ADVANTAGE) - away_elo
        return 1.0 / (1.0 + 10 ** (-diff / 400))


@lru_cache(maxsize=8)
def _fetch(date_str: str) -> ClubElo:
    url = _API_URL.format(date=date_str)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[clubelo] fetch failed for {date_str}: {e}")
        return ClubElo({})

    ratings: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        try:
            club = row["Club"].strip()
            elo = float(row["Elo"])
            ratings[club] = elo
        except (KeyError, ValueError):
            continue

    print(f"[clubelo] loaded {len(ratings)} club ELO ratings for {date_str}")
    return ClubElo(ratings)
