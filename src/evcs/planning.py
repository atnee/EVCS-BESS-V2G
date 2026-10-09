"""EVCS planning parameters: from number of cars to chargers, stations and coverage.
Pure arithmetic on evcs.yaml `planning:`; no network or solver imports."""
from dataclasses import dataclass
import math
import numpy as np

FT_TO_M = .3048  # IEEE123 bus coordinates are in feet


@dataclass(frozen=True)
class PlanningParameters:
    evs: int                      # electric cars in the feeder area
    daily_km: float               # average distance per car per day
    kwh_per_km: float             # consumption at the wheel, including losses
    public_share: float           # fraction of the energy charged at public stations (rest at home)
    charger_kw: float             # power per charger
    chargers_per_station: int
    utilization: float            # average fraction of the day a charger is busy
    peak_hour_share: float        # fraction of the daily public energy in the busiest hour
    coverage_radius_m: float      # maximum distance from a car (demand bus) to a station
    min_spacing_m: float          # minimum distance between two stations
    hourly_shape: tuple = ()      # optional 24 weights for the daily profile

    def __post_init__(self):
        if self.evs < 0 or self.chargers_per_station < 1 or self.charger_kw <= 0:
            raise ValueError("Invalid fleet or station size")
        for name in ("public_share","utilization","peak_hour_share"):
            if not 0 < getattr(self,name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.coverage_radius_m <= 0 or self.min_spacing_m < 0:
            raise ValueError("Invalid distances")


def from_config(config):
    p = dict(config["planning"])
    p["hourly_shape"] = tuple(p.get("hourly_shape",()))
    return PlanningParameters(**p)


def charging_needs(p: PlanningParameters):
    """Chargers are sized by the busier of two limits: the peak hour and the daily utilization."""
    daily_kwh = p.evs*p.daily_km*p.kwh_per_km*p.public_share
    peak_kw = daily_kwh*p.peak_hour_share
    by_peak = math.ceil(peak_kw/p.charger_kw)
    by_energy = math.ceil(daily_kwh/(p.charger_kw*24*p.utilization))
    chargers = max(by_peak,by_energy,1 if p.evs else 0)
    stations = math.ceil(chargers/p.chargers_per_station)
    return dict(daily_public_kwh=daily_kwh,peak_kw=peak_kw,chargers_by_peak=by_peak,chargers_by_energy=by_energy,
                chargers=chargers,stations=stations,station_kw=p.chargers_per_station*p.charger_kw,
                installed_kw=stations*p.chargers_per_station*p.charger_kw)


def hourly_demand(p: PlanningParameters, steps=24):
    """Daily public energy spread over the hours with `hourly_shape` (flat if empty)."""
    shape = np.ones(steps) if not p.hourly_shape else np.asarray(p.hourly_shape,float)
    if shape.shape != (steps,) or (shape < 0).any() or shape.sum() == 0:
        raise ValueError("hourly_shape needs one non-negative weight per step")
    return charging_needs(p)["daily_public_kwh"]*shape/shape.sum()


def distances_m(xy_a, xy_b):
    """Straight-line distance matrix between two lists of IEEE (x, y) coordinates, in metres."""
    a, b = np.asarray(xy_a,float)*FT_TO_M, np.asarray(xy_b,float)*FT_TO_M
    return np.hypot(a[:,None,0]-b[None,:,0],a[:,None,1]-b[None,:,1])


def greedy_coverage(candidates, demand, p: PlanningParameters, stations, station_kw):
    """Maximal-covering heuristic: pick, one by one, the candidate that covers the most still-uncovered
    demand, among those that can host `station_kw` and respect `min_spacing_m` from earlier picks.

    candidates: DataFrame with bus, x, y, hosting_kw. demand: DataFrame with bus, x, y, weight.
    A screening baseline, not an optimal solution."""
    d = distances_m(candidates[["x","y"]],demand[["x","y"]])
    covers = d <= p.coverage_radius_m
    weight = demand.weight.to_numpy(float)
    uncovered = np.ones(len(demand),bool)
    eligible = candidates.hosting_kw.to_numpy() >= station_kw
    spacing = distances_m(candidates[["x","y"]],candidates[["x","y"]])
    chosen = []
    for _ in range(stations):
        gain = np.where(eligible,(covers & uncovered).astype(float)@weight,-1.)
        if gain.max() < 0:
            break
        best = int(gain.argmax())
        chosen.append(dict(bus=candidates.bus.iloc[best],covered_new=float(gain[best]),order=len(chosen)+1))
        uncovered &= ~covers[best]
        eligible &= spacing[best] >= p.min_spacing_m
    covered = 1-weight[uncovered].sum()/weight.sum() if weight.sum() else 0.
    return chosen,float(covered)
