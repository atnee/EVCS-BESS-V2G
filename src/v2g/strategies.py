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
from scipy.optimize import LinearConstraint, milp
from scipy.sparse import lil_array
from core.schemas import make_profile
from v2g.availability import availability, session_energy, session_steps
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
        energy[i] = session_energy(p[i],b,v.arrival,v.departure,grid.dt_h)
        if energy[i].min() < b.soc_min*b.capacity_kwh-1e-7 or energy[i].max() > b.soc_max*b.capacity_kwh+1e-7:
            raise ValueError(f"{v.id}: SOC limits violated")
        # Never borrow the trip reserve: the energy after every discharging step stays >= reserve.
        discharging = np.flatnonzero(p[i] > 1e-9)
        if (energy[i][discharging+1] < v.reserve_kwh-1e-7).any():
            raise ValueError(f"{v.id}: discharge below the mobility reserve")
        if energy[i][v.departure]+1e-7 < v.departure_target_kwh:
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


# Margin [kWh] kept inside the LP bounds so solver tolerances never trip fleet_profile's 1e-7 checks.
LP_MARGIN_KWH = 1e-6


def hourly_to_steps(values, grid, name="prices"):
    """24 hour-of-day values -> one value per grid step, by the hour in which each step starts."""
    values = np.asarray(values,float)
    if values.shape != (24,) or not np.isfinite(values).all():
        raise ValueError(f"{name} needs 24 finite hour-of-day values")
    hours = np.floor(np.arange(grid.steps)*grid.dt_h+1e-9).astype(int) % 24
    return values[hours]


def window_mask(window, grid):
    """[start, end) hours from the start of the horizon -> step mask, as v2g.runner.discharge_mask."""
    start,end = window
    if not 0 <= start < end <= grid.steps*grid.dt_h:
        raise ValueError(f"Window {window} must lie inside the horizon")
    elapsed = np.arange(grid.steps)*grid.dt_h
    return (elapsed >= start) & (elapsed < end)


def check_price_policy(tariff, remuneration, no_charge, discharge):
    """Decision 8: charging dearest in the no-charge window, discharge best paid in the discharge window."""
    if tariff is not None and no_charge.any() and (~no_charge).any() \
            and tariff[no_charge].min() <= tariff[~no_charge].max():
        raise ValueError("Price policy: the tariff inside no_charge_window_h must exceed every tariff outside it")
    if remuneration is not None and discharge.any() and (~discharge).any() \
            and remuneration[discharge].min() <= remuneration[~discharge].max():
        raise ValueError("Price policy: the V2G remuneration inside the discharge window must exceed every value outside it")


def cesar_v1(station, grid, vehicles, independent_evcs_kw, independent_ids=(), discharge_mask=None, enable_v2g=True,
             no_charge_window_h=None, tariff_usd_kwh=None, v2g_remuneration_usd_kwh=None, degradation_usd_kwh=0.,
             tie_break_usd_kw=1e-4):
    """Fleet LP (methodology 11.2 with delta relaxed, 11.4): minimum charging cost minus V2G revenue.

    No charging inside no_charge_window_h nor in any step where discharge is allowed (so a car never
    charges and discharges in the same step). A participating car never goes below its trip reserve from
    its first discharge step on; every car leaves with departure_target_kwh. A small tie-break flattens
    the total recharge and spreads the discharge over the window. enable_v2g=False returns the heuristic
    (every car charges on arrival, no tariff nor window): S1 has no charging control.
    Without prices (no keyword arguments) tariff and remuneration are zero and there is no no-charge window."""
    T,dt = grid.steps,grid.dt_h
    mask = np.zeros(T,bool) if discharge_mask is None else np.asarray(discharge_mask,bool)
    if mask.shape != (T,):
        raise ValueError("Invalid discharge mask")
    no_charge = np.zeros(T,bool) if no_charge_window_h is None else window_mask(no_charge_window_h,grid)
    tariff = None if tariff_usd_kwh is None else hourly_to_steps(tariff_usd_kwh,grid,"tariff_usd_kwh")
    remuneration = None if v2g_remuneration_usd_kwh is None else \
        hourly_to_steps(v2g_remuneration_usd_kwh,grid,"v2g_remuneration_usd_kwh")
    check_price_policy(tariff,remuneration,no_charge,mask)
    warnings = []
    if tariff is None or remuneration is None:
        warnings.append("No tariff or V2G remuneration given: missing prices are taken as zero")
    tariff = np.zeros(T) if tariff is None else tariff
    remuneration = np.zeros(T) if remuneration is None else remuneration
    round_trip = max((v.battery.eta_charge*v.battery.eta_discharge for v in vehicles),default=1.)
    if remuneration.max()*round_trip <= tariff.min():
        warnings.append("max(remuneration)*eta_c*eta_d <= min(tariff): no car has an incentive to discharge")
    # Only arrival_plus_trip (restore_trip) targets can be capped at soc_max.
    capped = [v.id for v in vehicles if v.restore_trip
              and v.battery.soc_initial*v.battery.capacity_kwh+v.mobility_kwh > v.battery.soc_max*v.battery.capacity_kwh+1e-9]
    allowed = mask & bool(enable_v2g) & station.bidirectional
    if not enable_v2g:
        base = schedule(station,grid,vehicles,independent_evcs_kw,independent_ids,discharge_mask,False)
        power_kw,participating = np.array(base.metadata["vehicle_power_kw"]),[]
    else:
        power_kw,participating = fleet_lp(station,grid,vehicles,allowed,no_charge,tariff,remuneration,
                                          degradation_usd_kwh,tie_break_usd_kw)
    result = fleet_profile(station,grid,vehicles,power_kw,independent_evcs_kw,independent_ids,discharge_mask,enable_v2g)
    result.metadata.update(
        charging_cost_usd=float(dt*(tariff*np.maximum(-power_kw,0.)).sum()),
        v2g_revenue_usd=float(dt*(remuneration*np.maximum(power_kw,0.)).sum()),
        participating_vehicles=participating,capped_target_vehicles=capped,warnings=warnings)
    return result


