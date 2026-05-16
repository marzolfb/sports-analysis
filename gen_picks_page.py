#!/usr/bin/env python3
"""Generate a mobile-friendly HTML page with soccer picks + entertainment picks."""

import html as _h
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import db
    from engine import aggregator
    from models import AggregatedPick, EdgeFlag, MarketType, Pick, PickSide, Sport
    from sources.oddsshark import OddsSharkSource

DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
SPORT = "SOCCER"
ENTERTAINMENT_SPORTS = [Sport.MLB, Sport.NBA, Sport.NHL, Sport.NFL]

_EDGE = {
    EdgeFlag.STRONG_EDGE: ("#166534", "#dcfce7", "Strong Edge"),
    EdgeFlag.SLIGHT_EDGE: ("#854d0e", "#fef9c3", "Slight Edge"),
    EdgeFlag.NEUTRAL:     ("#374151", "#e5e7eb", "Neutral"),
    EdgeFlag.FADE:        ("#7f1d1d", "#fee2e2", "Fade — Skip"),
}
_EDGE_PRIORITY = {
    EdgeFlag.STRONG_EDGE: 3,
    EdgeFlag.SLIGHT_EDGE: 2,
    EdgeFlag.NEUTRAL: 1,
    EdgeFlag.FADE: 0,
}
_MARKET = {"GAME": "Moneyline", "SPREAD": "Spread", "TOTAL": "Total"}


# ── data loading ──────────────────────────────────────────────────────────────

def _fetch_log() -> str:
    result = subprocess.run(
        [sys.executable, "-W", "ignore::DeprecationWarning",
         "main.py", "fetch", "--sport", SPORT, "--date", DATE],
        capture_output=True, text=True, cwd=PROJECT,
    )
    return (result.stdout + result.stderr).strip()


def _load_soccer_aggs() -> list[AggregatedPick]:
    rows = db.load_picks_for_date(DATE)
    picks = []
    for r in rows:
        if r["sport"] != SPORT:
            continue
        try:
            picks.append(Pick(
                source=r["source"], sport=Sport(r["sport"]),
                home_team=r["home_team"], away_team=r["away_team"],
                game_time=datetime.fromisoformat(r["game_time"]),
                market_type=MarketType(r["market_type"]),
                pick_side=PickSide(r["pick_side"]),
                pick_team=r.get("pick_team"), line=r.get("line"),
                implied_prob=r.get("implied_prob"), confidence=r.get("confidence"),
                raw_odds=r.get("raw_odds"), notes=r.get("notes"),
                league=r.get("league"),
            ))
        except Exception:
            continue
    return aggregator.aggregate(picks)


def _fetch_entertainment_picks() -> list[Pick]:
    """Fetch non-soccer games via OddsShark (free, no LLM cost). Returns raw picks
    so that per-game notes (public bet %) are preserved."""
    target = datetime.strptime(DATE, "%Y-%m-%d")
    source = OddsSharkSource()
    picks: list[Pick] = []
    for sport in ENTERTAINMENT_SPORTS:
        picks.extend(source.fetch_picks(sport, target))
    # Deduplicate: one pick per (game, market_type) — keep highest-confidence side
    seen: dict[tuple, Pick] = {}
    for p in picks:
        key = (p.sport, p.home_team, p.away_team, p.market_type)
        if key not in seen or (p.implied_prob or 0) > (seen[key].implied_prob or 0):
            seen[key] = p
    return list(seen.values())


# ── HTML rendering ────────────────────────────────────────────────────────────

def render_card(a: AggregatedPick) -> str:
    text_color, bg_color, edge_label = _EDGE[a.edge_flag]
    pick_str = a.pick_team or a.pick_side.value.title()
    market = _MARKET.get(a.market_type.value, a.market_type.value)
    stake_str = "★" * a.recommended_stake_level if a.recommended_stake_level > 0 else "Skip"
    stake_color = "#fbbf24" if a.recommended_stake_level > 0 else "#6b7280"
    score_pct = int(a.composite_score * 100)
    league = getattr(a, "league", None) or a.sport.value
    try:
        if a.game_time.hour == 0 and a.game_time.minute == 0:
            game_time = a.game_time.strftime("%-m/%-d")
        else:
            game_time = a.game_time.strftime("%-m/%-d %-I:%M %p")
    except Exception:
        game_time = ""
    sources = ", ".join(a.sources_agreeing[:3])
    if len(a.sources_agreeing) > 3:
        sources += f" +{len(a.sources_agreeing)-3}"
    note = _h.escape(a.kalshi_notes[0][:80]) if a.kalshi_notes else ""

    return f"""<div class="card">
    <div class="card-top">
      <span class="badge league">{_h.escape(league)}</span>
      <span class="badge market">{market}</span>
    </div>
    <div class="game">{_h.escape(a.away_team)} @ {_h.escape(a.home_team)}</div>
    {"<div class='gtime'>" + game_time + "</div>" if game_time else ""}
    <div class="row">
      <span class="lbl">Pick</span>
      <span class="pick">{_h.escape(pick_str)}</span>
    </div>
    <div class="row score-row">
      <span class="lbl">Consensus</span>
      <div class="bar-wrap"><div class="bar" style="width:{score_pct}%"></div></div>
      <span class="score-num">{a.composite_score:.0%}</span>
    </div>
    <div class="row">
      <span class="edge-badge" style="background:{bg_color};color:{text_color}">{edge_label}</span>
      <span class="stake" style="color:{stake_color}">{stake_str}</span>
    </div>
    <div class="sources">Sources: {_h.escape(sources)} ({a.source_count})</div>
    {"<div class='note'>" + note + "</div>" if note else ""}
  </div>"""


