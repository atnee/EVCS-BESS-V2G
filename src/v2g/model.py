"""Per-vehicle schedules using an EVCS Station as the sole infrastructure owner."""
from dataclasses import dataclass
import numpy as np
from core.schemas import TimeGrid, Profile, make_profile, finite
from core.assets import Station, Battery
from v2g.availability import availability, session_steps, session_energy

@dataclass(frozen=True)
class Vehicle:
    id: str
    battery: Battery
    arrival: int
    departure: int
    mobility_kwh: float
    departure_soc: float

    def __post_init__(self):
        finite(self.mobility_kwh, "mobility_kwh")
        if not self.id or not self.battery.soc_min <= self.departure_soc <= self.battery.soc_max:
            raise ValueError("Invalid vehicle or departure SOC")
        if self.reserve_kwh > self.battery.capacity_kwh*self.battery.soc_max:
            raise ValueError("Mobility reserve exceeds usable battery")

    @property
    def reserve_kwh(self) -> float:
        # Preserve minimum SOC after the next trip and the departure target.
        return max(self.departure_soc*self.battery.capacity_kwh,
                   self.mobility_kwh+self.battery.soc_min*self.battery.capacity_kwh)


def schedule(station: Station, grid: TimeGrid, vehicles: list[Vehicle], independent_evcs_kw,
             independent_ids=(), discharge_mask=None, enable_v2g=True) -> Profile:
    """Conservative full-port reservation and gross-power sharing. No queueing."""
    ids = [v.id for v in vehicles]
    if len(set(ids)) != len(ids) or set(ids) & set(independent_ids):
        raise ValueError("Duplicate vehicle across EVCS/V2G")
    if enable_v2g and not station.bidirectional:
        raise ValueError("Station has no bidirectional chargers")
    independent = np.asarray(independent_evcs_kw, float)
    if independent.shape != (grid.steps,) or not np.isfinite(independent).all() or (independent < 0).any() or (independent > station.capacity_kw+1e-9).any():
        raise ValueError("Invalid independent station demand")
    mask = np.zeros(grid.steps, bool) if discharge_mask is None else np.asarray(discharge_mask, bool)
    if mask.shape != (grid.steps,):
        raise ValueError("Invalid discharge mask")
    connected = np.array([availability(grid.steps, v.arrival, v.departure) for v in vehicles]).reshape(len(vehicles), grid.steps)
    # Independent demand receives whole ports, even at partial loading.
    ports = np.ceil(np.maximum(0., independent-1e-10)/station.charger_kw)
    if (ports+connected.sum(axis=0) > station.chargers).any():
        raise ValueError("Insufficient shared charger ports")
    if (independent+connected.sum(axis=0)*station.charger_kw > station.connection_kw+1e-9).any():
        raise ValueError("Insufficient shared connection for conservative reservation")
    p = np.zeros((len(vehicles), grid.steps))
    states = np.zeros((len(vehicles), grid.steps+1))
    # The full-port reservation decouples vehicles, so each session is scheduled in its own time order.
    for i, v in enumerate(vehicles):
        b = v.battery
        limit = min(station.charger_kw, b.power_kw)
        target = max(v.reserve_kwh, b.soc_initial*b.capacity_kwh)
        e = b.soc_initial*b.capacity_kwh
        order = session_steps(grid.steps, v.arrival, v.departure)
        for k, t in enumerate(order):
            floor = v.reserve_kwh if enable_v2g and mask[t] else target
            future = (len(order)-k-1)*limit*grid.dt_h*b.eta_charge
            floor = max(floor, target-future)
            if e < floor-1e-9:
                power = -min(limit, (floor-e)/(grid.dt_h*b.eta_charge))
            elif enable_v2g and mask[t]:
                # Never borrow the reserve needed for the next trip.
                power = min(limit, max(0., e-floor)*b.eta_discharge/grid.dt_h)
            else:
                power = 0.
            p[i,t] = power
            e += (-power*b.eta_charge if power < 0 else -power/b.eta_discharge)*grid.dt_h
        states[i] = session_energy(p[i], b, v.arrival, v.departure, grid.dt_h)
        if states[i][v.departure]+1e-7 < target:
            raise ValueError(f"Infeasible departure energy: {v.id}")
    result = make_profile(grid, station.id+"-fleet", station.bus, station.phases, p.sum(axis=0), vehicle_ids=ids)
    result.metadata.update(vehicle_power_kw=p.tolist(), energy_kwh=states.tolist(),
                           v2g_delivered_kwh=float(np.maximum(p,0).sum()*grid.dt_h),
                           departure_energy_kwh={v.id: float(states[i][v.departure]) for i,v in enumerate(vehicles)})
    return result
