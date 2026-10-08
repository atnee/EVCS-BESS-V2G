"""BESS dispatch strategies. Add a function, register it in STRATEGIES and select it
with `strategy:` in configs/bess.yaml; standalone runs and integration both use it.

Contract: fn(battery, demand_kw, config, dt_h) -> bess.model.Dispatch
- demand_kw: site demand before the BESS, one value per step (kW).
- config: the whole bess.yaml (target_kw, plus any parameter you add, e.g. tariffs).
- Build the result with bess.model.simulate(battery, commands_kw, dt_h, final_soc=battery.soc_initial).
  Positive command discharges. simulate enforces power/SOC limits, reports rejected power and
  checks that the day ends at the initial SOC.
tests/test_strategies.py checks every registered strategy automatically.
"""
import numpy as np
from bess.model import simulate
from bess.dispatch import peak_shave


def peak_shave_strategy(battery, demand_kw, config, dt_h):
    """Default: deterministic peak shaving down to `target_kw`."""
    return peak_shave(battery,demand_kw,config["target_kw"],dt_h)


def template(battery, demand_kw, config, dt_h):
    """Starting point for an optimized dispatch (tariff, degradation, P/E sizing).
    It currently reuses the peak-shaving commands; replace them with your own."""
    demand = np.asarray(demand_kw,float)
    commands_kw = peak_shave(battery,demand,config["target_kw"],dt_h).injection_kw  # TODO: your dispatch
    return simulate(battery,commands_kw,dt_h,final_soc=battery.soc_initial)


STRATEGIES = {"peak_shave": peak_shave_strategy, "template": template}


def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown BESS strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name]
