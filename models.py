from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Sport(str, Enum):
    MLB = "MLB"
    NBA = "NBA"
    NHL = "NHL"
    NFL = "NFL"
    SOCCER = "SOCCER"


class MarketType(str, Enum):
    GAME = "GAME"      # moneyline / outright winner
    SPREAD = "SPREAD"
    TOTAL = "TOTAL"    # over/under


class PickSide(str, Enum):
    HOME = "HOME"
    AWAY = "AWAY"
    OVER = "OVER"
    UNDER = "UNDER"


class EdgeFlag(str, Enum):
    STRONG_EDGE = "STRONG_EDGE"   # 15-35¢ underdog zone: +20-38% ROI historically
    SLIGHT_EDGE = "SLIGHT_EDGE"   # near even-money (45-55¢): +4% ROI historically
    NEUTRAL = "NEUTRAL"
    FADE = "FADE"                 # heavy favorites or totals: historically negative


@dataclass
class Pick:
    source: str
    sport: Sport
    home_team: str
    away_team: str
    game_time: datetime
    market_type: MarketType
    pick_side: PickSide
    pick_team: Optional[str] = None   # team name for GAME/SPREAD
    line: Optional[float] = None      # spread value or total
    implied_prob: Optional[float] = None   # 0.0–1.0
    confidence: Optional[float] = None    # 0.0–1.0, source-stated
    raw_odds: Optional[str] = None        # e.g. "-110", "+150"
    notes: Optional[str] = None
    fetched_at: Optional[datetime] = None
    league: Optional[str] = None          # e.g. "EPL", "MLS" — soccer only


@dataclass
class AggregatedPick:
    sport: Sport
    home_team: str
    away_team: str
    game_time: datetime
    market_type: MarketType
    pick_side: PickSide
    pick_team: Optional[str]

    composite_score: float      # 0.0–1.0 weighted agreement score
    source_count: int
    sources_agreeing: list = field(default_factory=list)
    sources_disagreeing: list = field(default_factory=list)

    edge_flag: EdgeFlag = EdgeFlag.NEUTRAL
    edge_zone_hit: bool = False      # True if implied_prob in the sweet-spot range
    kalshi_notes: list = field(default_factory=list)

    # 1 = smallest, 5 = largest — deliberately capped because big bets historically lose
    recommended_stake_level: int = 1
    league: Optional[str] = None          # e.g. "EPL", "MLS" — soccer only
