"""Deterministic peak-shaving heuristic, with terminal recharge."""
import numpy as np
from bess.model import Battery, Dispatch, simulate

def peak_shave(battery: Battery, demand_kw, target_kw: float, dt_h: float) -> Dispatch:
    demand = np.asarray(demand_kw, float)
    if demand.ndim != 1 or len(demand) == 0 or not np.isfinite(demand).all() or not np.isfinite(target_kw):
        raise ValueError("Invalid demand/target")
    commands = []
    energy = battery.soc_initial * battery.capacity_kwh
    target_energy = energy
    for t, load in enumerate(demand):
        # Reserve enough remaining time to restore the initial energy.
        future_charge = (len(demand)-t-1)*battery.power_kw*dt_h*battery.eta_charge
        floor = max(battery.soc_min*battery.capacity_kwh, target_energy-future_charge)
        if load > target_kw:
            p = min(load-target_kw, battery.power_kw, max(0., energy-floor)*battery.eta_discharge/dt_h)
        else:
            p = -min(max(0., target_kw-load), battery.power_kw,
                     max(0., target_energy-energy)/(battery.eta_charge*dt_h))
        required_charge = max(0., target_energy-future_charge-energy)/(battery.eta_charge*dt_h)
        if required_charge > 0:
            p = -min(battery.power_kw, required_charge)
        energy += (-p*battery.eta_charge if p < 0 else -p/battery.eta_discharge)*dt_h
        commands.append(p)
    return simulate(battery, commands, dt_h, final_soc=battery.soc_initial)
