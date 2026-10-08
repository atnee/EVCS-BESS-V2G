"""Native pandapower three-phase time series with voltage-dependent loads."""
import numpy as np
import pandas as pd
import pandapower as pp
from core.schemas import Profile
from network.ieee123_loader import create_pandapower_network, FIDELITY, LIMITATIONS

POWER_COLUMNS = [f"{pq}_{ph}_{unit}" for pq,unit in (("p","mw"),("q","mvar")) for ph in "abc"]


def solve_snapshot(net, nominal, max_outer=40):
    """Solve PQ/I/Z and capacitors by updating powers from terminal voltages.

The 3ph engine handles asymmetric PQ and delta connections. A fixed-point
outer loop supplies constant-current (model 5) and impedance (model 2) powers.
Nominal values must already include the timestep's background load multiplier.
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
    for iteration in range(max_outer):
        pp.runpp_3ph(net,numba=False,max_iteration=100,tolerance_mva=1e-8,
                     init="flat" if iteration==0 else "results")
        if not net.converged or not np.isfinite(net.res_bus_3ph.to_numpy()).all():
            raise RuntimeError("pandapower returned unconverged or non-finite bus results")
        result = net.res_bus_3ph.loc[bus_ids]
        v = result[[f"vm_{ph}_pu" for ph in "abc"]].to_numpy()*np.exp(
            1j*np.deg2rad(result[[f"va_{ph}_degree" for ph in "abc"]].to_numpy()))*kv_ln[:,None]
        delta = rows.type.to_numpy()=="delta"
        v[delta] = v[delta]-np.roll(v[delta],-1,axis=1)
        scale = (abs(v)/rated[:,None])**exponent[:,None]
        updated = nominal.to_numpy()*np.tile(scale,(1,2))
        previous = net.asymmetric_load.loc[ids,POWER_COLUMNS].to_numpy()
        if np.max(abs(updated-previous)) < 1e-8:
            return iteration+1
        net.asymmetric_load.loc[ids,POWER_COLUMNS] = updated
    raise RuntimeError("Voltage-dependent IEEE123 loads did not converge")


class PandapowerSolver:
    def __init__(self, load_multipliers=None):
        self.load_multipliers = load_multipliers

    def solve(self, network, profile: Profile) -> dict:
        """Profile contains added DER injections only; IEEE loads are built in."""
        profile.validate(network)
        multipliers = np.ones(profile.grid.steps) if self.load_multipliers is None else np.asarray(self.load_multipliers,float)
        if multipliers.shape!=(profile.grid.steps,) or not np.isfinite(multipliers).all() or (multipliers<0).any():
            raise ValueError("One finite nonnegative IEEE load multiplier per timestep required")
        # A new model per solve prevents scenarios from inheriting prior dispatch.
        net = create_pandapower_network(network)
        nominal = net.asymmetric_load[POWER_COLUMNS].copy()
        load_ids = nominal.index[:len(network.equipment["ieee123_data"][0]["loads"])]
        additions = {}
        for bus in profile.data.bus.unique():
            additions[bus] = pp.create_asymmetric_load(net,net.ieee123_bus_lookup[bus],name=f"DER:{bus}",type="wye")
        voltages, branches, source, consumption = [], [], [], []
        for step,(time,group) in enumerate(profile.data.groupby("time",sort=True)):
            for row in group.itertuples():
                idx = additions[row.bus]
                net.asymmetric_load.at[idx,f"p_{row.phase.lower()}_mw"] = -row.p_kw/1000
                net.asymmetric_load.at[idx,f"q_{row.phase.lower()}_mvar"] = -row.q_kvar/1000
            current_nominal = nominal.copy()
            current_nominal.loc[load_ids] *= multipliers[step]
            iterations = solve_snapshot(net,current_nominal)
            ext = net.res_ext_grid_3ph
            source.append(dict(time=time,p_kw=float(ext[[f"p_{p}_mw" for p in "abc"]].sum().sum()*1000),
                               q_kvar=float(ext[[f"q_{p}_mvar" for p in "abc"]].sum().sum()*1000),
                               converged=True,zip_iterations=iterations,load_multiplier=multipliers[step]))
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
                "fidelity":FIDELITY,"limitations":LIMITATIONS.copy(),"backend_version":pp.__version__}
        for table in ("voltages","branches","source","native_loads"):
            if not np.isfinite(flow[table].select_dtypes(include="number").to_numpy()).all():
                raise RuntimeError(f"Non-finite pandapower results in {table}")
        return flow
