"""Load hosting capacity: largest balanced three-phase load each bus accepts in one snapshot."""
import numpy as np
import pandas as pd
import pandapower as pp
from network.ieee123_loader import load_ieee123, create_pandapower_network
from network.pandapower_solver import solve_snapshot, POWER_COLUMNS


def _phase_mask(table, phases_col="phases"):
    return np.array([[ph in p for ph in "ABC"] for p in table[phases_col]])


class HostingStudy:
    """One pandapower model reused for many probe loads at a fixed loading level.

    Criteria: minimum physical-phase voltage, regulator/transformer loading (rated kVA from
    the IEEE data) and line loading. Lines already above the limit before the probe are
    pre-existing (their ampacity is an assumption) and only reported, not used as a criterion.
    """

    def __init__(self, network=None, multiplier=1.0, v_min=.95, line_limit_pct=100., equipment_limit_pct=100., pf=1.0):
        self.network = load_ieee123() if network is None else network
        self.net = create_pandapower_network(self.network)
        self.v_min, self.line_limit, self.equipment_limit = v_min, line_limit_pct, equipment_limit_pct
        self.q_ratio = float(np.tan(np.arccos(pf)))
        self.nominal = self.net.asymmetric_load[POWER_COLUMNS].copy()
        loads = len(self.network.equipment["ieee123_data"][0]["loads"])
        self.nominal.loc[self.nominal.index[:loads]] *= multiplier  # capacitors stay nominal
        self.probe = pp.create_asymmetric_load(self.net,self.net.ieee123_bus_lookup[self.network.slack_bus],
                                               name="probe",type="wye")
        self.bus_mask = _phase_mask(self.net.bus)
        self.line_mask = _phase_mask(self.net.line)
        self.trafo_mask = _phase_mask(self.net.trafo)
        self.base = self.evaluate(None,0.)
        self.preexisting = sorted(self.base["overloaded_lines"])

    def _solve(self, bus, kw):
        if bus is not None:
            self.net.asymmetric_load.at[self.probe,"bus"] = self.net.ieee123_bus_lookup[bus]
        for ph in "abc":
            self.net.asymmetric_load.at[self.probe,f"p_{ph}_mw"] = kw/3000
            self.net.asymmetric_load.at[self.probe,f"q_{ph}_mvar"] = kw*self.q_ratio/3000
        solve_snapshot(self.net,self.nominal)

    def evaluate(self, bus, kw):
        try:
            self._solve(bus,kw)
        except RuntimeError:
            return dict(ok=False,binding="no convergence",v_min_pu=np.nan,overloaded_lines=set())
        v = self.net.res_bus_3ph[[f"vm_{p}_pu" for p in "abc"]].to_numpy()
        v_min = float(np.where(self.bus_mask,v,np.inf).min())
        lines = np.where(self.line_mask,self.net.res_line_3ph[[f"loading_{p}_percent" for p in "abc"]].to_numpy(),0).max(axis=1)
        trafos = np.where(self.trafo_mask,self.net.res_trafo_3ph[[f"loading_{p}_percent" for p in "abc"]].to_numpy(),0).max(axis=1)
        names = self.net.line.name.to_numpy()
        overloaded = set(names[lines > self.line_limit])
        new_overloads = sorted(overloaded-set(getattr(self,"preexisting",[])))
        binding = []
        if v_min < self.v_min:
            bad = self.net.bus.name.to_numpy()[(np.where(self.bus_mask,v,np.inf) < self.v_min).any(axis=1)]
            binding.append(f"voltage<{self.v_min} at {bad[0]}")
        if (trafos > self.equipment_limit).any():
            binding.append(f"equipment {self.net.trafo.name.to_numpy()[trafos.argmax()]} {trafos.max():.0f}%")
        if new_overloads:
            binding.append(f"line {new_overloads[0]}")
        return dict(ok=not binding,binding="; ".join(binding),v_min_pu=v_min,overloaded_lines=overloaded,
                    max_equipment_pct=float(trafos.max()),
                    max_line_pct_new=float(max([lines[names==n].max() for n in new_overloads],default=0.)))

    def capacity(self, bus, max_kw=3000., tol_kw=10.):
        """Bisection on the probe size; returns the largest feasible kW and what blocks more."""
        top = self.evaluate(bus,max_kw)
        if top["ok"]:
            return dict(bus=bus,hosting_kw=max_kw,binding=f"> {max_kw:.0f} kW (search limit)",v_min_at_capacity=top["v_min_pu"])
        lo,hi,blocked = 0.,max_kw,top
        while hi-lo > tol_kw:
            mid = (lo+hi)/2
            result = self.evaluate(bus,mid)
            if result["ok"]:
                lo = mid
            else:
                hi,blocked = mid,result
        at_lo = self.evaluate(bus,lo)
        return dict(bus=bus,hosting_kw=lo,binding=blocked["binding"],v_min_at_capacity=at_lo["v_min_pu"])

    def sweep(self, buses, max_kw=3000., tol_kw=10.):
        return pd.DataFrame([self.capacity(b,max_kw,tol_kw) for b in buses])

    def check(self, placements):
        """Simultaneous check of several stations {bus: kw} (individual capacities do not add up)."""
        extra = []
        for bus,kw in placements.items():
            idx = pp.create_asymmetric_load(self.net,self.net.ieee123_bus_lookup[bus],name=f"station:{bus}",type="wye",
                **{f"p_{p}_mw": kw/3000 for p in "abc"},**{f"q_{p}_mvar": kw*self.q_ratio/3000 for p in "abc"})
            extra.append(idx)
        try:
            return self.evaluate(None,0.)
        finally:
            self.net.asymmetric_load.drop(index=extra,inplace=True)
