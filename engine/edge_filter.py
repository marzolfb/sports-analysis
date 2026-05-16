"""
Applies Kalshi-derived edge adjustments to aggregated picks.

Derived from 1,546 settled sports markets:
  - 15-25¢ underdogs: +37.6% ROI (best edge)
  - <15¢ underdogs:   +19.8% ROI
  - 45-55¢ near-even: +3.8% ROI (slight edge)
  - Heavy favorites (>55¢): -12% to -58% ROI
  - Totals (OVER/UNDER): -$46 total loss — worst market type
  - Bigger bets = worse outcomes (overconfidence is miscalibrated)
"""

from models import AggregatedPick, EdgeFlag, MarketType
from config import EDGE_ZONES, FADE_ZONES, MARKET_TYPE_SCORE_DELTA, STAKE_BY_EDGE


def apply(pick: AggregatedPick, implied_prob: float | None = None) -> AggregatedPick:
    notes = list(pick.kalshi_notes)

    # Market type penalty
    delta = MARKET_TYPE_SCORE_DELTA.get(pick.market_type.value, 0.0)
    if delta < 0:
        notes.append(f"TOTAL bets lost -$46 in Kalshi history — score penalized {delta:+.2f}")
    elif delta > 0:
        notes.append(f"GAME/moneyline was the only profitable market type historically")
    pick.composite_score = max(0.0, min(1.0, pick.composite_score + delta))

    # Edge zone classification by implied probability
    edge_flag = EdgeFlag.NEUTRAL
    edge_zone_hit = False

    if implied_prob is not None:
        for lo, hi, flag in EDGE_ZONES:
            if lo <= implied_prob < hi:
                edge_flag = EdgeFlag(flag)
                edge_zone_hit = True
                notes.append(
                    f"Implied prob {implied_prob:.2f} is in the "
                    f"{lo:.0%}–{hi:.0%} edge zone (historically +ROI)"
                )
                break

        if not edge_zone_hit:
            for lo, hi in FADE_ZONES:
                if lo <= implied_prob < hi:
                    edge_flag = EdgeFlag.FADE
                    notes.append(
                        f"Implied prob {implied_prob:.2f} is in the fade zone — "
                        f"historically negative ROI in this range"
                    )
                    break

    # Stake level — deliberately conservative; Kalshi shows overconfidence = bigger losses
    stake = STAKE_BY_EDGE.get(edge_flag.value, 1)
    if pick.source_count == 1:
        notes.append("Only 1 source agrees — single-source picks can outperform high-consensus ones")
    elif pick.source_count >= 3:
        notes.append("3+ sources agree — watch for overconfidence trap; don't size up beyond stake level")

    pick.edge_flag = edge_flag
    pick.edge_zone_hit = edge_zone_hit
    pick.kalshi_notes = notes
    pick.recommended_stake_level = stake

    return pick
