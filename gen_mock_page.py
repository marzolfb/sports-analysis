#!/usr/bin/env python3
"""Generate picks_mock.html with simulated data to preview both sections."""

import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from models import AggregatedPick, EdgeFlag, MarketType, Pick, PickSide, Sport

from gen_picks_page import render_page

DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
_DT = datetime(2026, 5, 16, 19, 30)


def _agg(sport, home, away, market, side, pick_team, score, edge, stake,
         sources, notes=None, league=None):
    a = AggregatedPick(
        sport=sport, home_team=home, away_team=away, game_time=_DT,
        market_type=MarketType(market), pick_side=PickSide(side),
        pick_team=pick_team, composite_score=score,
        source_count=len(sources), sources_agreeing=sources,
        edge_flag=EdgeFlag(edge), recommended_stake_level=stake,
        kalshi_notes=[notes] if notes else [],
    )
    a.league = league
    return a


def _pick(sport, home, away, market, side, pick_team, odds, notes):
    return Pick(
        source="oddsshark", sport=sport, home_team=home, away_team=away,
        game_time=_DT, market_type=MarketType(market), pick_side=PickSide(side),
        pick_team=pick_team, raw_odds=odds, notes=notes, fetched_at=_DT,
    )


SOCCER_PICKS = [
    _agg(Sport.SOCCER, "Newcastle", "West Ham", "GAME", "HOME", "Newcastle",
         0.87, "STRONG_EDGE", 4, ["xgscore", "clubelo"],
         "ELO diff +185, xG avg edge 1.8 vs 0.9 per match", league="EPL"),
    _agg(Sport.SOCCER, "Lazio", "Roma", "GAME", "AWAY", "Roma",
         0.79, "STRONG_EDGE", 3, ["xgscore", "clubelo", "kalshi"],
         "Derby form: Roma +W3, Kalshi 38¢ underdog zone", league="Serie A"),
    _agg(Sport.SOCCER, "Real Betis", "Barcelona", "GAME", "AWAY", "Barcelona",
         0.71, "SLIGHT_EDGE", 2, ["xgscore", "kalshi"], league="La Liga"),
    # FADE — will appear in entertainment section
    _agg(Sport.SOCCER, "Real Madrid", "Sevilla", "GAME", "HOME", "Real Madrid",
         0.94, "FADE", 0, ["xgscore", "clubelo", "kalshi"],
         "Heavy favorite zone (>75¢) — historically -15% ROI", league="La Liga"),
]

ENTERTAINMENT_PICKS = [
    _pick(Sport.MLB,  "Red Sox",  "Yankees",   "GAME",   "AWAY", "Yankees",  "-145", "Public bets: 68% away / 32% home"),
    _pick(Sport.NBA,  "Heat",     "Celtics",   "GAME",   "HOME", "Celtics",  "-190", "Public bets: 72% home / 28% away"),
    _pick(Sport.NHL,  "Sabres",   "Canadiens", "TOTAL",  "OVER", None,       "-115", "Total 5.5: 61% over / 39% under"),
    _pick(Sport.MLB,  "Padres",   "Dodgers",   "SPREAD", "HOME", "Dodgers",  "-1.5", "Public bets: 65% home / 35% away"),
]

if __name__ == "__main__":
    page = render_page(SOCCER_PICKS, ENTERTAINMENT_PICKS,
                       log="[mock] Simulated data — not real picks.", date=DATE)
    out = PROJECT / "picks_mock.html"
    out.write_text(page)
    print(f"Written → {out}")
    print(f"View at: http://192.168.68.66:8765/picks_mock.html")
