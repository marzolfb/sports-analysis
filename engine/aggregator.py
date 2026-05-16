from collections import defaultdict
from datetime import datetime

from models import AggregatedPick, EdgeFlag, MarketType, Pick, PickSide, Sport
from config import source_weights_for_sport
from engine import edge_filter


def _game_key(pick: Pick) -> tuple:
    date = pick.game_time.strftime("%Y%m%d")
    return (pick.sport, pick.home_team, pick.away_team, date, pick.market_type)


def _side_key(pick: Pick) -> str:
    return pick.pick_side.value


def aggregate(picks: list[Pick]) -> list[AggregatedPick]:
    """
    Groups picks by game+market, scores weighted agreement, applies Kalshi edge filter.
    Returns one AggregatedPick per (game, market_type, dominant_side), sorted by score desc.
    """
    # Group all picks by game+market
    game_groups: dict[tuple, list[Pick]] = defaultdict(list)
    for p in picks:
        game_groups[_game_key(p)].append(p)

    results = []
    for (sport, home, away, date, market_type), group in game_groups.items():
        weights = source_weights_for_sport(Sport(sport))

        # Tally weighted votes per side
        side_scores: dict[str, float] = defaultdict(float)
        side_picks: dict[str, list[Pick]] = defaultdict(list)
        for p in group:
            w = weights.get(p.source, 0.10)
            side = _side_key(p)
            side_scores[side] += w
            side_picks[side].append(p)

        if not side_scores:
            continue

        # Dominant side
        dominant_side = max(side_scores, key=lambda s: side_scores[s])
        total_weight = sum(weights.get(p.source, 0.10) for p in group)
        composite = side_scores[dominant_side] / total_weight if total_weight else 0.0
        composite = max(0.0, min(1.0, composite))

        agreeing_sources = [p.source for p in side_picks[dominant_side]]
        disagreeing_sources = [p.source for p in group if p.source not in agreeing_sources]

        # Representative pick: prefer one with a specific (non-midnight) kickoff time
        candidates = side_picks[dominant_side]
        rep = next(
            (p for p in candidates if p.game_time.hour != 0 or p.game_time.minute != 0),
            candidates[0],
        )

        # Average implied prob across agreeing picks that have it
        probs = [p.implied_prob for p in side_picks[dominant_side] if p.implied_prob]
        avg_prob = sum(probs) / len(probs) if probs else None

        agg = AggregatedPick(
            sport=Sport(sport),
            home_team=home,
            away_team=away,
            game_time=rep.game_time,
            market_type=MarketType(market_type),
            pick_side=PickSide(dominant_side),
            pick_team=rep.pick_team,
            composite_score=composite,
            source_count=len(agreeing_sources),
            sources_agreeing=list(set(agreeing_sources)),
            sources_disagreeing=list(set(disagreeing_sources)),
        )

        agg.league = getattr(rep, 'league', None)
        agg = edge_filter.apply(agg, implied_prob=avg_prob)
        results.append(agg)

    results.sort(key=lambda a: (a.recommended_stake_level, a.composite_score), reverse=True)
    return results
