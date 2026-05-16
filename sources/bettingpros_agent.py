"""
BettingPros browser-use agent.

Uses an LLM-driven browser agent (browser-use + Claude Sonnet) to navigate
BettingPros, render the JS picks page, and extract structured pick data.

Requires: ANTHROPIC_API_KEY in environment or .env

Schema change from v1: replaced confidence_stars (didn't exist on the page,
caused the agent to hallucinate) with pct_bets / pct_money — the actual
signals BettingPros shows. Confidence is derived in Python from those values.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

import cache
from models import MarketType, Pick, PickSide, Sport
from sources.base import BaseSource

SPORT_SLUGS = {
    Sport.MLB:    "mlb",
    Sport.NBA:    "nba",
    Sport.NHL:    "nhl",
    Sport.NFL:    "nfl",
    Sport.SOCCER: "soccer",
}


class _RawPick(BaseModel):
    home_team: str
    away_team: str
    game_time_str: str
    # "moneyline" | "spread" | "total" — read from the section header or card label
    market_type: str
    # team name for moneyline/spread; "over" or "under" for totals
    pick_team_or_side: str
    # percentage of bets placed on this side, e.g. 75 means 75%
    pct_bets: Optional[float] = None
    # percentage of money on this side, e.g. 68 means 68%
    pct_money: Optional[float] = None
    line: Optional[float] = None
    odds: Optional[str] = None
    notes: Optional[str] = None


class _RawPickList(BaseModel):
    picks: list[_RawPick]


def _pct_to_confidence(pct_bets: float | None, pct_money: float | None) -> float | None:
    """Convert BettingPros % of bets/money into 0–1 confidence. Money weighted 2:1 over bets."""
    values = []
    if pct_bets is not None:
        values.append((pct_bets / 100.0, 1))
    if pct_money is not None:
        values.append((pct_money / 100.0, 2))
    if not values:
        return None
    total_w = sum(w for _, w in values)
    return sum(v * w for v, w in values) / total_w


TASK_TEMPLATE = """
Navigate to {url}

Your goal is to extract EVERY pick shown on this page for {date_str}.

Step-by-step instructions:
1. Wait for the page to fully load (picks table/cards should be visible).
2. Look for a section called "Bet Signals" or a picks list. Note the market type
   label in the section header (e.g. "Moneyline", "Spread", "Totals") — that is
   the market_type for ALL picks in that section.
3. For each pick card visible, extract the fields listed below.
4. Scroll down slowly to find more picks below the fold.
5. If you see a "View More", "Show More", or "Load More" button, click it and
   extract the newly revealed picks too.
6. Repeat scrolling and clicking until you have reached the bottom of the page.
7. Return ALL picks found, not just the first few.

For each pick extract:
- home_team: home team name exactly as shown
- away_team: away team name exactly as shown
- game_time_str: start time exactly as shown (e.g. "7:05 PM ET")
- market_type: "moneyline", "spread", or "total" — read from the section header
- pick_team_or_side: the team being picked (for moneyline/spread) OR "over"/"under"
- pct_bets: the "% of Bets" number for the picked side as a plain number (e.g. 75 for 75%)
- pct_money: the "% of Money" number for the picked side as a plain number (e.g. 68 for 68%)
- line: the spread or total line value as a number if visible (null if not)
- odds: American odds string like "-115" or "+130" if visible (null if not)
- notes: any short explanatory text shown on the card (null if none)

Important:
- DO NOT skip picks because they seem uncertain.
- DO NOT invent confidence stars — only record pct_bets and pct_money as seen.
- If a value is not visible, use null.

