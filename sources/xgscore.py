"""
xGScore source — soccer only.

Strategy:
  1. Fetch the league page on xgscore.io (e.g. /epl/) to find match preview links.
  2. Filter to matches whose date string matches the target date.
  3. For each preview page, extract teams from JSON-LD and xG from page text.
  4. Emit a pick for matches where |home_xg - away_xg| >= XG_EDGE_THRESHOLD.
  5. Optionally augment with Club ELO win probability for European leagues.

The cache key is "{source}_{sport}_{league}_{date}" so each league is cached separately.
"""

import json
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

import cache
from config import ACTIVE_SOCCER_LEAGUES
from models import MarketType, Pick, PickSide, Sport
from sources.base import BaseSource

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

BASE_URL = "https://xgscore.io"
XG_EDGE_THRESHOLD = 0.30  # minimum xG difference to emit a pick


class XGScoreSource(BaseSource):
    name = "xgscore"

    def fetch_picks(self, sport: Sport, date: datetime,
                    league: Optional[str] = None) -> list[Pick]:
        if sport != Sport.SOCCER:
            return []

        if league:
            leagues_to_fetch = {league: ACTIVE_SOCCER_LEAGUES[league]} \
                if league in ACTIVE_SOCCER_LEAGUES else {}
        else:
            leagues_to_fetch = ACTIVE_SOCCER_LEAGUES

        all_picks: list[Pick] = []
        for league_name, league_cfg in leagues_to_fetch.items():
            picks = self._fetch_league(league_name, league_cfg, date)
            all_picks.extend(picks)

        return all_picks

    def _fetch_league(self, league_name: str, league_cfg: dict,
                      date: datetime) -> list[Pick]:
        slug = league_cfg["xgscore_slug"]
        date_str = date.strftime("%Y-%m-%d")
        cache_key = f"{league_name.replace(' ', '_')}"

        cached = cache.get(self.name, f"SOCCER_{cache_key}", date)
        if cached:
            print(f"[xgscore] using cached data for {league_name} {date_str}")
            return self._parse_cached(cached, league_name, date)

        print(f"[xgscore] fetching {league_name} ({slug}) for {date_str}...")
        preview_urls = self._find_preview_links(slug, date_str)

        if not preview_urls:
            print(f"[xgscore] no matches found for {league_name} on {date_str}")
            cache.set(self.name, f"SOCCER_{cache_key}", date, "[]")
            return []

        raw_matches = []
        for url in preview_urls:
            match_data = self._fetch_preview(url, date, league_name)
            if match_data:
                raw_matches.append(match_data)

        cache.set(self.name, f"SOCCER_{cache_key}", date, json.dumps(raw_matches))
        return self._build_picks(raw_matches, date)

    def _find_preview_links(self, slug: str, date_str: str) -> list[str]:
        url = f"{BASE_URL}/{slug}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"[xgscore] failed to fetch league page /{slug}/: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        preview_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(f"/{slug}/") and href.endswith("/preview"):
                full_url = BASE_URL + href if href.startswith("/") else href
                # Check if the date appears near this link (in surrounding text)
                # xgscore pages show date context around match cards
                parent_text = (a.parent.get_text() if a.parent else "") + href
                if date_str in parent_text or date_str.replace("-", "") in parent_text:
                    if full_url not in preview_links:
                        preview_links.append(full_url)

        if not preview_links:
            # Looser fallback: collect all preview links and filter by date on the preview page
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith(f"/{slug}/") and href.endswith("/preview"):
                    full_url = BASE_URL + href if href.startswith("/") else href
                    if full_url not in preview_links:
                        preview_links.append(full_url)

        return preview_links

    def _fetch_preview(self, url: str, date: datetime,
                       league_name: str) -> dict | None:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"[xgscore] failed to fetch {url}: {e}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract team names and game time from JSON-LD SportsEvent schema
        home_team = away_team = None
        game_time_str = None
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "SportsEvent"), {})
                if data.get("@type") == "SportsEvent":
                    # Schema updated: homeTeam/awayTeam replaced competitor[]
                    home_team = (data.get("homeTeam") or {}).get("name")
                    away_team = (data.get("awayTeam") or {}).get("name")
                    # Legacy format fallback
                    if not home_team or not away_team:
                        for c in data.get("competitor", []):
                            role = c.get("disambiguatingDescription", "").lower()
                            if "home" in role:
                                home_team = c.get("name")
                            elif "away" in role:
                                away_team = c.get("name")
                    game_time_str = data.get("startDate")
                    break
            except Exception:
                continue

        if not home_team or not away_team:
            # Fallback: parse from URL slug (e.g. /epl/arsenal-chelsea/preview)
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2:
                matchup = parts[-2]
                teams = matchup.split("-")
                mid = len(teams) // 2
                home_team = " ".join(t.title() for t in teams[:mid]) if mid else matchup
                away_team = " ".join(t.title() for t in teams[mid:]) if mid else matchup

        # Date check: accept ±1 day to handle UTC vs local timezone offset
        if game_time_str:
            from datetime import timedelta
            target_dates = {
                (date + timedelta(d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)
            }
            if not any(d in game_time_str for d in target_dates):
                return None  # clearly a different date — skip

        # Extract xG predictions from page text.
        # xgscore renders concatenated values e.g. "Match Score Prediction 1.81.2"
        # where 1.8 = home xG and 1.2 = away xG.
        page_text = soup.get_text()
        home_xg = away_xg = None
        m = re.search(r'Match Score Prediction\s+(\d+\.\d+)(\d+\.\d+)', page_text)
        if m:
            home_xg = float(m.group(1))
            away_xg = float(m.group(2))

        if home_xg is None or away_xg is None:
            print(f"[xgscore] could not extract xG from {url}")
            return None

        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_xg": home_xg,
            "away_xg": away_xg,
            "league": league_name,
            "game_time_str": game_time_str,
        }

    def _parse_cached(self, cached_str: str, league_name: str,
                      date: datetime) -> list[Pick]:
        try:
            raw_matches = json.loads(cached_str)
        except Exception:
            return []
        return self._build_picks(raw_matches, date)

    def _build_picks(self, raw_matches: list[dict], date: datetime) -> list[Pick]:
        from sources.clubelo import ClubElo
        try:
            elo = ClubElo.for_date(date)
        except Exception:
            elo = None

        picks = []
        for m in raw_matches:
            pick = self._to_pick(m, date, elo)
            if pick:
                picks.append(pick)

        print(f"[xgscore] {len(picks)} picks from {len(raw_matches)} matches")
        return picks

    def _to_pick(self, m: dict, date: datetime, elo) -> Pick | None:
        try:
            home = m["home_team"]
            away = m["away_team"]
            home_xg = float(m["home_xg"])
            away_xg = float(m["away_xg"])
            league_name = m.get("league")

            game_time = date
            game_time_str = m.get("game_time_str")
            if game_time_str:
                try:
                    game_time = datetime.fromisoformat(
                        game_time_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    pass

            xg_diff = home_xg - away_xg
            if abs(xg_diff) < XG_EDGE_THRESHOLD:
                return None

            side = PickSide.HOME if xg_diff > 0 else PickSide.AWAY
            team = home if side == PickSide.HOME else away
            confidence = min(1.0, abs(xg_diff) / 1.5)

            notes = f"xG: {home_xg:.2f} home / {away_xg:.2f} away (diff {xg_diff:+.2f})"

            # Augment with Club ELO if available
            if elo:
                elo_prob = elo.match_probability(home, away)
                if elo_prob is not None:
                    elo_fav = "home" if elo_prob >= 0.50 else "away"
                    notes += f" | ELO home-win prob: {elo_prob:.0%} ({elo_fav} favoured)"

            return Pick(
                source=self.name,
                sport=Sport.SOCCER,
                home_team=home,
                away_team=away,
                game_time=game_time,
                market_type=MarketType.GAME,
                pick_side=side,
                pick_team=team,
                confidence=confidence,
                notes=notes,
                fetched_at=datetime.utcnow(),
                league=league_name,
            )
        except Exception as e:
            print(f"[xgscore] skipping match: {e}")
            return None
