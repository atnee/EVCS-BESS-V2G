"""Station infrastructure and independent (non-V2G) demand."""
import numpy as np
from core.schemas import TimeGrid, Profile, make_profile
from core.assets import Station  # public compatibility re-export

def demand_profile(station: Station, grid: TimeGrid, demand_kw, vehicle_ids=()) -> Profile:
    """Aggregate grid-side demand, clipped to installed capacity; no queue model."""
    demand = np.asarray(demand_kw, float)
    if demand.shape != (grid.steps,) or not np.isfinite(demand).all() or (demand < 0).any():
        raise ValueError("Invalid EV demand")
    served = np.minimum(demand, station.capacity_kw)
    result = make_profile(grid, station.id, station.bus, station.phases, -served, vehicle_ids=vehicle_ids)
    result.metadata.update(requested_kwh=float(demand.sum()*grid.dt_h), served_kwh=float(served.sum()*grid.dt_h),
                           unserved_kwh=float((demand-served).sum()*grid.dt_h))
    return result
