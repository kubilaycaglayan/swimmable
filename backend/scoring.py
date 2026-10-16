"""Versioned, experimental comfort policy. Change constants and version together."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Policy:
    version: str = "1"
    wave_weight: float = .60
    sea_weight: float = .30
    air_weight: float = .10
    danger_at: float = .60
    danger_cap: int = 20

    def __post_init__(self):
        if not isfinite(self.wave_weight + self.sea_weight + self.air_weight) or abs(self.wave_weight + self.sea_weight + self.air_weight - 1) > 1e-9:
            raise ValueError("weights must sum to one")
        if not 0 < self.danger_at <= 1 or not 0 <= self.danger_cap <= 100:
            raise ValueError("invalid danger settings")


POLICY = Policy()


def interpolate(value: float, points: tuple[tuple[float, float], ...]) -> float:
    if not isfinite(value):
        raise ValueError("input must be finite")
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            return y0 + (value - x0) * (y1 - y0) / (x1 - x0)
    return points[-1][1]


# Piecewise-linear comfort estimates, not validated physical safety measures.
# Wave comfort falls faster above 0.3. Sea 20 and 24 delimit the requested bands;
# air reaches peak comfort in 29–32 and gently falls above 32 for heat exposure.
WAVE_CURVE = ((0, 1), (.3, .85), (.6, .2), (1, 0))
SEA_CURVE = ((0, 0), (16, .1), (20, .5), (24, .9), (28, 1), (40, 1))
AIR_CURVE = ((-20, 0), (20, .2), (29, 1), (32, 1), (40, .6), (50, 0))


def bands(wave: float | None, sea: float | None, air: float | None) -> dict:
    return {
        "wave": None if wave is None else "comfortable" if wave < .3 else "fun" if wave < .6 else "dangerous",
        "sea": None if sea is None else "cold" if sea < 20 else "swimmable" if sea <= 24 else "comfortable",
        "air": None if air is None else "chilly" if air < 29 else "comfortable" if air <= 32 else "hot",
    }


def score(wave: float, sea: float, air: float, policy: Policy = POLICY) -> int:
    if not 0 <= wave <= 1:
        raise ValueError("waviness outside 0–1")
    # Wave share remains 60% initially, then decreases as waviness increases.
    wave_weight = policy.wave_weight * (1 - .5 * wave)
    released = policy.wave_weight - wave_weight
    sea_weight = policy.sea_weight + released * .75
    air_weight = policy.air_weight + released * .25
    result = round(100 * (wave_weight * interpolate(wave, WAVE_CURVE) + sea_weight * interpolate(sea, SEA_CURVE) + air_weight * interpolate(air, AIR_CURVE)))
    return min(result, policy.danger_cap) if wave >= policy.danger_at else result
