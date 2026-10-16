"""Versioned, experimental conditions policy.

All product choices that affect a score or condition band live in Policy. Change
its defaults and version together; historical score rows retain their version.
"""
from dataclasses import dataclass
from math import isfinite

Curve = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Policy:
    version: str = "1"
    wave_weight: float = .60
    sea_weight: float = .30
    air_weight: float = .10
    wave_comfortable_below: float = .30
    wave_danger_at: float = .60
    sea_swimmable_at: float = 20
    sea_comfortable_above: float = 24
    air_comfortable_at: float = 29
    air_hot_above: float = 32
    danger_cap: int = 20
    wave_weight_reduction_at_max: float = .50
    released_share_to_sea: float = .75
    # Piecewise-linear comfort estimates, not validated physical measures.
    wave_curve: Curve = ((0, 1), (.3, .85), (.6, .2), (1, 0))
    sea_curve: Curve = ((0, 0), (16, .1), (20, .5), (24, .9), (28, 1), (40, 1))
    air_curve: Curve = ((-20, 0), (20, .2), (29, 1), (32, 1), (40, .6), (50, 0))

    def __post_init__(self):
        weights = (self.wave_weight, self.sea_weight, self.air_weight)
        if any(not isfinite(weight) or weight < 0 for weight in weights) or abs(sum(weights) - 1) > 1e-9:
            raise ValueError("weights must be nonnegative and sum to one")
        if not self.version.strip():
            raise ValueError("policy version is required")
        if not (0 < self.wave_comfortable_below < self.wave_danger_at <= 1):
            raise ValueError("invalid wave bands")
        if not (self.sea_swimmable_at < self.sea_comfortable_above and self.air_comfortable_at < self.air_hot_above):
            raise ValueError("invalid temperature bands")
        if not (0 <= self.danger_cap <= 100 and 0 <= self.wave_weight_reduction_at_max <= 1 and 0 <= self.released_share_to_sea <= 1):
            raise ValueError("invalid score limits")
        for curve in (self.wave_curve, self.sea_curve, self.air_curve):
            if len(curve) < 2 or any(not isfinite(x) or not isfinite(y) or not 0 <= y <= 1 for x, y in curve):
                raise ValueError("invalid comfort curve")
            if any(curve[i][0] >= curve[i + 1][0] for i in range(len(curve) - 1)):
                raise ValueError("comfort curve x values must increase")
        if self.wave_curve[0][0] > 0 or self.wave_curve[-1][0] < 1:
            raise ValueError("wave curve must cover 0–1")


POLICY = Policy()


def interpolate(value: float, points: Curve) -> float:
    if not isfinite(value):
        raise ValueError("input must be finite")
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            return y0 + (value - x0) * (y1 - y0) / (x1 - x0)
    return points[-1][1]


def bands(wave: float | None, sea: float | None, air: float | None, policy: Policy = POLICY) -> dict:
    return {
        "wave": None if wave is None else "comfortable" if wave < policy.wave_comfortable_below else "fun" if wave < policy.wave_danger_at else "dangerous",
        "sea": None if sea is None else "cold" if sea < policy.sea_swimmable_at else "swimmable" if sea <= policy.sea_comfortable_above else "comfortable",
        "air": None if air is None else "chilly" if air < policy.air_comfortable_at else "comfortable" if air <= policy.air_hot_above else "hot",
    }


def score(wave: float, sea: float, air: float, policy: Policy = POLICY) -> int:
    if not isfinite(wave) or not 0 <= wave <= 1:
        raise ValueError("waviness outside 0–1")
    wave_weight = policy.wave_weight * (1 - policy.wave_weight_reduction_at_max * wave)
    released = policy.wave_weight - wave_weight
    sea_weight = policy.sea_weight + released * policy.released_share_to_sea
    air_weight = policy.air_weight + released * (1 - policy.released_share_to_sea)
    result = round(100 * (
        wave_weight * interpolate(wave, policy.wave_curve)
        + sea_weight * interpolate(sea, policy.sea_curve)
        + air_weight * interpolate(air, policy.air_curve)
    ))
    return min(result, policy.danger_cap) if wave >= policy.wave_danger_at else result
