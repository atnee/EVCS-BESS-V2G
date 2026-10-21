"""Reproducible V2G fleet sampled from the `fleet:` profiles of configs/v2g.yaml.

Each profile gives a share of the fleet, a battery distribution, the daily trip energy and
integer-hour windows for arrival and departure. A departure earlier than the arrival is an
overnight session (connected from the arrival to midnight and from midnight to the departure)."""
import numpy as np
from core.assets import Battery
from v2g.model import Vehicle

PROFILE_FIELDS = ("share","battery_kwh","battery_std_kwh","daily_km","kwh_per_km",
                  "arrival_h","departure_h","arrival_soc_mean","arrival_soc_std","departure_soc")


def profile_counts(shares, n):
    """Split n vehicles by share with the largest-remainder rule, so the counts sum to n."""
    shares = np.asarray(shares,float)
    if n < 1 or (shares < 0).any() or not np.isclose(shares.sum(),1.):
        raise ValueError("Fleet needs n_vehicles >= 1 and non-negative shares summing to 1")
    raw = shares*n
    counts = np.floor(raw).astype(int)
    counts[np.argsort(counts-raw)[:n-counts.sum()]] += 1
    return counts


def hour_index(window, grid):
    """Inclusive integer-hour window [first, last] -> candidate step indices."""
    first,last = window
    if not 0 <= first <= last <= grid.steps*grid.dt_h:
        raise ValueError(f"Window {window} must lie inside the horizon")
    return [int(round(h/grid.dt_h)) for h in range(int(first),int(last)+1)]


def generate_fleet(fleet, grid):
    """Sample the vehicles of every profile; same seed -> same fleet."""
    rng = np.random.default_rng(fleet["seed"])
    profiles = fleet["profiles"]
    for name,p in profiles.items():
        missing = set(PROFILE_FIELDS)-set(p)
        if missing:
            raise ValueError(f"Fleet profile {name!r} lacks {sorted(missing)}")
    counts = profile_counts([p["share"] for p in profiles.values()],fleet["n_vehicles"])
    vehicles = []
    for (name,p),count in zip(profiles.items(),counts):
        arrivals,departures = hour_index(p["arrival_h"],grid),hour_index(p["departure_h"],grid)
        if set(arrivals) & set(departures):
            raise ValueError(f"Fleet profile {name!r}: arrival and departure windows overlap")
        for k in range(count):
            capacity = max(fleet["battery_min_kwh"],rng.normal(p["battery_kwh"],p["battery_std_kwh"])) \
                if p["battery_std_kwh"] else p["battery_kwh"]
            soc = float(np.clip(rng.normal(p["arrival_soc_mean"],p["arrival_soc_std"]),fleet["soc_min"],fleet["soc_max"]))
            arrival,departure = int(rng.choice(arrivals)),int(rng.choice(departures))
            battery = Battery(capacity_kwh=float(capacity),power_kw=fleet["power_kw"],soc_initial=soc,
                              soc_min=fleet["soc_min"],soc_max=fleet["soc_max"],
                              eta_charge=fleet["eta_charge"],eta_discharge=fleet["eta_discharge"])
            vehicles.append(Vehicle(f"{name}-{k:03d}",battery,arrival,departure,
                                    p["daily_km"]*p["kwh_per_km"],p["departure_soc"]))
    return vehicles
