#!/usr/bin/env python3
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env if present
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import click
from tabulate import tabulate

import db
from config import ACTIVE_SOCCER_LEAGUES
from engine import aggregator
from models import EdgeFlag, MarketType, Pick, PickSide, Sport
from sources.oddsshark import OddsSharkSource
from sources.xgscore import XGScoreSource
from sources.kalshi import KalshiSource

# ActionNetwork and BettingPros require browser-use / playwright which may not
# be installed in lightweight environments (e.g. GitHub Actions).
try:
    from sources.actionnetwork import ActionNetworkSource
    _ACTIONNETWORK = ActionNetworkSource()
except ImportError:
    _ACTIONNETWORK = None

def _make_bettingpros_source():
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from sources.bettingpros_agent import BettingProsAgentSource
            return BettingProsAgentSource()
        except ImportError:
            pass
    from sources.bettingpros import BettingProsSource
    return BettingProsSource()

_BETTINGPROS = _make_bettingpros_source()
_XGSCORE = XGScoreSource()
_KALSHI = KalshiSource()
_NON_SOCCER_SOURCES = [s for s in [OddsSharkSource(), _BETTINGPROS, _ACTIONNETWORK] if s]
_SOCCER_SOURCES = [_XGSCORE, _KALSHI]

def _sources_for(sport: Sport):
    return _NON_SOCCER_SOURCES

SPORT_CHOICES = [s.value for s in Sport]
MARKET_CHOICES = [m.value for m in MarketType]
SIDE_CHOICES = [s.value for s in PickSide]


@click.group()
def cli():
    """Sports pick aggregator with Kalshi-derived edge filtering."""
    db.init_db()


@cli.command()
@click.option("--sport", type=click.Choice(SPORT_CHOICES), required=True)
@click.option("--date", default=datetime.utcnow().strftime("%Y-%m-%d"), help="YYYY-MM-DD")
@click.option("--league", default=None, help="Soccer league filter (e.g. EPL, MLS)")
def fetch(sport, date, league):
    """Fetch picks from all automated sources for a sport and date."""
    target_date = datetime.strptime(date, "%Y-%m-%d")
    all_picks = []

    if Sport(sport) == Sport.SOCCER:
        all_picks = _fetch_soccer(target_date, league)
    else:
        for source in _sources_for(Sport(sport)):
            picks = source.fetch_picks(Sport(sport), target_date)
            if picks:
                click.echo(f"  {source.name}: {len(picks)} picks")
                for p in picks:
                    db.upsert_pick(p)
                all_picks.extend(picks)
            else:
                click.echo(f"  {source.name}: 0 picks (manual entry or not implemented)")

    if all_picks:
        click.echo(f"\nAggregating {len(all_picks)} picks...")
        aggs = aggregator.aggregate(all_picks)
        for a in aggs:
            db.save_aggregated(a)
        _print_aggregated(aggs)
    else:
        click.echo("No picks fetched. Use 'add-pick' to enter manually.")


def _fetch_soccer(target_date: datetime, league_filter: str | None) -> list[Pick]:
    """Fetch soccer picks across all active leagues (or one if --league is set)."""
    leagues = ACTIVE_SOCCER_LEAGUES
    if league_filter:
        if league_filter not in leagues:
            click.echo(f"Unknown league '{league_filter}'. Choices: {', '.join(leagues)}")
            return []
        leagues = {league_filter: leagues[league_filter]}

    all_picks: list[Pick] = []

    for league_name in leagues:
        click.echo(f"\n--- {league_name} ---")

        for source in _SOCCER_SOURCES:
            picks = source.fetch_picks(Sport.SOCCER, target_date, league=league_name)
            if picks:
                click.echo(f"  {source.name}: {len(picks)} picks")
                for p in picks:
                    db.upsert_pick(p)
                all_picks.extend(picks)
            else:
                click.echo(f"  {source.name}: 0 picks")

    return all_picks


@cli.command("add-pick")
@click.option("--source", type=click.Choice(["xgscore","oddsshark","actionnetwork","bettingpros","manual"]), required=True)
@click.option("--sport", type=click.Choice(SPORT_CHOICES), required=True)
@click.option("--home", required=True, help="Home team name")
@click.option("--away", required=True, help="Away team name")
@click.option("--game-time", required=True, help="YYYY-MM-DD HH:MM (UTC)")
@click.option("--market", type=click.Choice(MARKET_CHOICES), required=True)
@click.option("--side", type=click.Choice(SIDE_CHOICES), required=True)
@click.option("--team", default=None, help="Specific team picked (for GAME/SPREAD)")
@click.option("--line", type=float, default=None, help="Spread or total line value")
@click.option("--odds", default=None, help="American odds, e.g. -110 or +150")
@click.option("--confidence", type=float, default=None, help="0.0–1.0 confidence (source-stated)")
@click.option("--notes", default=None)
def add_pick(source, sport, home, away, game_time, market, side, team, line, odds, confidence, notes):
    """Manually enter a pick from any source."""
    from sources.base import BaseSource
    _base = BaseSource.__new__(BaseSource)
    implied = _base._american_to_implied(odds) if odds else None

    pick = Pick(
        source=source,
        sport=Sport(sport),
        home_team=home,
        away_team=away,
        game_time=datetime.strptime(game_time, "%Y-%m-%d %H:%M"),
        market_type=MarketType(market),
        pick_side=PickSide(side),
        pick_team=team,
        line=line,
        implied_prob=implied,
        confidence=confidence,
        raw_odds=odds,
        notes=notes,
        fetched_at=datetime.utcnow(),
    )
    game_id = db.upsert_pick(pick)
    click.echo(f"Pick saved (game: {game_id})")


