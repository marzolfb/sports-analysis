from abc import ABC, abstractmethod
from datetime import datetime

from models import Pick, Sport


class BaseSource(ABC):
    name: str = ""

    @abstractmethod
    def fetch_picks(self, sport: Sport, date: datetime) -> list[Pick]:
        """Fetch picks for a given sport and date. Returns empty list on failure."""
        ...

    def _american_to_implied(self, odds: str) -> float | None:
        """Convert American odds string (e.g. '-110', '+150') to implied probability."""
        try:
            n = int(odds.replace("+", ""))
            if n < 0:
                return abs(n) / (abs(n) + 100)
            else:
                return 100 / (n + 100)
        except (ValueError, TypeError):
            return None