def fleet_lp(station, grid, vehicles, allowed, no_charge, tariff, remuneration, degradation, tie_break):
    """Solve the relaxed fleet LP with HiGHS; returns (vehicles x steps power, participating ids)."""
    T,dt = grid.steps,grid.dt_h
    charge_ok = ~no_charge & ~allowed
    cost,lb,ub,blocks,participating = [],[],[],[],[]
    def add(c,lo,hi):
        cost.extend(c);lb.extend(lo);ub.extend(hi)
        return np.arange(len(cost)-len(c),len(cost))
    for v in vehicles:
        b = v.battery
        order = np.array(session_steps(T,v.arrival,v.departure))
        limit = min(station.charger_kw,b.power_kw)
        e0,e_max,target = b.soc_initial*b.capacity_kwh,b.soc_max*b.capacity_kwh,v.departure_target_kwh
        c_hi,d_hi = np.where(charge_ok[order],limit,0.),np.where(allowed[order],limit,0.)
        # Upper bound of the departure energy: charge at every allowed step, capped at soc_max.
        best = e0
        for hi in c_hi:
            best = min(e_max,best+hi*b.eta_charge*dt)
        if best+1e-9 < target:
            raise ValueError(f"Infeasible departure energy: {v.id} reaches at most {best:.3f} of {target:.3f} kWh")
        first = np.flatnonzero(d_hi > 0)
        joins = first.size > 0 and e0+c_hi[:first[0]].sum()*b.eta_charge*dt > v.reserve_kwh+LP_MARGIN_KWH
        if joins:
            participating.append(v.id)
        else:
            d_hi[:] = 0.
        K = len(order)
        e_lo = np.full(K+1,b.soc_min*b.capacity_kwh)
        if joins:
            e_lo[first[0]:] = v.reserve_kwh+LP_MARGIN_KWH
        e_up = np.full(K+1,max(e_max-LP_MARGIN_KWH,e0))
        e_lo[-1] = max(e_lo[-1],min(target+LP_MARGIN_KWH,e_up[-1]))
        e_lo[0] = e_up[0] = e0
        c = add(dt*tariff[order],np.zeros(K),c_hi)
        d = add(-dt*(remuneration[order]-degradation),np.zeros(K),d_hi)
        e = add(np.zeros(K+1),e_lo,e_up)
        blocks.append((order,c,d,e,b))
    # Epigraph scalars: z_c >= the largest total charging, z_d <= the smallest total discharging in the window.
    z_c = add([tie_break],[0.],[np.inf])[0]
    z_d = add([-tie_break],[0.],[np.inf if allowed.any() else 0.])[0]
    rows = sum(len(order) for order,*_ in blocks)
    A = lil_array((rows+2*T,len(cost)))
    r = 0
    for order,c,d,e,b in blocks:
        for k,t in enumerate(order):
            # E_{k+1} = E_k + eta_c*c*dt - d*dt/eta_d, in session order.
            A[r,e[k+1]],A[r,e[k]],A[r,c[k]],A[r,d[k]] = 1.,-1.,-b.eta_charge*dt,dt/b.eta_discharge
            A[rows+t,c[k]] = 1.  # z_c >= total charging in step t
            A[rows+T+t,d[k]] = -1.  # z_d <= total discharging in step t
            r += 1
    for t in range(T):
        A[rows+t,z_c] = -1.
        if allowed[t]:
            A[rows+T+t,z_d] = 1.
    lo_rows = np.concatenate([np.zeros(rows),np.full(2*T,-np.inf)])
    res = milp(np.array(cost),constraints=LinearConstraint(A.tocsr(),lo_rows,np.zeros(rows+2*T)),
               integrality=np.zeros(len(cost)),bounds=(np.array(lb),np.array(ub)))
    if res.status != 0:
        raise ValueError(f"cesar_v1 LP failed ({res.message}); check sessions, windows and departure targets")
    x = np.clip(np.where(np.abs(res.x) < 1e-9,0.,res.x),lb,ub)
    power = np.zeros((len(vehicles),T))
    for i,(order,c,d,e,b) in enumerate(blocks):
        power[i,order] = x[d]-x[c]
    return power,participating


STRATEGIES = {"heuristic": schedule, "template": template, "cesar_v1": cesar_v1}


def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown V2G strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name]
