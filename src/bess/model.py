"""AC-side storage dispatch with explicit state conservation."""
from dataclasses import dataclass
import numpy as np
from core.schemas import finite
from core.assets import Battery  # public compatibility re-export

@dataclass
class Dispatch:
    injection_kw: np.ndarray
    soc: np.ndarray  # T+1 boundary states
    rejected_kw: np.ndarray


def simulate(battery: Battery, requested_kw, dt_h: float, final_soc: float | None = None) -> Dispatch:
    """Positive command discharges. Feasible projection; rejected power is explicit."""
    finite(dt_h, "dt_h")
    req = np.asarray(requested_kw, float)
    if dt_h == 0 or req.ndim != 1 or not len(req) or not np.isfinite(req).all():
        raise ValueError("Invalid commands or duration")
    if final_soc is not None and not battery.soc_min <= final_soc <= battery.soc_max:
        raise ValueError("Invalid final SOC")
    energy = battery.capacity_kwh * battery.soc_initial
    states, actual = [battery.soc_initial], []
    for command in req:
        if command >= 0:
            p = min(command, battery.power_kw, max(0., energy-battery.soc_min*battery.capacity_kwh)*battery.eta_discharge/dt_h)
            energy -= p * dt_h / battery.eta_discharge
        else:
            charge = min(-command, battery.power_kw, max(0., battery.soc_max*battery.capacity_kwh-energy)/(battery.eta_charge*dt_h))
            p = -charge
            energy += charge * dt_h * battery.eta_charge
        actual.append(p)
        states.append(energy/battery.capacity_kwh)
    if final_soc is not None and abs(states[-1]-final_soc) > 1e-7:
        raise ValueError("Dispatch does not meet required final SOC")
    power = np.asarray(actual)
    return Dispatch(power, np.asarray(states), req-power)
