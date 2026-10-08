"""V2G fleet strategies. Add a function, register it in STRATEGIES and select it
with `strategy:` in configs/v2g.yaml; standalone runs and integration both use it.

Contract: fn(station, grid, vehicles, independent_evcs_kw, independent_ids, discharge_mask, enable_v2g) -> Profile
- independent_evcs_kw: EVCS demand already using the same station (kW per step).
- discharge_mask: steps in which V2G discharge is allowed; enable_v2g=False forbids it (S1/S2).
- Compute a vehicles x steps power matrix (positive = discharge to the grid) and return
  fleet_profile(...), which checks connection, ratings, SOC, mobility reserve and shared ports.
tests/test_strategies.py checks every registered strategy automatically.
"""
import numpy as np
from core.schemas import make_profile
from v2g.availability import availability
from v2g.model import schedule


def fleet_profile(station, grid, vehicles, power_kw, independent_evcs_kw, independent_ids=(),
                  discharge_mask=None, enable_v2g=True):
    """Validate a vehicles x steps power matrix and build the fleet Profile."""
    p = np.asarray(power_kw,float)
    ids = [v.id for v in vehicles]
    if p.shape != (len(vehicles),grid.steps) or not np.isfinite(p).all():
        raise ValueError("power_kw must be a finite vehicles x steps matrix")
    if len(set(ids)) != len(ids) or set(ids) & set(independent_ids):
        raise ValueError("Duplicate vehicle across EVCS/V2G")
    independent = np.asarray(independent_evcs_kw,float)
    mask = np.zeros(grid.steps,bool) if discharge_mask is None else np.asarray(discharge_mask,bool)
    if independent.shape != (grid.steps,) or mask.shape != (grid.steps,):
        raise ValueError("Invalid independent demand or discharge mask")
    allowed = mask & enable_v2g & station.bidirectional
    connected = np.array([availability(grid.steps,v.arrival,v.departure) for v in vehicles]).reshape(len(vehicles),grid.steps)
    # Same conservative full-port reservation as the heuristic.
    ports = np.ceil(np.maximum(0.,independent-1e-10)/station.charger_kw)
    if (ports+connected.sum(axis=0) > station.chargers).any():
        raise ValueError("Insufficient shared charger ports")
    if (independent+connected.sum(axis=0)*station.charger_kw > station.connection_kw+1e-9).any():
        raise ValueError("Insufficient shared connection for conservative reservation")
    energy = np.zeros((len(vehicles),grid.steps+1))
    for i,v in enumerate(vehicles):
        b = v.battery
        if (np.abs(p[i][~connected[i]]) > 1e-9).any():
            raise ValueError(f"{v.id}: power while disconnected")
        if (np.abs(p[i]) > min(station.charger_kw,b.power_kw)+1e-9).any():
            raise ValueError(f"{v.id}: power above charger/battery rating")
        if (p[i][~allowed] > 1e-9).any():
            raise ValueError(f"{v.id}: discharge outside the allowed V2G window")
        delta = np.where(p[i] < 0,p[i]*b.eta_charge,p[i]/b.eta_discharge)*grid.dt_h
        energy[i] = b.soc_initial*b.capacity_kwh-np.concatenate([[0.],np.cumsum(delta)])
        if energy[i].min() < b.soc_min*b.capacity_kwh-1e-7 or energy[i].max() > b.soc_max*b.capacity_kwh+1e-7:
            raise ValueError(f"{v.id}: SOC limits violated")
        if energy[i][v.departure]+1e-7 < max(v.reserve_kwh,b.soc_initial*b.capacity_kwh):
            raise ValueError(f"Infeasible departure energy: {v.id}")
    result = make_profile(grid,station.id+"-fleet",station.bus,station.phases,p.sum(axis=0),vehicle_ids=ids)
    result.metadata.update(vehicle_power_kw=p.tolist(),energy_kwh=energy.tolist(),
                           v2g_delivered_kwh=float(np.maximum(p,0).sum()*grid.dt_h),
                           departure_energy_kwh={v.id: float(energy[i][v.departure]) for i,v in enumerate(vehicles)})
    return result


def template(station, grid, vehicles, independent_evcs_kw, independent_ids=(), discharge_mask=None, enable_v2g=True):
    """Starting point for availability/trip/degradation-aware scheduling.
    It currently reuses the heuristic power matrix; replace `power_kw` with your own."""
    base = schedule(station,grid,vehicles,independent_evcs_kw,independent_ids,discharge_mask,enable_v2g)
    power_kw = np.array(base.metadata["vehicle_power_kw"])  # TODO: your schedule (vehicles x steps)
    return fleet_profile(station,grid,vehicles,power_kw,independent_evcs_kw,independent_ids,discharge_mask,enable_v2g)


STRATEGIES = {"heuristic": schedule, "template": template}


def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown V2G strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name]
