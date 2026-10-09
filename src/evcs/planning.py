"""EVCS planning: fleet, station types (AC eletroposto / DC hub) and minute-level charging sessions.

Pure numpy/pandas on evcs.yaml `planning:`; no network or solver imports.
A session: a car arrives at a sampled minute with a sampled SOC, picks a station type, waits for a
free charger (FIFO, gives up after `max_wait_min`) and charges to `target_soc` at
min(charger power, car limit). Grid power = charging power / charger efficiency.
"""
from dataclasses import dataclass, field
import math
import numpy as np
import pandas as pd

FT_TO_M = .3048  # IEEE123 and IEEE8500 bus coordinates are in feet
MINUTES = 1440


@dataclass(frozen=True)
class Fleet:
    evs: int                    # electric cars in the feeder area
    battery_kwh: float          # average usable battery
    battery_std_kwh: float      # spread of battery sizes (0 = all equal)
    daily_km: float             # km per car per day
    kwh_per_km: float
    public_share: float         # fraction of the energy charged at public stations (rest at home)
    arrival_soc_mean: float
    arrival_soc_std: float
    max_ac_kw: float            # on-board charger limit
    max_dc_kw: float            # car DC acceptance limit

    def __post_init__(self):
        if self.evs < 0 or self.battery_kwh <= 0 or self.battery_std_kwh < 0 or self.daily_km < 0 or self.kwh_per_km <= 0:
            raise ValueError("Invalid fleet")
        if not 0 < self.public_share <= 1 or not 0 < self.arrival_soc_mean < 1 or self.arrival_soc_std < 0:
            raise ValueError("Invalid fleet shares/SOC")
        if self.max_ac_kw <= 0 or self.max_dc_kw <= 0:
            raise ValueError("Invalid car charging limits")

    @property
    def daily_public_kwh(self):
        return self.evs*self.daily_km*self.kwh_per_km*self.public_share


@dataclass(frozen=True)
class StationType:
    key: str                    # "ac" or "dc"
    name: str                   # label: eletroposto / hub
    charger_kw: float
    chargers_per_site: int
    share: float                # fraction of public energy delivered by this type
    target_soc: float
    max_wait_min: float
    coverage_radius_m: float
    min_spacing_m: float
    arrival_shape: tuple        # 24 hourly weights for arrival times
    capex_usd: float = 0.       # installed cost of one site (chargers + connection), used by the integration KPIs

    def __post_init__(self):
        if self.key not in ("ac","dc") or self.charger_kw <= 0 or self.chargers_per_site < 1 or self.capex_usd < 0:
            raise ValueError("Invalid station type")
        if not 0 <= self.share <= 1 or not 0 < self.target_soc <= 1 or self.max_wait_min < 0:
            raise ValueError("Invalid station share/target/wait")
        shape = np.asarray(self.arrival_shape,float)
        if shape.shape != (24,) or (shape < 0).any() or shape.sum() == 0:
            raise ValueError("arrival_shape needs 24 non-negative weights")

    @property
    def site_kw(self):
        return self.charger_kw*self.chargers_per_site


@dataclass(frozen=True)
class PlanningParameters:
    fleet: Fleet
    types: dict = field(default_factory=dict)   # key -> StationType
    seed: int = 42
    resolution_min: int = 15
    charger_efficiency: float = .95
    sizing_quantile: float = .95                # chargers = this quantile of unconstrained concurrency
    candidate_spacing_m: float = 0.             # screening candidates at least this far apart (0 = all)

    def __post_init__(self):
        if abs(sum(t.share for t in self.types.values())-1) > 1e-9:
            raise ValueError("Station type shares must add up to 1")
        if (MINUTES % self.resolution_min or not 0 < self.charger_efficiency <= 1 or not 0 < self.sizing_quantile <= 1
                or self.candidate_spacing_m < 0):
            raise ValueError("Invalid resolution/efficiency/quantile")

    def car_limit(self, key):
        return self.fleet.max_ac_kw if key == "ac" else self.fleet.max_dc_kw


