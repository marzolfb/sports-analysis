"""
Simple file-based daily cache for agent output.

Keyed by (source, sport, date). Cache is only valid within the same calendar day —
a new day means a new run. This prevents re-spending API credits on repeated
fetch runs for the same sport on the same day.

Cache files: data/cache/{source}_{sport}_{YYYY-MM-DD}.json
"""

import json
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "data" / "cache"


def _path(source: str, sport: str, date: datetime) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{source}_{sport}_{date.strftime('%Y-%m-%d')}.json"


def get(source: str, sport: str, date: datetime) -> str | None:
    p = _path(source, sport, date)
    if p.exists():
        return p.read_text()
    return None


def set(source: str, sport: str, date: datetime, data: str):
    _path(source, sport, date).write_text(data)


def invalidate(source: str, sport: str, date: datetime):
    p = _path(source, sport, date)
    if p.exists():
        p.unlink()
        print(f"[cache] cleared {p.name}")
