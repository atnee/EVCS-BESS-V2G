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
from scipy.optimize import LinearConstraint, milp
from scipy.sparse import lil_array
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


def trial7_schedule(battery, demand_kw, config, dt_h):
    """Replay the selected hourly S2 dispatch for the documented study day."""
    demand = np.asarray(demand_kw, float)
    commands = np.asarray(config["trial7_schedule"]["commands_kw"], float)
    if (dt_h != 1.0 or demand.ndim != 1 or commands.shape != demand.shape or
            not np.isfinite(demand).all() or not np.isfinite(commands).all()):
        raise ValueError("Trial-7 schedule requires a finite hourly demand of matching length")
    result = simulate(battery, commands, dt_h, final_soc=battery.soc_initial)
    if np.max(np.abs(result.rejected_kw)) > 1e-6:
        raise ValueError("Trial-7 schedule exceeds battery power or SOC limits")
    return result


LP_MARGIN_KWH = 1e-6  # kept inside the SOC bounds so solver tolerances never trip simulate's checks


def line_limit(battery, demand_kw, config, dt_h):
    """Keep the predicted loading of each monitored line at or below `target_pct` minus its margin (LP, HiGHS).

    Generalizes Kenia's line rule (trials 4-5) without manual adjustments. For each monitored line l
    (config `line_limit.lines`, written by `python -m integration.bess_calibration`):
      predicted loading before the BESS  L_lt = a_l + b_l * demand_t      (% , demand in kW)
      loading after the BESS             L_lt - s_l * (discharge_t - charge_t) <= target_pct - margin_l
    margin_l is the largest error of the line's fit, so the prediction error is covered line by line.
    Minimizes the loading above target (dominant weight), then the energy cycled, charging first where
    the demand is lowest. Charging is limited by the same line constraints, so it never creates an
    overload the model can see. The day ends at the initial SOC. With no monitored line it stays idle."""
    demand = np.asarray(demand_kw,float)
    if demand.ndim != 1 or not len(demand) or not np.isfinite(demand).all() or dt_h <= 0:
        raise ValueError("Invalid BESS demand or time step")
    settings = config["line_limit"]
    lines = settings.get("lines") or {}
    target, w_over, w_use = float(settings["target_pct"]), float(settings["overload_weight"]), float(settings["use_weight"])
    T, L = len(demand), len(lines)
    c, d, e, u = 0, T, 2*T, 3*T+1  # charge[T], discharge[T], energy[T+1], excess[L*T]
    size = u+L*T
    e0, cap = battery.soc_initial*battery.capacity_kwh, battery.capacity_kwh
    lb, ub = np.zeros(size), np.full(size,np.inf)
    ub[c:c+T] = ub[d:d+T] = battery.power_kw
    lb[e:e+T+1], ub[e:e+T+1] = battery.soc_min*cap+LP_MARGIN_KWH, battery.soc_max*cap-LP_MARGIN_KWH
    lb[e], ub[e], lb[e+T], ub[e+T] = e0, e0, e0, e0
    spread = max(float(np.ptp(demand)),1.)
    cost = np.zeros(size)
    cost[c:c+T] = w_use*(1+(demand-demand.min())/spread)  # charge where the demand is lowest
    cost[d:d+T] = w_use
    cost[u:] = w_over
    A = lil_array((T+L*T,size)); lo, hi = np.zeros(T+L*T), np.zeros(T+L*T)
    for t in range(T):  # E_{t+1} = E_t + eta_c*c*dt - d*dt/eta_d
        A[t,e+t+1], A[t,e+t], A[t,c+t], A[t,d+t] = 1., -1., -battery.eta_charge*dt_h, dt_h/battery.eta_discharge
    for k,(name,line) in enumerate(lines.items()):
        before = float(line["a"])+float(line["b"])*demand
        limit = target-float(line.get("margin",0.))
        for t in range(T):  # before - s*(d - c) - excess <= target - margin
            r = T+k*T+t
            A[r,d+t], A[r,c+t], A[r,u+k*T+t] = -float(line["s"]), float(line["s"]), -1.
            lo[r], hi[r] = -np.inf, limit-before[t]
    res = milp(cost,constraints=LinearConstraint(A.tocsr(),lo,hi),integrality=np.zeros(size),bounds=(lb,ub))
    if res.status != 0:
        raise ValueError(f"line_limit LP failed: {res.message}")
    x = np.clip(np.where(np.abs(res.x) < 1e-9,0.,res.x),lb,ub)
    return simulate(battery,x[d:d+T]-x[c:c+T],dt_h,final_soc=battery.soc_initial)


STRATEGIES = {"peak_shave": peak_shave_strategy, "template": template,
              "trial7_schedule": trial7_schedule, "line_limit": line_limit}


def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown BESS strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name]