def from_config(config):
    p = dict(config["planning"])
    fleet = Fleet(**p.pop("fleet"))
    types = {k: StationType(key=k,**{**v,"arrival_shape": tuple(v["arrival_shape"])})
             for k,v in p.pop("station_types").items()}
    return PlanningParameters(fleet=fleet,types=types,**p)


def with_fleet(p: PlanningParameters, evs):
    from dataclasses import replace
    return replace(p,fleet=replace(p.fleet,evs=int(evs)))


def generate_sessions(p: PlanningParameters):
    """Charging requests for one representative day (no charger limits yet)."""
    rng = np.random.default_rng(p.seed)
    f, rows = p.fleet, []
    for key,t in p.types.items():
        energy_goal, energy = f.daily_public_kwh*t.share, 0.
        weights = np.asarray(t.arrival_shape,float)/sum(t.arrival_shape)
        while energy < energy_goal-1e-9:
            battery = max(10.,rng.normal(f.battery_kwh,f.battery_std_kwh)) if f.battery_std_kwh else f.battery_kwh
            soc = float(np.clip(rng.normal(f.arrival_soc_mean,f.arrival_soc_std),.05,t.target_soc-.05))
            need = min(battery*(t.target_soc-soc),energy_goal-energy)  # last session tops up the daily total
            power = min(t.charger_kw,p.car_limit(key))
            arrival = int(rng.choice(24,p=weights))*60+int(rng.integers(60))
            rows.append(dict(type=key,arrival_min=arrival,battery_kwh=battery,soc_arrival=soc,energy_kwh=need,
                             power_kw=power,grid_kw=power/p.charger_efficiency,
                             duration_min=max(1,math.ceil(need/power*60))))
            energy += need
    sessions = pd.DataFrame(rows,columns=["type","arrival_min","battery_kwh","soc_arrival","energy_kwh","power_kw",
                                          "grid_kw","duration_min"])
    return sessions.sort_values("arrival_min",kind="stable").reset_index(drop=True)


def _occupancy(start, duration):
    """Minute-by-minute count of active sessions over a periodic day."""
    count = np.zeros(MINUTES)
    for s,d in zip(start,duration):
        idx = (np.arange(int(d))+int(s)) % MINUTES
        np.add.at(count,idx,1)
    return count


def size_sites(p: PlanningParameters, sessions=None):
    """Chargers per type from the unconstrained concurrency, then whole sites."""
    sessions = generate_sessions(p) if sessions is None else sessions
    out = {}
    for key,t in p.types.items():
        s = sessions[sessions.type==key]
        concurrency = _occupancy(s.arrival_min,s.duration_min)
        chargers = int(math.ceil(np.quantile(concurrency,p.sizing_quantile))) if len(s) else 0
        chargers = max(chargers,1 if len(s) else 0)
        sites = math.ceil(chargers/t.chargers_per_site)
        out[key] = dict(name=t.name,sessions=len(s),energy_kwh=float(s.energy_kwh.sum()),
                        peak_concurrency=int(concurrency.max()) if len(s) else 0,chargers=chargers,sites=sites,
                        site_kw=t.site_kw,installed_kw=sites*t.site_kw)
    return out