@cli.command()
@click.option("--date", default=datetime.utcnow().strftime("%Y-%m-%d"), help="YYYY-MM-DD")
@click.option("--sport", default=None, type=click.Choice(SPORT_CHOICES))
def recommend(date, sport):
    """Show aggregated recommendations for a date, sorted by edge + score."""
    rows = db.load_picks_for_date(date)
    if not rows:
        click.echo(f"No picks found for {date}. Run 'fetch' or 'add-pick' first.")
        return

    picks = []
    for r in rows:
        if sport and r["sport"] != sport:
            continue
        try:
            picks.append(Pick(
                source=r["source"],
                sport=Sport(r["sport"]),
                home_team=r["home_team"],
                away_team=r["away_team"],
                game_time=datetime.fromisoformat(r["game_time"]),
                market_type=MarketType(r["market_type"]),
                pick_side=PickSide(r["pick_side"]),
                pick_team=r.get("pick_team"),
                line=r.get("line"),
                implied_prob=r.get("implied_prob"),
                confidence=r.get("confidence"),
                raw_odds=r.get("raw_odds"),
                notes=r.get("notes"),
                league=r.get("league"),
            ))
        except Exception:
            continue

    aggs = aggregator.aggregate(picks)
    for a in aggs:
        db.save_aggregated(a)
    _print_aggregated(aggs)


@cli.command("log-outcome")
@click.option("--sport", type=click.Choice(SPORT_CHOICES), required=True)
@click.option("--home", required=True)
@click.option("--away", required=True)
@click.option("--game-time", required=True, help="YYYY-MM-DD HH:MM (UTC)")
@click.option("--home-score", type=int, required=True)
@click.option("--away-score", type=int, required=True)
def log_outcome(sport, home, away, game_time, home_score, away_score):
    """Record the actual result so accuracy can be tracked."""
    gt = datetime.strptime(game_time, "%Y-%m-%d %H:%M")
    db.log_outcome(sport, home, away, gt, home_score, away_score)
    winner = "HOME" if home_score > away_score else ("AWAY" if away_score > home_score else "TIE")
    click.echo(f"Outcome logged: {away} @ {home} → {winner} ({away_score}-{home_score})")


@cli.command()
def stats():
    """Show pick accuracy and ROI broken down by source and market type."""
    rows = db.accuracy_by_source()
    if not rows:
        click.echo("No settled picks yet. Log outcomes with 'log-outcome'.")
        return

    table = []
    for r in rows:
        total = r["total"]
        wins = r["wins"] or 0
        win_pct = wins / total if total else 0
        table.append([r["source"], r["market_type"], total, wins, f"{win_pct:.1%}"])

    click.echo(tabulate(table, headers=["Source", "Market", "Total", "Wins", "Win%"], tablefmt="rounded_grid"))


_EDGE_PRIORITY = {
    EdgeFlag.STRONG_EDGE: 3,
    EdgeFlag.SLIGHT_EDGE: 2,
    EdgeFlag.NEUTRAL: 1,
    EdgeFlag.FADE: 0,
}


def _print_aggregated(aggs):
    if not aggs:
        click.echo("No aggregated picks.")
        return

    sorted_aggs = sorted(aggs, key=lambda a: (
        a.game_time.strftime("%Y-%m-%d"),
        a.league or a.sport.value,
        -_EDGE_PRIORITY.get(a.edge_flag, 0),
        -a.composite_score,
    ))

    table = []
    current_group = None
    for a in sorted_aggs:
        group = (a.game_time.strftime("%Y-%m-%d"), a.league or a.sport.value)
        if group != current_group:
            if current_group is not None:
                table.append([""] * 10)
            current_group = group

        game = f"{a.away_team} @ {a.home_team}"
        pick_str = a.pick_team or a.pick_side.value
        stake_str = "★" * a.recommended_stake_level if a.recommended_stake_level > 0 else "skip"
        sources_str = ",".join(a.sources_agreeing)
        notes_preview = a.kalshi_notes[0] if a.kalshi_notes else ""
        try:
            from datetime import timezone as _tz
            local = a.game_time.replace(tzinfo=_tz.utc).astimezone()
            kickoff = local.strftime("%-m/%-d %-I:%M%p")
        except Exception:
            kickoff = a.game_time.strftime("%m/%d %H:%M")
        table.append([
            a.league or a.sport.value,
            kickoff,
            game,
            a.market_type.value,
            pick_str,
            f"{a.composite_score:.2f}",
            a.edge_flag.value,
            stake_str,
            f"{a.source_count} ({sources_str})",
            notes_preview[:55],
        ])

    click.echo(tabulate(
        table,
        headers=["League","Kickoff","Game","Market","Pick","Score","Edge","Stake","Sources","Note"],
        tablefmt="rounded_grid",
    ))


if __name__ == "__main__":
    cli()