When you have collected all picks, call done() with ONLY the raw JSON object as your
message — no explanation, no markdown fences, no extra text. Example:
{{"picks": [{{"home_team": "Yankees", "away_team": "Red Sox", ...}}, ...]}}
"""


class BettingProsAgentSource(BaseSource):
    name = "bettingpros"

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — add it to .env or export it before running"
            )

    def fetch_picks(self, sport: Sport, date: datetime) -> list[Pick]:
        cached = cache.get(self.name, sport.value, date)
        if cached:
            print(f"[bettingpros] using cached data for {sport.value} {date.strftime('%Y-%m-%d')}")
            return self._parse_raw(cached, sport, date)
        return asyncio.run(self._fetch_async(sport, date))

    async def _fetch_async(self, sport: Sport, date: datetime) -> list[Pick]:
        from browser_use import Agent
        from browser_use.llm import ChatAnthropic

        slug = SPORT_SLUGS.get(sport)
        if not slug:
            return []

        date_str = date.strftime("%Y-%m-%d")
        url = f"https://www.bettingpros.com/{slug}/picks/?date={date_str}"
        task = TASK_TEMPLATE.format(url=url, date_str=date_str)

        llm = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=self._api_key)
        agent = Agent(
            task=task,
            llm=llm,
            use_vision=False,        # DOM text is sufficient; screenshots are ~70% of token cost
            use_thinking=False,      # Haiku's thinking output breaks AgentOutput validation
            max_failures=3,
            max_actions_per_step=10,
            max_history_items=15,    # cap growing context window
            step_timeout=60,
        )

        print(f"[bettingpros] launching browser agent for {sport.value} on {date_str}...")
        history = await agent.run()

        raw_str = history.final_result()
        if not raw_str:
            print("[bettingpros] agent returned no result")
            return []

        cache.set(self.name, sport.value, date, raw_str)
        return self._parse_raw(raw_str, sport, date)

    def _parse_raw(self, raw_str: str, sport: Sport, date: datetime) -> list[Pick]:
        try:
            cleaned = raw_str.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)
            raw_list = _RawPickList.model_validate(data)
        except Exception as e:
            print(f"[bettingpros] failed to parse output: {e}")
            print(f"Raw (first 800): {raw_str[:800]}")
            return []

        picks = [self._to_pick(r, sport, date) for r in raw_list.picks]
        picks = [p for p in picks if p is not None]
        print(f"[bettingpros] {len(picks)} picks")
        return picks

    def _to_pick(self, raw: _RawPick, sport: Sport, date: datetime) -> Pick | None:
        try:
            market_map = {
                "moneyline": MarketType.GAME,
                "spread":    MarketType.SPREAD,
                "total":     MarketType.TOTAL,
            }
            market_type = market_map.get(raw.market_type.lower(), MarketType.GAME)

            s = raw.pick_team_or_side.strip().lower()
            if s == "over":
                pick_side, pick_team = PickSide.OVER, None
            elif s == "under":
                pick_side, pick_team = PickSide.UNDER, None
            elif raw.home_team.lower() in s or s in raw.home_team.lower():
                pick_side, pick_team = PickSide.HOME, raw.home_team
            else:
                pick_side, pick_team = PickSide.AWAY, raw.away_team

            confidence = _pct_to_confidence(raw.pct_bets, raw.pct_money)

            bets_str  = f"{raw.pct_bets:.0f}% bets"  if raw.pct_bets  is not None else ""
            money_str = f"{raw.pct_money:.0f}% money" if raw.pct_money is not None else ""
            consensus_label = " / ".join(x for x in [bets_str, money_str] if x) or "no consensus data"
            note = f"BettingPros: {consensus_label}"
            if raw.notes:
                note += f" | {raw.notes}"

            return Pick(
                source=self.name,
                sport=sport,
                home_team=raw.home_team,
                away_team=raw.away_team,
                game_time=date,
                market_type=market_type,
                pick_side=pick_side,
                pick_team=pick_team,
                line=raw.line,
                implied_prob=self._american_to_implied(raw.odds) if raw.odds else None,
                confidence=confidence,
                raw_odds=raw.odds,
                notes=note,
                fetched_at=datetime.utcnow(),
            )
        except Exception as e:
            print(f"[bettingpros] skipping pick due to parse error: {e}")
            return None