def render_entertainment_card(p: Pick) -> str:
    pick_str = p.pick_team or p.pick_side.value.title()
    market = _MARKET.get(p.market_type.value, p.market_type.value)
    odds_str = f" ({p.raw_odds})" if p.raw_odds else ""
    note = _h.escape(p.notes) if p.notes else ""

    return f"""<div class="card ent-card">
    <div class="card-top">
      <span class="badge league">{_h.escape(p.sport.value)}</span>
      <span class="badge market">{market}</span>
    </div>
    <div class="game">{_h.escape(p.away_team)} @ {_h.escape(p.home_team)}</div>
    <div class="row">
      <span class="lbl">Lean</span>
      <span class="pick ent-pick">{_h.escape(pick_str)}{_h.escape(odds_str)}</span>
    </div>
    {"<div class='note'>" + note + "</div>" if note else ""}
  </div>"""


def render_page(soccer_aggs: list[AggregatedPick], ent_picks: list[Pick], log: str, date: str = DATE) -> str:
    now = datetime.now().strftime("%b %-d, %-I:%M %p")

    # Soccer: split into edge recs vs FADE (entertainment only)
    rec_aggs = [a for a in soccer_aggs if a.recommended_stake_level > 0]
    soccer_fade = [a for a in soccer_aggs if a.recommended_stake_level == 0]

    # Convert FADE soccer aggs to lightweight Pick-like objects for the entertainment renderer
    fade_picks: list[Pick] = []
    for a in soccer_fade:
        fade_picks.append(Pick(
            source="soccer", sport=a.sport,
            home_team=a.home_team, away_team=a.away_team,
            game_time=a.game_time, market_type=a.market_type,
            pick_side=a.pick_side, pick_team=a.pick_team,
            notes=a.kalshi_notes[0] if a.kalshi_notes else None,
        ))

    all_ent = fade_picks + ent_picks

    if rec_aggs:
        count = len(rec_aggs)
        status = f'<div class="status ok">{count} recommendation{"s" if count != 1 else ""} ready</div>'

        # Sort by (day, league, edge priority desc, score desc), then group with headers
        rec_sorted = sorted(rec_aggs, key=lambda a: (
            a.game_time.strftime("%Y-%m-%d"),
            getattr(a, "league", None) or a.sport.value,
            -_EDGE_PRIORITY.get(a.edge_flag, 0),
            -a.composite_score,
        ))
        recs_parts = []
        current_group = None
        for a in rec_sorted:
            league_name = getattr(a, "league", None) or a.sport.value
            day_str = a.game_time.strftime("%Y-%m-%d")
            group = (day_str, league_name)
            if group != current_group:
                try:
                    day_dt = datetime.strptime(day_str, "%Y-%m-%d")
                    day_label = "Today" if day_str == date else day_dt.strftime("%a %b %-d")
                except Exception:
                    day_label = day_str
                recs_parts.append(
                    f'<div class="league-header">'
                    f'<span class="lh-league">{_h.escape(league_name)}</span>'
                    f'<span class="lh-day">{day_label}</span>'
                    f'</div>'
                )
                current_group = group
            recs_parts.append(render_card(a))
        recs_html = "\n".join(recs_parts)
    else:
        status = '<div class="status wait">No edge picks yet — sources haven\'t posted lines yet</div>'
        recs_html = """<div class="empty">
      <div class="empty-icon">⏳</div>
      <div class="empty-title">Waiting for data</div>
      <div class="empty-sub">Odds and xG previews typically appear 6–12 hours before kickoff.<br>This page refreshes every hour.</div>
    </div>"""

    if all_ent:
        ent_html = "\n".join(render_entertainment_card(p) for p in all_ent)
        ent_section = f"""<div class="section-header">
    <span class="section-title">🎰 Just for Fun</span>
    <span class="section-sub">No edge — bet for entertainment only</span>
  </div>
  {ent_html}"""
    else:
        ent_section = ""

    log_section = f"""<details>
    <summary>Fetch log</summary>
    <pre>{_h.escape(log)}</pre>
  </details>""" if log else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Picks — {date}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
          background:#0a0a0a;color:#e5e7eb;padding:16px 14px;min-height:100vh}}
    h1{{font-size:1.1em;font-weight:700;color:#f9fafb}}
    .updated{{font-size:0.70em;color:#6b7280;margin-top:3px;margin-bottom:14px}}
    .status{{font-size:0.75em;font-weight:600;margin-bottom:14px;padding:8px 12px;
             border-radius:8px}}
    .status.ok{{background:#052e16;color:#4ade80}}
    .status.wait{{background:#111827;color:#9ca3af}}

    .card{{background:#141414;border:1px solid #222;border-radius:12px;
           padding:14px;margin-bottom:12px}}
    .ent-card{{background:#0f0f0f;border-color:#1a1a1a}}

    .card-top{{display:flex;justify-content:space-between;margin-bottom:8px}}
    .badge{{font-size:0.62em;font-weight:700;text-transform:uppercase;
            letter-spacing:0.07em;padding:2px 7px;border-radius:4px}}
    .badge.league{{background:transparent;color:#6b7280;padding-left:0}}
    .badge.market{{background:#1f2937;color:#9ca3af}}
    .game{{font-size:0.92em;font-weight:600;color:#f3f4f6;margin-bottom:2px}}
    .gtime{{font-size:0.67em;color:#6b7280;margin-bottom:10px}}

    .row{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
    .lbl{{font-size:0.63em;color:#6b7280;text-transform:uppercase;
          letter-spacing:0.07em;min-width:62px}}
    .pick{{font-size:0.88em;font-weight:700;color:#f9fafb}}
    .ent-pick{{color:#d1d5db;font-weight:600}}
    .odds{{font-size:0.72em;color:#6b7280;margin-left:auto}}

    .score-row .bar-wrap{{flex:1;height:4px;background:#222;border-radius:2px;overflow:hidden}}
    .bar{{height:100%;background:linear-gradient(90deg,#2563eb,#60a5fa);border-radius:2px}}
    .score-num{{font-size:0.68em;color:#9ca3af;min-width:30px;text-align:right}}

    .edge-badge{{font-size:0.67em;font-weight:600;padding:3px 9px;border-radius:6px}}
    .stake{{font-size:0.95em;margin-left:auto;letter-spacing:2px}}

    .sources{{font-size:0.62em;color:#4b5563;margin-top:4px}}
    .note{{font-size:0.63em;color:#9ca3af;margin-top:7px;font-style:italic;
           border-left:2px solid #1f2937;padding-left:8px;line-height:1.5}}

    .empty{{text-align:center;padding:44px 20px;background:#111;
            border-radius:12px;border:1px solid #1f1f1f}}
    .empty-icon{{font-size:2em;margin-bottom:10px}}
    .empty-title{{font-size:0.9em;font-weight:600;color:#9ca3af;margin-bottom:8px}}
    .empty-sub{{font-size:0.75em;color:#4b5563;line-height:1.7}}

    .section-header{{display:flex;align-items:baseline;gap:8px;
                     margin:20px 0 12px;border-top:1px solid #1a1a1a;padding-top:18px}}
    .section-title{{font-size:0.85em;font-weight:700;color:#9ca3af}}
    .section-sub{{font-size:0.65em;color:#4b5563}}

    .league-header{{display:flex;align-items:baseline;gap:8px;
                    margin:16px 0 8px;padding-top:14px;border-top:1px solid #1c1c1c}}
    .lh-league{{font-size:0.78em;font-weight:700;color:#6b7280;text-transform:uppercase;
                letter-spacing:0.08em}}
    .lh-day{{font-size:0.65em;color:#374151;margin-left:4px}}

    details{{margin-top:20px}}
    summary{{font-size:0.68em;color:#374151;cursor:pointer;
             text-transform:uppercase;letter-spacing:0.08em;user-select:none}}
    pre{{background:#0f0f0f;border:1px solid #1a1a1a;border-radius:8px;padding:12px;
         margin-top:8px;overflow-x:auto;font-size:0.6em;line-height:1.5;
         color:#4b5563;white-space:pre}}
  </style>
</head>
<body>
  <h1>Picks — {date}</h1>
  <div class="updated">Updated {now} · auto-refreshes hourly</div>
  {status}
  {recs_html}
  {ent_section}
  {log_section}
</body>
</html>"""


def main():
    db.init_db()
    print(f"Fetching soccer for {DATE}...")
    log = _fetch_log()
    soccer_aggs = _load_soccer_aggs()
    print(f"Soccer: {len(soccer_aggs)} aggregated picks")

    print("Fetching entertainment picks (MLB/NBA/NHL/NFL via OddsShark)...")
    ent_picks = _fetch_entertainment_picks()
    print(f"Entertainment: {len(ent_picks)} games")

    page = render_page(soccer_aggs, ent_picks, log)
    out = PROJECT / "picks.html"
    out.write_text(page)
    print(f"Written → {out}")


if __name__ == "__main__":
    main()
