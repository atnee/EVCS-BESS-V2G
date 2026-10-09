"""Native pandapower three-phase time series with voltage-dependent loads."""
import numpy as np
import pandas as pd
import pandapower as pp
from core.schemas import Profile
from network.feeder_loader import create_pandapower_network

POWER_COLUMNS = [f"{pq}_{ph}_{unit}" for pq,unit in (("p","mw"),("q","mvar")) for ph in "abc"]


def solve_snapshot(net, nominal, max_outer=40, tolerance_mw=1e-8, warm=False):
    """Solve PQ/I/Z and capacitors by updating powers from terminal voltages.

The 3ph engine handles asymmetric PQ and delta connections. A fixed-point
outer loop supplies constant-current (model 5) and impedance (model 2) powers
and, on feeders with regulator settings (net.regulator_control), moves the
regulator taps until each regulated voltage is inside its band.
Nominal values must already include the timestep's background load multiplier.
warm: start from the previous results (time series) instead of a flat profile.
"""
    ids = nominal.index
    net.asymmetric_load.loc[ids,POWER_COLUMNS] = nominal
    rows = net.asymmetric_load.loc[ids]
    bus_ids = rows.bus.to_numpy(int)
    kv_ln = net.bus.loc[bus_ids,"vn_kv"].to_numpy()/np.sqrt(3)
    rated = rows.nominal_kv.to_numpy(float)
    exponent = rows.source_model.map({1:0,2:2,5:1}).to_numpy(float)
    if not np.isfinite(exponent).all():
        raise ValueError("Unsupported IEEE load model")
    warm = warm and "res_bus_3ph" in net and len(net.res_bus_3ph) == len(net.bus) and _finite(net)
    for iteration in range(max_outer):
        _run_3ph(net,"results" if iteration or warm else "flat")
        result = net.res_bus_3ph.loc[bus_ids]
        v = result[[f"vm_{ph}_pu" for ph in "abc"]].to_numpy()*np.exp(
            1j*np.deg2rad(result[[f"va_{ph}_degree" for ph in "abc"]].to_numpy()))*kv_ln[:,None]
        delta = rows.type.to_numpy()=="delta"
        v[delta] = v[delta]-np.roll(v[delta],-1,axis=1)
        scale = (abs(v)/rated[:,None])**exponent[:,None]
        updated = nominal.to_numpy()*np.tile(scale,(1,2))
        previous = net.asymmetric_load.loc[ids,POWER_COLUMNS].to_numpy()
        loads_settled = np.max(abs(updated-previous)) < tolerance_mw
        net.asymmetric_load.loc[ids,POWER_COLUMNS] = updated
        if not _move_regulator(net) and loads_settled:
            net.asymmetric_load.loc[ids,POWER_COLUMNS] = previous  # powers of the solved state
            return iteration+1
    raise RuntimeError("Voltage-dependent IEEE loads did not converge")


def _move_regulator(net, max_step=4):
    """RegControl emulation: the most upstream bank outside vreg +/- band/2 (mean of its phases)
    moves toward vreg, at most max_step taps of 0.625 %. Returns True when a tap moved."""
    for r in net.get("regulator_control",[]):
        res = net.res_bus_3ph.loc[r["lv_bus"]]
        v = float(np.mean([res[f"vm_{ph.lower()}_pu"] for ph in r["phases"]]))
        if abs(v-r["vreg_pu"]) <= r["band_pu"]/2:
            continue
        tap = net.trafo.at[r["trafo"],"tap_pos"]
        new = float(np.clip(tap+np.clip(round((r["vreg_pu"]-v)/.00625),-max_step,max_step),-16,16))
        if new != tap:
            net.trafo.at[r["trafo"],"tap_pos"] = new
            return True
    return False


def _finite(net):
    return net.converged and np.isfinite(net.res_bus_3ph.to_numpy()).all()


def _run_3ph(net, init):
    """runpp_3ph; a flat start that fails with regulator taps far from neutral (IEEE 8500 at peak)
    is retried by ramping the taps from neutral, each step warm-started from the previous one."""
    def run(start):
        try:
            pp.runpp_3ph(net,numba=False,max_iteration=100,tolerance_mva=1e-8,init=start)
        except pp.LoadflowNotConverged:
            net.converged = False
        return _finite(net)
    if run(init):
        return
    taps = net.trafo.tap_pos.copy()
    if init == "flat" and taps.fillna(0).abs().max() > 0:
        net.trafo.loc[taps.notna(),"tap_pos"] = 0.
        ok = run("flat")
        for share in (.25,.5,.75,1.):
            if not ok:
                break
            net.trafo["tap_pos"] = taps*share if share < 1 else taps
            ok = run("results")
        net.trafo["tap_pos"] = taps
        if ok:
            return
    raise RuntimeError("pandapower returned unconverged or non-finite bus results")