def simulate(p: PlanningParameters, sites, sessions=None):
    """Assign sessions to sites of their type and queue them on the chargers.

    sites: {bus: type_key}. Returns (sessions with site/start/wait/served, minute grid power per site).
    Sessions are spread over the sites of their type in turn; the day is periodic (charging past
    midnight wraps to the early hours)."""
    rng = np.random.default_rng(p.seed+1)
    sessions = (generate_sessions(p) if sessions is None else sessions).copy()
    sessions["site"], sessions["start_min"], sessions["wait_min"], sessions["served"] = None, np.nan, np.nan, False
    power = {bus: np.zeros(MINUTES) for bus in sites}
    for key,t in p.types.items():
        buses = [b for b,k in sites.items() if k == key]
        idx = sessions.index[sessions.type==key]
        if not buses:
            continue
        sessions.loc[idx,"site"] = rng.choice(buses,size=len(idx))
        for bus in buses:
            free = np.zeros(t.chargers_per_site)
            for i in sessions.index[(sessions.type==key) & (sessions.site==bus)]:
                row = sessions.loc[i]
                charger = int(free.argmin())
                start = max(row.arrival_min,free[charger])
                if start-row.arrival_min > t.max_wait_min:
                    continue  # gave up: unserved
                free[charger] = start+row.duration_min
                sessions.loc[i,["start_min","wait_min","served"]] = [start,start-row.arrival_min,True]
                minutes = (np.arange(int(row.duration_min))+int(start)) % MINUTES
                # Last minute charges only the remaining energy.
                kw = np.full(len(minutes),row.grid_kw)
                kw[-1] = row.grid_kw*(row.energy_kwh/row.power_kw*60-(len(minutes)-1))
                np.add.at(power[bus],minutes,kw)
    return sessions, power


def resample(minute_kw, resolution_min):
    """Average power per block (energy-preserving)."""
    return np.asarray(minute_kw,float).reshape(-1,resolution_min).mean(axis=1)


def distances_m(xy_a, xy_b):
    """Straight-line distance matrix between two lists of IEEE (x, y) coordinates, in metres."""
    a, b = np.asarray(xy_a,float)*FT_TO_M, np.asarray(xy_b,float)*FT_TO_M
    return np.hypot(a[:,None,0]-b[None,:,0],a[:,None,1]-b[None,:,1])


def thin_candidates(candidates, demand, spacing_m):
    """Keep candidates at least `spacing_m` apart, preferring those with more demand within that
    distance; spacing 0 keeps all. Limits the hosting sweep on large feeders."""
    if spacing_m <= 0 or candidates.empty:
        return candidates.reset_index(drop=True)
    xy = candidates[["x","y"]]
    score = (distances_m(xy,demand[["x","y"]]) <= spacing_m).astype(float)@demand.weight.to_numpy(float)
    spacing = distances_m(xy,xy)
    kept = []
    for i in np.argsort(-score,kind="stable"):
        if all(spacing[i,j] >= spacing_m for j in kept):
            kept.append(i)
    return candidates.iloc[sorted(kept)].reset_index(drop=True)


def greedy_coverage(candidates, demand, radius_m, spacing_m, stations, station_kw, taken=()):
    """Maximal-covering heuristic: pick, one by one, the candidate that covers the most still-uncovered
    demand, among those that can host `station_kw`, are not `taken` and keep `spacing_m` from earlier picks.

    candidates: DataFrame with bus, x, y, hosting_kw. demand: DataFrame with bus, x, y, weight.
    A screening baseline, not an optimal solution."""
    d = distances_m(candidates[["x","y"]],demand[["x","y"]])
    covers = d <= radius_m
    weight = demand.weight.to_numpy(float)
    uncovered = np.ones(len(demand),bool)
    eligible = (candidates.hosting_kw.to_numpy() >= station_kw) & ~candidates.bus.isin(list(taken)).to_numpy()
    spacing = distances_m(candidates[["x","y"]],candidates[["x","y"]])
    chosen = []
    for _ in range(stations):
        gain = np.where(eligible,(covers & uncovered).astype(float)@weight,-1.)
        if gain.max() < 0:
            break
        best = int(gain.argmax())
        chosen.append(dict(bus=candidates.bus.iloc[best],covered_new=float(gain[best]),order=len(chosen)+1))
        uncovered &= ~covers[best]
        eligible &= spacing[best] >= spacing_m
    covered = 1-weight[uncovered].sum()/weight.sum() if weight.sum() else 0.
    return chosen,float(covered)
