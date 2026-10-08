"""EVCS demand strategies. Add a function, register it in STRATEGIES and select it
with `strategy:` in configs/evcs.yaml; standalone runs and integration both use it.

Contract: fn(station, grid, config) -> requested demand in kW, one value >= 0 per step.
`config` is the whole evcs.yaml, so new parameters can be added there.
evcs.model.demand_profile clips the request to station capacity and reports unserved energy.
tests/test_strategies.py checks every registered strategy automatically.
"""
import numpy as np


def fixed(station, grid, config):
    """Default: hourly demand typed in evcs.yaml (`independent_demand_kw`)."""
    return np.asarray(config["independent_demand_kw"],float)


def template(station, grid, config):
    """Starting point for a vehicle-population model (arrivals, energy per session, queues).
    It currently reproduces `fixed`; replace the line below with your own computation."""
    demand_kw = fixed(station,grid,config).copy()  # TODO: your demand model
    return demand_kw


STRATEGIES = {"fixed": fixed, "template": template}


def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown EVCS strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name]