class PandapowerSolver:
    def __init__(self, load_multipliers=None, tolerance_mw=1e-8):
        self.load_multipliers = load_multipliers
        self.tolerance_mw = tolerance_mw

    def solve(self, network, profile: Profile) -> dict:
        """Profile contains added DER injections only; IEEE loads are built in."""
        profile.validate(network)
        multipliers = np.ones(profile.grid.steps) if self.load_multipliers is None else np.asarray(self.load_multipliers,float)
        if multipliers.shape!=(profile.grid.steps,) or not np.isfinite(multipliers).all() or (multipliers<0).any():
            raise ValueError("One finite nonnegative IEEE load multiplier per timestep required")
        # A new model per solve prevents scenarios from inheriting prior dispatch.
        net = create_pandapower_network(network)
        nominal = net.asymmetric_load[POWER_COLUMNS].copy()
        load_ids = nominal.index[:len(network.equipment["feeder_data"][0]["loads"])]
        additions = {}
        for bus in profile.data.bus.unique():
            additions[bus] = pp.create_asymmetric_load(net,net.bus_lookup[bus],name=f"DER:{bus}",type="wye")
        voltages, branches, source, consumption = [], [], [], []
        for step,(time,group) in enumerate(profile.data.groupby("time",sort=True)):
            for row in group.itertuples():
                idx = additions[row.bus]
                net.asymmetric_load.at[idx,f"p_{row.phase.lower()}_mw"] = -row.p_kw/1000
                net.asymmetric_load.at[idx,f"q_{row.phase.lower()}_mvar"] = -row.q_kvar/1000
            current_nominal = nominal.copy()
            current_nominal.loc[load_ids] *= multipliers[step]
            iterations = solve_snapshot(net,current_nominal,tolerance_mw=self.tolerance_mw,warm=step>0)
            ext = net.res_ext_grid_3ph
            source.append(dict(time=time,p_kw=float(ext[[f"p_{p}_mw" for p in "abc"]].sum().sum()*1000),
                               q_kvar=float(ext[[f"q_{p}_mvar" for p in "abc"]].sum().sum()*1000),
                               converged=True,zip_iterations=iterations,load_multiplier=multipliers[step],
                               **{f"tap_{r['name']}": float(net.trafo.at[r["trafo"],"tap_pos"])
                                  for r in net.get("regulator_control",[])}))
            for idx,b in net.bus.iterrows():
                for phase in b.phases:
                    voltages.append(dict(time=time,bus=b["name"],phase=phase,
                                         v_pu=net.res_bus_3ph.at[idx,f"vm_{phase.lower()}_pu"]))
            for kind in ("line","trafo"):
                for idx,element in net[kind].iterrows():
                    res = net[f"res_{kind}_3ph"].loc[idx]
                    for ph in "abc":
                        amp = res[f"i_{ph}_ka"] if kind=="line" else max(res[f"i_{ph}_hv_ka"],res[f"i_{ph}_lv_ka"])
                        branches.append(dict(time=time,line=element["name"],element_type=kind,phase=ph.upper(),
                            physical_phase=ph.upper() in element.phases,current_a=amp*1000,
                            loading_pct=res[f"loading_{ph}_percent"],loss_kw=res[f"pl_{ph}_mw"]*1000))
            for idx,row in net.asymmetric_load.loc[nominal.index].iterrows():
                consumption.append(dict(time=time,asset=row["name"],bus=net.bus.at[row.bus,"name"],
                    p_kw=sum(row[f"p_{ph}_mw"] for ph in "abc")*1000,
                    q_kvar=sum(row[f"q_{ph}_mvar"] for ph in "abc")*1000))
        flow = {"voltages":pd.DataFrame(voltages),"branches":pd.DataFrame(branches),
                "source":pd.DataFrame(source),"native_loads":pd.DataFrame(consumption),
                "fidelity":net.fidelity,"limitations":list(net.limitations),"backend_version":pp.__version__}
        for table in ("voltages","branches","source","native_loads"):
            if not np.isfinite(flow[table].select_dtypes(include="number").to_numpy()).all():
                raise RuntimeError(f"Non-finite pandapower results in {table}")
        return flow
