"""
ActionNetwork source — browser-use agent targeting picks articles.

ActionNetwork publishes expert analysis articles with embedded pick recommendations.
The agent navigates to the picks hub, finds today's articles, and extracts
the recommended pick and reasoning from each.

Results are cached daily so repeated runs don't incur additional API costs.
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

TASK_TEMPLATE = """
Go to https://www.actionnetwork.com/{slug}/picks

Find all picks articles published today ({date_str}) or for today's games.
For each article visible on the page:
1. Note the game it covers (home team, away team).
2. Look for the explicit pick recommendation — usually shown as a highlighted
   call-out box, bold text, or "The Pick:" label.
3. Note whether the pick is a moneyline, spread, or total.
4. Note any odds or line shown alongside the pick.

Do NOT click into individual articles — extract from what is visible on the
picks hub page itself. If a pick is not clearly stated, skip that article.

Collect:
- home_team: home team name
- away_team: away team name
- game_time_str: game time if shown (null if not)
- market_type: "moneyline", "spread", or "total"
- pick_team_or_side: team name, "over", or "under"
- is_home: true if picked team is home team, false if away (null for over/under)
- line: spread or total value as a number (null if not shown)
- odds: American odds string like "-115" (null if not shown)
- article_headline: the article headline/title
- notes: the pick reasoning in 1-2 sentences if shown

When done, call done() with ONLY the raw JSON — no markdown, no explanation:
{{"picks": [{{"home_team": "...", "away_team": "...", ...}}, ...]}}
"""


class _RawPick(BaseModel):
    home_team: str
    away_team: str
    game_time_str: Optional[str] = None
    market_type: str
    pick_team_or_side: str
    is_home: Optional[bool] = None
    line: Optional[float] = None
    odds: Optional[str] = None
    article_headline: Optional[str] = None
    notes: Optional[str] = None


class _RawPickList(BaseModel):
    picks: list[_RawPick]


class ActionNetworkSource(BaseSource):
    name = "actionnetwork"

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY")

    def fetch_picks(self, sport: Sport, date: datetime) -> list[Pick]:
        if not self._api_key:
            print("[actionnetwork] ANTHROPIC_API_KEY not set — skipping")
            return []

        cached = cache.get(self.name, sport.value, date)
        if cached:
            print(f"[actionnetwork] using cached data for {sport.value} {date.strftime('%Y-%m-%d')}")
            return self._parse_raw(cached, sport, date)

        return asyncio.run(self._fetch_async(sport, date))

    async def _fetch_async(self, sport: Sport, date: datetime) -> list[Pick]:
        from browser_use import Agent
        from browser_use.llm import ChatAnthropic

        slug = SPORT_SLUGS.get(sport)
        if not slug:
            return []

        date_str = date.strftime("%Y-%m-%d")
        task = TASK_TEMPLATE.format(slug=slug, date_str=date_str)

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

        print(f"[actionnetwork] launching browser agent for {sport.value} on {date_str}...")
        history = await agent.run()

        raw_str = history.final_result()
        if not raw_str:
            print("[actionnetwork] agent returned no result")
            return []

        cache.set(self.name, sport.value, date, raw_str)
        return self._parse_raw(raw_str, sport, date)

    def _parse_raw(self, raw_str: str, sport: Sport, date: datetime) -> list[Pick]:
        try:
            cleaned = raw_str.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)
            raw_list = _RawPickList.model_validate(data)
        except Exception as e:
            print(f"[actionnetwork] failed to parse output: {e}")
            print(f"Raw (first 500): {raw_str[:500]}")
            return []

        picks = [self._to_pick(r, sport, date) for r in raw_list.picks]
        picks = [p for p in picks if p is not None]
        print(f"[actionnetwork] {len(picks)} picks")
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
            elif raw.is_home is True:
                pick_side, pick_team = PickSide.HOME, raw.home_team
            elif raw.is_home is False:
                pick_side, pick_team = PickSide.AWAY, raw.away_team
            elif raw.home_team.lower() in s or s in raw.home_team.lower():
                pick_side, pick_team = PickSide.HOME, raw.home_team
            else:
                pick_side, pick_team = PickSide.AWAY, raw.away_team

            note_parts = ["ActionNetwork expert pick"]
            if raw.article_headline:
                note_parts.append(raw.article_headline[:60])
            if raw.notes:
                note_parts.append(raw.notes[:80])

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
                raw_odds=raw.odds,
                notes=" | ".join(note_parts),
                fetched_at=datetime.utcnow(),
            )
        except Exception as e:
            print(f"[actionnetwork] skipping pick: {e}")
            return None
