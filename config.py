from models import Sport

# Leagues the user actively bets on.
# xgscore_slug: URL slug used on xgscore.io (e.g. /epl/...)
# clubelo_country/level: filters for Club ELO CSV (None = not covered by Club ELO)
ACTIVE_SOCCER_LEAGUES: dict[str, dict] = {
    "EPL":        {"xgscore_slug": "epl",        "clubelo_country": "ENG", "clubelo_level": 1},
    "La Liga":    {"xgscore_slug": "la-liga",     "clubelo_country": "ESP", "clubelo_level": 1},
    "Bundesliga": {"xgscore_slug": "bundesliga",  "clubelo_country": "GER", "clubelo_level": 1},
    "Serie A":    {"xgscore_slug": "serie-a",     "clubelo_country": "ITA", "clubelo_level": 1},
    "Ligue 1":    {"xgscore_slug": "ligue-1",     "clubelo_country": "FRA", "clubelo_level": 1},
    "MLS":        {"xgscore_slug": "mls",         "clubelo_country": None,  "clubelo_level": None},
    "Liga MX":    {"xgscore_slug": "liga-mx",     "clubelo_country": None,  "clubelo_level": None},
}

# Per-source weights. Adjust as you observe accuracy over time.
# Kalshi is weighted highest for soccer: real-money crowd consensus is the
# best-calibrated signal available. xgscore is soccer-specific xG model.
SOURCE_WEIGHTS: dict[str, float] = {
    "kalshi":        0.40,   # real-money prediction market — highest signal
    "xgscore":       0.35,   # soccer-specific xG model
    "oddsshark":     0.15,
    "actionnetwork": 0.05,
    "bettingpros":   0.05,
}

SOCCER_SOURCES    = {"kalshi", "xgscore"}
NON_SOCCER_SOURCES = {"oddsshark", "actionnetwork", "bettingpros"}

def source_weights_for_sport(sport: Sport) -> dict[str, float]:
    active_keys = SOCCER_SOURCES if sport == Sport.SOCCER else NON_SOCCER_SOURCES
    active = {k: v for k, v in SOURCE_WEIGHTS.items() if k in active_keys}
    total = sum(active.values())
    return {k: v / total for k, v in active.items()}


# Additive score delta per market type — derived from Kalshi history
MARKET_TYPE_SCORE_DELTA: dict[str, float] = {
    "GAME":   +0.05,   # only market type with positive P&L (+$4.44)
    "SPREAD":  0.00,   # roughly neutral (-$13 over 647 trades)
    "TOTAL":  -0.15,   # worst performer (-$46.63 over 400 trades)
}

# Kalshi-derived edge zones by implied probability
# Format: (low, high, EdgeFlag label)
# <15¢: +19.8% ROI (11 trades)
# 15-25¢: +37.6% ROI (48 trades) ← strongest historical edge
# 45-55¢: +3.8% ROI (692 trades) ← slight edge near coinflip
# All other ranges: negative ROI
EDGE_ZONES = [
    (0.10, 0.15, "STRONG_EDGE"),
    (0.15, 0.25, "STRONG_EDGE"),
    (0.45, 0.55, "SLIGHT_EDGE"),
]
FADE_ZONES = [
    (0.25, 0.45),   # -12% to -28% ROI
    (0.55, 1.00),   # -12% to -58% ROI — favorites are traps
]

# Recommended stake levels by edge flag — deliberately low to fight overconfidence bias.
# Kalshi data shows: larger bets → worse outcomes (top-25% bets lost $73.87 total)
STAKE_BY_EDGE: dict[str, int] = {
    "STRONG_EDGE": 3,
    "SLIGHT_EDGE": 2,
    "NEUTRAL":     1,
    "FADE":        0,
}
