"""Independent-phase radial backward/forward sweep for SYNTHETIC tests only.
No mutual impedances, neutral model, delta loads or voltage controllers.
"""
import numpy as np
import pandas as pd
from core.schemas import Network, Profile
from network.network_analysis import radial_order

class SyntheticRadialSolver:
    def solve(self, network: Network, profile: Profile) -> dict:
        if not network.synthetic:
            raise NotImplementedError("Synthetic solver only; use PandapowerSolver for the IEEE123 approximation")
        if any(network.equipment.values()):
            raise NotImplementedError("Equipment controls/transformers unsupported by synthetic solver")
        profile.validate(network)
        order, children, incoming = radial_order(network)
        buses = {b.id: b for b in network.buses}
        vbase = buses[network.slack_bus].vn_ln_v
        if any(abs(b.vn_ln_v-vbase)>1e-9 for b in network.buses):
            raise NotImplementedError("Multiple voltage levels unsupported")
        voltage_rows, branch_rows, source_rows = [], [], []
        for time, group in profile.data.groupby("time", sort=True):
            load = {(r.bus,r.phase): -complex(r.p_kw,r.q_kvar)*1000 for r in group.itertuples()}
            v = {(b.id,ph): vbase*np.exp(1j*{"A":0,"B":-2*np.pi/3,"C":2*np.pi/3}[ph]) for b in network.buses for ph in b.phases}
            source_v = {ph:v[(network.slack_bus,ph)] for ph in buses[network.slack_bus].phases}
            def currents():
                node = {key: np.conj(load.get(key,0)/value) for key,value in v.items()}
                branch = {}
                for bus in reversed(order[1:]):
                    line = incoming[bus]
                    for ph in line.phases:
                        branch[(line.id,ph)] = node[(bus,ph)]
                        node[(line.from_bus,ph)] += node[(bus,ph)]
                return node,branch
            for iteration in range(200):
                _, current = currents()
                old = v.copy()
                for bus in order[1:]:
                    line = incoming[bus]
                    for ph in line.phases:
                        v[(bus,ph)] = v[(line.from_bus,ph)]-complex(line.r_ohm,line.x_ohm)*current[(line.id,ph)]
                if min(abs(x) for x in v.values()) < 0.2*vbase:
                    raise RuntimeError("Voltage collapse/invalid iterate")
                if max(abs(v[k]-old[k]) for k in v)/vbase < 1e-10:
                    break
            else:
                raise RuntimeError("Power flow did not converge")
            node,current = currents()
            source = sum(source_v[ph]*np.conj(node[(network.slack_bus,ph)]) for ph in source_v)/1000
            source_rows.append(dict(time=time,p_kw=source.real,q_kvar=source.imag))
            for (bus,phase), value in v.items():
                voltage_rows.append(dict(time=time,bus=bus,phase=phase,v_pu=abs(value)/vbase))
            for line in network.lines:
                for ph in line.phases:
                    amp = abs(current[(line.id,ph)])
                    branch_rows.append(dict(time=time,line=line.id,phase=ph,current_a=amp,
                                            loading_pct=100*amp/line.ampacity_a,loss_kw=amp**2*line.r_ohm/1000))
        return {"voltages":pd.DataFrame(voltage_rows),"branches":pd.DataFrame(branch_rows),
                "source":pd.DataFrame(source_rows),"fidelity":"synthetic independent-phase radial PQ only"}
