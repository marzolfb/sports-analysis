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
    if a.game_time.hour == 0 and a.game_time.minute == 0:
        gtime_html = f"<div class='gtime'>{a.game_time.strftime('%-m/%-d')}</div>"
    else:
        utc_iso = a.game_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        gtime_html = f"<div class='gtime' data-utc='{utc_iso}'>{utc_iso}</div>"
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
    {gtime_html}
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

    rec_count = sum(1 for a in soccer_aggs if a.recommended_stake_level > 0)
    if rec_count:
        status = f'<div class="status ok">{rec_count} edge pick{"s" if rec_count != 1 else ""} today</div>'
    elif soccer_aggs:
        status = '<div class="status wait">No edge picks — sources loaded but nothing in the sweet spot</div>'
    else:
        status = '<div class="status wait">No data yet — odds and xG previews typically appear 6–12 h before kickoff</div>'

    # ── Soccer: one section per league ───────────────────────────────────────
    soccer_by_league: dict[str, list[AggregatedPick]] = {}
    for a in soccer_aggs:
        soccer_by_league.setdefault(a.league or a.sport.value, []).append(a)

    soccer_parts = []
    for league_name in sorted(soccer_by_league):
        league_picks = sorted(soccer_by_league[league_name], key=lambda a: (
            a.game_time.strftime("%Y-%m-%d"),
            -_EDGE_PRIORITY.get(a.edge_flag, 0),
            -a.composite_score,
        ))
        cards = "\n".join(render_card(a) for a in league_picks)
        soccer_parts.append(
            f'<div class="sport-section">'
            f'<div class="shdr soccer-shdr">{_h.escape(league_name)}</div>'
            f'{cards}'
            f'</div>'
        )

    # ── Entertainment: one section per sport ─────────────────────────────────
    ent_by_sport: dict[str, list[Pick]] = {}
    for p in ent_picks:
        ent_by_sport.setdefault(p.sport.value, []).append(p)

    ent_parts = []
    for sport_name in sorted(ent_by_sport):
        cards = "\n".join(render_entertainment_card(p) for p in ent_by_sport[sport_name])
        ent_parts.append(
            f'<div class="sport-section">'
            f'<div class="shdr ent-shdr">{_h.escape(sport_name)}'
            f'<span class="shdr-sub"> · entertainment only</span></div>'
            f'{cards}'
            f'</div>'
        )

    if not soccer_parts and not ent_parts:
        main_html = """<div class="empty">
  <div class="empty-icon">⏳</div>
  <div class="empty-title">Waiting for data</div>
  <div class="empty-sub">Odds and xG previews typically appear 6–12 hours before kickoff.<br>This page refreshes every hour.</div>
</div>"""
    else:
        soccer_html = "\n".join(soccer_parts)
        ent_html = (
            '<div class="ent-divider">🎰 Entertainment</div>\n' + "\n".join(ent_parts)
            if ent_parts else ""
        )
        main_html = "\n".join(filter(None, [soccer_html, ent_html]))

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

    .sport-section{{margin-bottom:24px}}
    .shdr{{font-size:0.8em;font-weight:700;text-transform:uppercase;
           letter-spacing:0.1em;padding:7px 12px;border-radius:8px;
           margin-bottom:10px}}
    .soccer-shdr{{background:#0f1f3d;color:#60a5fa;border:1px solid #1e3a5f}}
    .ent-shdr{{background:#111;color:#6b7280;border:1px solid #1f1f1f}}
    .shdr-sub{{font-size:0.75em;font-weight:400;letter-spacing:0.02em;color:#4b5563}}

    .ent-divider{{font-size:0.72em;font-weight:600;color:#4b5563;
                  text-transform:uppercase;letter-spacing:0.1em;
                  margin:28px 0 16px;padding-top:18px;
                  border-top:1px solid #1a1a1a}}

    .card{{background:#141414;border:1px solid #222;border-radius:12px;
           padding:14px;margin-bottom:10px}}
    .ent-card{{background:#0f0f0f;border-color:#1a1a1a}}

    .card-top{{display:flex;justify-content:space-between;margin-bottom:8px}}
    .badge{{font-size:0.62em;font-weight:700;text-transform:uppercase;
            letter-spacing:0.07em;padding:2px 7px;border-radius:4px}}
    .badge.league{{background:transparent;color:#4b5563;padding-left:0}}
    .badge.market{{background:#1f2937;color:#9ca3af}}
    .game{{font-size:0.92em;font-weight:600;color:#f3f4f6;margin-bottom:2px}}
    .gtime{{font-size:0.67em;color:#6b7280;margin-bottom:10px}}

    .row{{display:flex;align-items:center;gap:8px;margin-bottom:8px}}
    .lbl{{font-size:0.63em;color:#6b7280;text-transform:uppercase;
          letter-spacing:0.07em;min-width:62px}}
    .pick{{font-size:0.88em;font-weight:700;color:#f9fafb}}
    .ent-pick{{color:#d1d5db;font-weight:600}}

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
  {main_html}
  {log_section}
  <script>
    document.querySelectorAll('.gtime[data-utc]').forEach(function(el) {{
      try {{
        var d = new Date(el.dataset.utc);
        el.textContent = d.toLocaleDateString('en-US',{{month:'numeric',day:'numeric'}}) +
          ' ' + d.toLocaleTimeString('en-US',{{hour:'numeric',minute:'2-digit'}});
      }} catch(e) {{}}
    }});
  </script>
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
