"""
OddsShark source — direct HTTP (no agent needed).

API endpoint confirmed working:
  https://www.oddsshark.com/api/scores/{sport}/{YYYY-MM-DD}

Returns game data including:
  - teams.home/away.moneyLine  : American odds
  - teams.home/away.votes      : % of public bets on each side (moneyline)
  - teams.home/away.spread     : spread value
  - overVotes / underVotes     : % of public bets on over/under
  - total                      : total line
  - overPrice / underPrice     : American odds for over/under
"""

import json
from datetime import datetime

import requests

import cache
from models import MarketType, Pick, PickSide, Sport
from sources.base import BaseSource

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

SPORT_SLUGS = {
    Sport.MLB: "mlb",
    Sport.NBA: "nba",
    Sport.NHL: "nhl",
    Sport.NFL: "nfl",
    # SOCCER intentionally excluded — OddsShark soccer API returns 404
}

# Minimum public bet % to generate a pick signal (avoid 50/50 noise)
MIN_VOTE_PCT = 55


class OddsSharkSource(BaseSource):
    name = "oddsshark"

    def fetch_picks(self, sport: Sport, date: datetime) -> list[Pick]:
        slug = SPORT_SLUGS.get(sport)
        if not slug:
            return []

        cached = cache.get(self.name, sport.value, date)
        if cached:
            print(f"[oddsshark] using cached data for {sport.value} {date.strftime('%Y-%m-%d')}")
            games = json.loads(cached)
        else:
            url = f"https://www.oddsshark.com/api/scores/{slug}/{date.strftime('%Y-%m-%d')}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[oddsshark] fetch failed: {e}")
                return []
            games = data.get("scores", [])
            cache.set(self.name, sport.value, date, json.dumps(games))

        picks = []
        for game in games:
            try:
                picks.extend(self._parse_game(game, sport, date))
            except Exception:
                continue
        return picks

    def _parse_game(self, game: dict, sport: Sport, date: datetime) -> list[Pick]:
        home_data = game.get("teams", {}).get("home", {})
        away_data = game.get("teams", {}).get("away", {})

        home = home_data.get("names", {}).get("name", "")
        away = away_data.get("names", {}).get("name", "")
        if not home or not away:
            return []

        # Parse actual kickoff time from unix timestamp (UTC)
        unix_ts = game.get("date")
        try:
            from datetime import timezone as _tz
            game_time = datetime.fromtimestamp(int(unix_ts), tz=_tz.utc).replace(tzinfo=None)
        except Exception:
            game_time = date

        picks = []

        # --- Moneyline: use public vote % as signal ---
        home_votes = home_data.get("votes") or 0
        away_votes = away_data.get("votes") or 0
        home_ml = home_data.get("moneyLine")
        away_ml = away_data.get("moneyLine")

        if home_votes >= MIN_VOTE_PCT or away_votes >= MIN_VOTE_PCT:
            if home_votes >= away_votes:
                side, team, ml = PickSide.HOME, home, home_ml
                vote_pct = home_votes
            else:
                side, team, ml = PickSide.AWAY, away, away_ml
                vote_pct = away_votes

            picks.append(Pick(
                source=self.name,
                sport=sport,
                home_team=home,
                away_team=away,
                game_time=game_time,
                market_type=MarketType.GAME,
                pick_side=side,
                pick_team=team,
                implied_prob=self._american_to_implied(str(ml)) if ml else None,
                raw_odds=str(ml) if ml else None,
                notes=f"Public bets: {home_votes}% home / {away_votes}% away",
                fetched_at=datetime.utcnow(),
            ))

        # --- Spread ---
        home_spread = home_data.get("spread")
        if home_spread is not None:
            # Pick the side with more public support (same votes as moneyline proxy)
            if home_votes >= away_votes and home_votes >= MIN_VOTE_PCT:
                spread_side, spread_team = PickSide.HOME, home
                spread_price = home_data.get("spreadPrice")
            elif away_votes >= MIN_VOTE_PCT:
                spread_side, spread_team = PickSide.AWAY, away
                home_spread = away_data.get("spread")
                spread_price = away_data.get("spreadPrice")
            else:
                spread_side = spread_team = spread_price = None

            if spread_side:
                picks.append(Pick(
                    source=self.name,
                    sport=sport,
                    home_team=home,
                    away_team=away,
                    game_time=game_time,
                    market_type=MarketType.SPREAD,
                    pick_side=spread_side,
                    pick_team=spread_team,
                    line=home_spread,
                    implied_prob=self._american_to_implied(str(spread_price)) if spread_price else None,
                    raw_odds=str(spread_price) if spread_price else None,
                    notes=f"Spread: {home_spread}",
                    fetched_at=datetime.utcnow(),
                ))

        # --- Total ---
        over_votes = game.get("overVotes") or 0
        under_votes = game.get("underVotes") or 0
        total_line = game.get("total")

        if total_line and (over_votes >= MIN_VOTE_PCT or under_votes >= MIN_VOTE_PCT):
            if over_votes >= under_votes:
                total_side = PickSide.OVER
                total_price = game.get("overPrice")
            else:
                total_side = PickSide.UNDER
                total_price = game.get("underPrice")

            picks.append(Pick(
                source=self.name,
                sport=sport,
                home_team=home,
                away_team=away,
                game_time=game_time,
                market_type=MarketType.TOTAL,
                pick_side=total_side,
                line=total_line,
                implied_prob=self._american_to_implied(str(total_price)) if total_price else None,
                raw_odds=str(total_price) if total_price else None,
                notes=f"Total {total_line}: {over_votes}% over / {under_votes}% under",
                fetched_at=datetime.utcnow(),
            ))

        return picks
