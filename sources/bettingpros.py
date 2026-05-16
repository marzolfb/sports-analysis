"""
BettingPros source.

BettingPros aggregates expert picks from dozens of cappers and publishes a
consensus rating (1–5 stars) per game per market. Their picks page URL pattern:
  https://www.bettingpros.com/mlb/picks/         (today)
  https://www.bettingpros.com/mlb/picks/?date=YYYY-MM-DD

The page renders via JavaScript, so a simple requests fetch won't get the picks table.
Two options:
  1. Use their internal JSON API — inspect XHR in DevTools on the picks page,
     look for a /api/v3/picks endpoint with an Authorization header.
  2. Use Selenium/Playwright for browser rendering.

For now this source falls back to manual entry via `main.py add-pick`.
TODO: capture the /api/v3/picks endpoint URL and auth token from browser DevTools,
      then implement _fetch_api() below.
"""

from datetime import datetime

from models import MarketType, Pick, PickSide, Sport
from sources.base import BaseSource

SPORT_SLUGS = {
    Sport.MLB: "mlb",
    Sport.NBA: "nba",
    Sport.NHL: "nhl",
    Sport.NFL: "nfl",
    Sport.SOCCER: "soccer",
}


class BettingProsSource(BaseSource):
    name = "bettingpros"

    def fetch_picks(self, sport: Sport, date: datetime) -> list[Pick]:
        # TODO: implement once API endpoint is captured from browser DevTools
        print(f"[bettingpros] automated fetch not yet implemented — use 'add-pick' for manual entry")
        return []

    def _parse_pick_row(self, row: dict, sport: Sport, date: datetime) -> Pick | None:
        """
        Expected shape from /api/v3/picks (fill in once endpoint is confirmed):
          row = {
            "game": {"home": "...", "away": "...", "start_time": "..."},
            "market": "spread" | "total" | "moneyline",
            "pick": "home" | "away" | "over" | "under",
            "consensus_rating": 1-5,
            "line": float,
            "odds": "-110",
          }
        """
        try:
            game = row["game"]
            market_map = {"moneyline": MarketType.GAME, "spread": MarketType.SPREAD, "total": MarketType.TOTAL}
            side_map = {"home": PickSide.HOME, "away": PickSide.AWAY, "over": PickSide.OVER, "under": PickSide.UNDER}

            market_type = market_map.get(row.get("market", ""), MarketType.GAME)
            pick_side = side_map.get(row.get("pick", ""), PickSide.HOME)
            team = game["home"] if pick_side == PickSide.HOME else game.get("away")
            rating = row.get("consensus_rating", 3)
            confidence = rating / 5.0

            return Pick(
                source=self.name,
                sport=sport,
                home_team=game["home"],
                away_team=game["away"],
                game_time=date,
                market_type=market_type,
                pick_side=pick_side,
                pick_team=team,
                line=row.get("line"),
                implied_prob=self._american_to_implied(row.get("odds", "")),
                confidence=confidence,
                raw_odds=row.get("odds"),
                notes=f"BettingPros consensus: {rating}/5 stars",
                fetched_at=datetime.utcnow(),
            )
        except (KeyError, TypeError):
            return None
