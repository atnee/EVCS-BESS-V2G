"""24-hour BESS + per-vehicle V2G MILP; network coefficients provided by OpenDSS.

Scientific distinction: the MILP is a LOCAL linear surrogate, never a replacement
for post-solution AC/OpenDSS validation with automatic controls.
Positive power means injection into the MV feeder.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
import numpy as np
import pandas as pd
from scipy.optimize import milp, Bounds, LinearConstraint
from scipy.sparse import coo_matrix


@dataclass
class OptSettings:
    thermal_screen_pct: float = 80.0
    voltage_screen_margin_pu: float = 0.005
    thermal_weight: float = 40.0  # objective units per percentage-point-hour
    undervoltage_weight: float = 5000.0  # objective units per pu-hour
    overvoltage_weight: float = 5000.0
    peak_weight: float = 0.5   # objective units per kW of peak import
    bess_throughput_usd_kwh: float = 0.03
    vehicle_degradation_usd_kwh: float = 0.0
    battery_mode_binary: bool = True
    relative_to_s0: bool = True
    solver_time_limit_s: float = 300.0
    mip_rel_gap: float = 0.02
    # Optional sequential-linearization trust regions (kW by hour).
    # None preserves exact V0.8 behavior.
    bess_trust_kw: float | None = None
    v2g_trust_kw: float | None = None
    # Optional epsilon-constraints on PREDICTED (frozen-control) violations.
    # These are NOT AC feasibility certificates. Applied only to screened rows.
    max_under_slack_puh: float | None = None
    max_over_slack_puh: float | None = None
    max_thermal_slack_pph: float | None = None
    # V1.1: deterministic per-phase/hour hard guards on the local linear surrogate.
    # (hour, element_name, phase, bound). All must be AC-verified after solving.
    guarded_line_max_pct: tuple[tuple[int, str, str, float], ...] = ()
    guarded_voltage_min_pu: tuple[tuple[int, str, str, float], ...] = ()
    guarded_voltage_max_pu: tuple[tuple[int, str, str, float], ...] = ()


class MatrixBuilder:
    """Sparse row-based MILP builder with explicit bounds and integrality."""
    def __init__(self):
        self.cost=[]; self.lb=[]; self.ub=[]; self.integ=[]
        self.ri=[]; self.ci=[]; self.val=[]; self.row_lb=[]; self.row_ub=[]

    def var(self, cost=0., lo=0., hi=np.inf, integer=False):
        i=len(self.cost)
        self.cost.append(float(cost)); self.lb.append(float(lo));self.ub.append(float(hi))
        self.integ.append(int(bool(integer)))
        return i

    def row(self, items, lo=-np.inf, hi=np.inf):
        i=len(self.row_lb)
        for j,v in items:
            if abs(float(v))>1e-14:
                self.ri.append(i);self.ci.append(int(j));self.val.append(float(v))
        self.row_lb.append(float(lo));self.row_ub.append(float(hi))

    def solve(self, time_limit_s, mip_gap):
        shape=(len(self.row_lb),len(self.cost))
        A=coo_matrix((self.val,(self.ri,self.ci)),shape=shape).tocsr()
        # Duplicate sparse coefficients are intentionally summed by coo->csr.
        opts={"time_limit":float(time_limit_s),"mip_rel_gap":float(mip_gap),"disp":False}
        res=milp(c=np.asarray(self.cost), integrality=np.asarray(self.integ),
                 bounds=Bounds(self.lb,self.ub),
                 constraints=LinearConstraint(A,np.asarray(self.row_lb),np.asarray(self.row_ub)),
                 options=opts)
        if res.x is None or res.status not in (0,1):
            raise RuntimeError(f"Otimização MILP não produziu solução primal: status={res.status}; {res.message}")
        x=np.asarray(res.x,dtype=float)
        if not np.isfinite(x).all():
            raise RuntimeError("Otimização retornou valores não finitos")
        residual=A@x
        feas=float(max(np.max(np.maximum(np.asarray(self.row_lb)-residual,0.),initial=0.),
                       np.max(np.maximum(residual-np.asarray(self.row_ub),0.),initial=0.),
                       np.max(np.maximum(np.asarray(self.lb)-x,0.),initial=0.),
                       np.max(np.maximum(x-np.asarray(self.ub),0.),initial=0.)))
        if feas>0.005:
            raise RuntimeError(f"MILP devolveu uma solução com violação de restrição {feas:.6g}")
        return res,x,{"rows":shape[0],"variables":shape[1],"nonzeros":int(A.nnz),
                     "binary_variables":int(np.count_nonzero(self.integ)),"max_feasibility_residual":feas}


def _indexed(frame, keys, values):
    if frame.duplicated(keys).any():
        raise ValueError("Duplicatas em serie elétrica "+str(keys))
    return frame.set_index(keys)[values].sort_index()


def solve_joint(*, vehicles:Sequence, grid, station, battery, bess_bus:str, v2g_bus:str,
                source_s1:pd.DataFrame, lines_s1:pd.DataFrame, volt_s1:pd.DataFrame,
                lines_s0:pd.DataFrame, volt_s0:pd.DataFrame,
                ds_lines:pd.DataFrame, ds_volt:pd.DataFrame, ds_source:pd.DataFrame,
                fleet_s1_kw:np.ndarray, fleet_s1_per_vehicle_kw:np.ndarray|None, independent_evcs_kw:np.ndarray,
                discharge_window:tuple[int,int], no_charge_window:tuple[int,int],
                tariffs:np.ndarray, remuneration:np.ndarray,
                settings:OptSettings, mode:str="S4",
                network_reference_bess_kw:np.ndarray|None=None,
                network_reference_fleet_kw:np.ndarray|None=None) -> dict:
    """Solve a physical 24-h per-vehicle MILP around the supplied AC snapshots.

    *lines_s1/volt_s1*: OpenDSS snapshots with automatic controls at the
    network reference (S1 by default, or last AC-tested S4 in V0.9).
    *ds_*: frozen-control finite differences around the SAME baseline.
    Energy/time models remain EXACT (linear). Electrical constraints approximate
    the local AC response and are checked independently after solving.
    """
    from v2g.availability import availability, session_steps
    from v2g.strategies import fleet_profile

    if mode not in ("S2","S3","S4"):
        raise ValueError("mode deve ser S2, S3 ou S4")
    T=int(grid.steps);dt=float(grid.dt_h)
    if T!=24 or abs(dt-1.)>1e-12:
        raise ValueError("Este estudo foi deliberadamente limitado a 24 passos horarios")
    n=len(vehicles)
    basefleet=np.asarray(fleet_s1_kw,float)
    old_vehicle = None if fleet_s1_per_vehicle_kw is None else np.asarray(fleet_s1_per_vehicle_kw,float)
    if mode=="S2" and (old_vehicle is None or old_vehicle.shape!=(n,T)):
        raise ValueError("S2 precisa dos perfis S1 de cada veículo para mantê-los exatamente fixos")
    if old_vehicle is not None and not np.allclose(old_vehicle.sum(axis=0),basefleet,atol=0.001):
        raise ValueError("O perfil agregado S1 não coincide com os veículos S1 individuais")
    # Physical network reference may differ from the original S1 fleet schedule.
    # Keep fleet_s1_kw unchanged: it validates original per-vehicle inputs.
    ref_bess=np.zeros(T) if network_reference_bess_kw is None else np.asarray(network_reference_bess_kw,float)
    ref_fleet=basefleet.copy() if network_reference_fleet_kw is None else np.asarray(network_reference_fleet_kw,float)
    for name,a in (("network_reference_bess_kw",ref_bess),("network_reference_fleet_kw",ref_fleet)):
        if a.shape!=(T,) or not np.isfinite(a).all():
            raise ValueError(f"{name} precisa ter 24 valores finitos")
    for name,value in (("bess_trust_kw",settings.bess_trust_kw),("v2g_trust_kw",settings.v2g_trust_kw)):
        if value is not None and (not np.isfinite(value) or value<=0):
            raise ValueError(f"{name} deve ser positivo e finito")
    ind=np.asarray(independent_evcs_kw,float)
    tariffs=np.asarray(tariffs,float);remuneration=np.asarray(remuneration,float)
    for a in (basefleet,ind,tariffs,remuneration):
        if a.shape!=(T,) or not np.isfinite(a).all():
            raise ValueError("Um vetor horario do modelo MILP é inválido")
    if str(station.bus).lower()!=v2g_bus or bess_bus==v2g_bus:
        raise ValueError("Barramentos de conexão inesperados")
    if not (0<=discharge_window[0]<discharge_window[1]<=24):
        raise ValueError("Janela de descarga V2G inválida")
    if not (0<=no_charge_window[0]<no_charge_window[1]<=24):
        raise ValueError("Janela sem recarga V2G inválida")
    if np.any(ind>station.connection_kw) or np.any(ind<0):
        raise ValueError("Carga independente V2G ultrapassa a conexão")
    allowed=np.zeros(T,dtype=bool);allowed[slice(*discharge_window)]=True
    prohibited=np.zeros(T,dtype=bool);prohibited[slice(*no_charge_window)]=True
    if (allowed&(~prohibited)).any():
        raise ValueError("O fluxo V2G exige janelas de carga/descarga disjuntas")
    connected=np.array([availability(T,v.arrival,v.departure) for v in vehicles],bool)
    reserved_ports=np.ceil(np.maximum(ind-1e-9,0)/station.charger_kw)
    if np.any(reserved_ports+connected.sum(0)>station.chargers):
        raise ValueError("Mais veiculos conectados do que portas disponiveis")
    if np.any(ind+connected.sum(0)*station.charger_kw>station.connection_kw+1e-7):
        raise ValueError("A reserva conservadora de potencia excede a estacao")

    b=MatrixBuilder()
    # BESS: all hours. Binary disables simultaneous charge/discharge.
    kwmax=float(battery.power_kw);capacity=float(battery.capacity_kwh)
    em0=float(battery.soc_initial*capacity)
    charge=[b.var(settings.bess_throughput_usd_kwh*dt,0,kwmax) for _ in range(T)]
    disch=[b.var(settings.bess_throughput_usd_kwh*dt,0,kwmax) for _ in range(T)]
    energy=[b.var(0, (em0 if k in (0,T) else battery.soc_min*capacity+0.002),
                    (em0 if k in (0,T) else battery.soc_max*capacity-0.002)) for k in range(T+1)]
    if settings.battery_mode_binary:
        flags=[b.var(0,0,1,True) for _ in range(T)]
        for t in range(T):
            b.row([(charge[t],1),(flags[t],-kwmax)],hi=0)
            b.row([(disch[t],1),(flags[t],kwmax)],hi=kwmax)
    for t in range(T):
        b.row([(energy[t+1],1),(energy[t],-1),(charge[t],-battery.eta_charge*dt),
               (disch[t],dt/battery.eta_discharge)],lo=0,hi=0)
    bnet=[b.var(0, max(-kwmax,ref_bess[t]-settings.bess_trust_kw) if settings.bess_trust_kw is not None else -kwmax,
                 min(kwmax,ref_bess[t]+settings.bess_trust_kw) if settings.bess_trust_kw is not None else kwmax)
          for t in range(T)]
    for t in range(T):
        b.row([(bnet[t],1),(disch[t],-1),(charge[t],1)],lo=0,hi=0)
    if mode=="S3":
        for t in range(T):
            b.row([(bnet[t],1)],lo=0,hi=0)

    # Cesar: explicit per-vehicle power, SOC and departure constraints.
    # Each vehicle has ONE connected session; overnight sessions wrap the day.
    vc_by_t=[[] for _ in range(T)];vd_by_t=[[] for _ in range(T)]
    vehicle_blocks=[]
    for i,v in enumerate(vehicles):
        bat=v.battery
        order=list(session_steps(T,v.arrival,v.departure));K=len(order)
        e0=float(bat.soc_initial*bat.capacity_kwh)
        reserve=float(v.reserve_kwh);target=float(v.departure_target_kwh)
        pmax=min(float(bat.power_kw),float(station.charger_kw))
        vc=[];vd=[]
        # Restrict exporting to vehicles with enough arrival energy to preserve
        # their next-trip reserve; this is conservative but always auditable.
        can_discharge= bool(e0>=reserve+0.01 and any(allowed[t] for t in order))
        for t in order:
            if mode=="S2":
                # S1 uncoordinated charging may occur DURING the V2G no-charge window;
                # it must remain identical for an honest BESS-only comparison.
                old=float(old_vehicle[i,t]);
                if old>1e-6 or -old>pmax+1e-6:
                    raise ValueError(f"Veículo S1 {v.id} excede potência ou exporta em S2")
                clo=chi=max(0.,-old);dlo=dhi=0.
            else:
                clo=0.;chi=0. if prohibited[t] else pmax
                dlo=0.;dhi=pmax if allowed[t] and can_discharge else 0.
            vc.append(b.var(tariffs[t]*dt,clo,chi))
            vd.append(b.var((-remuneration[t]+settings.vehicle_degradation_usd_kwh)*dt,dlo,dhi))
            vc_by_t[t].append(vc[-1]);vd_by_t[t].append(vd[-1])
        ve=[]
        for k in range(K+1):
            eps=0. if mode=="S2" else 0.001
            lo=float(bat.soc_min*bat.capacity_kwh+eps)
            hi=float(bat.soc_max*bat.capacity_kwh-eps)
            if k==0:lo=hi=e0
            if k==K:lo=max(lo,target-1e-8 if mode=="S2" else target+0.001)
            if k>0 and allowed[order[k-1]] and can_discharge and mode!="S2":
                lo=max(lo,reserve+0.001)
            if lo>hi+1e-7:
                raise RuntimeError(f"V2G {v.id}: limite de energia impossivel em sessao {order}")
            ve.append(b.var(0,lo,hi))
        for k in range(K):
            b.row([(ve[k+1],1),(ve[k],-1),(vc[k],-bat.eta_charge*dt),
                   (vd[k],dt/bat.eta_discharge)],lo=0,hi=0)
        vehicle_blocks.append((v,order,vc,vd,ve))

    vnet=[b.var(0, max(-station.connection_kw,ref_fleet[t]-settings.v2g_trust_kw) if settings.v2g_trust_kw is not None else -station.connection_kw,
                 min(station.connection_kw,ref_fleet[t]+settings.v2g_trust_kw) if settings.v2g_trust_kw is not None else station.connection_kw)
          for t in range(T)]
    # The net fleet injection is DISCHARGE minus CHARGE. The independent station
    # demand belongs to EVCS and is already present in baseline S1.
    for t in range(T):
        b.row([(vnet[t],1)]+[(j,-1) for j in vd_by_t[t]]+
              [(j,1) for j in vc_by_t[t]],lo=0,hi=0)
        b.row([(vnet[t],-1)],hi=station.connection_kw-ind[t])
        b.row([(vnet[t],1)],hi=station.connection_kw+ind[t])
        if mode=="S2":
            # S2 must preserve EXACTLY the charging profile of S1.
            # The fleet charging is not reoptimized in the BESS-only benchmark.
            b.row([(vnet[t],1)],lo=basefleet[t],hi=basefleet[t])

    # Electric sensitivities. Use monitored line-phase / bus-phase, not IDs only.
    src=_indexed(source_s1,["hour"],["p_kw"])
    line0=_indexed(lines_s0,["hour","line","phase"],["loading_pct"])
    line1=_indexed(lines_s1,["hour","line","phase"],["loading_pct"])
    volt0=_indexed(volt_s0,["hour","bus","phase"],["v_pu"])
    volt1=_indexed(volt_s1,["hour","bus","phase"],["v_pu"])
    ls=_indexed(ds_lines,["hour","line","phase"],["dl_bess","dl_v2g"])
    vs=_indexed(ds_volt,["hour","bus","phase"],["dv_bess","dv_v2g"])
    ss=_indexed(ds_source,["hour"],["dp_bess","dp_v2g"])
    if set(line1.index)!=set(ls.index) or set(volt1.index)!=set(vs.index):
        raise ValueError("Sensibilidades não cobrem todas as linhas/fases e barras/fases do S1")
    if set(src.index.get_level_values("hour"))!=set(range(T)):
        raise ValueError("Faltam horarios nas potencias da fonte")
    thermal_keys={tuple(x) for x in line1[line1.loading_pct>=settings.thermal_screen_pct].index.droplevel(0).unique()}
    thermal_keys|={tuple(x) for x in line0[line0.loading_pct>=100.].index.droplevel(0).unique()}
    voltage_keys={tuple(x) for x in volt1[(volt1.v_pu<=.95+settings.voltage_screen_margin_pu)|
                                          (volt1.v_pu>=1.05-settings.voltage_screen_margin_pu)].index.droplevel(0).unique()}
    voltage_keys|={tuple(x) for x in volt0[(volt0.v_pu<.95)|(volt0.v_pu>1.05)].index.droplevel(0).unique()}
    # Important: grouping via set yields deterministic ordering.
    slack_idx={"thermal":[],"under":[],"over":[]}
    screen_counts={"line_phase":len(thermal_keys),"voltage_bus_phase":len(voltage_keys)}
    for t in range(T):
        for line,phase in sorted(thermal_keys):
            key=(t,line,phase)
            if key not in line1.index or key not in ls.index:continue
            base=float(line1.at[key,"loading_pct"])
            ref=float(line0.at[key,"loading_pct"]) if key in line0.index else 0.
            limit=max(100.,ref) if settings.relative_to_s0 else 100.
            k_b=float(ls.at[key,"dl_bess"]);k_v=float(ls.at[key,"dl_v2g"])
            z=b.var(settings.thermal_weight*dt,0,np.inf)
            slack_idx["thermal"].append((key,z,base,limit))
            b.row([(bnet[t],k_b),(vnet[t],k_v),(z,-1)],
                  hi=limit-base+k_b*ref_bess[t]+k_v*ref_fleet[t])
        for bus,phase in sorted(voltage_keys):
            key=(t,bus,phase)
            if key not in volt1.index or key not in vs.index:continue
            base=float(volt1.at[key,"v_pu"])
            ref=float(volt0.at[key,"v_pu"]) if key in volt0.index else base
            lo=min(.95,ref) if settings.relative_to_s0 else .95
            hi=max(1.05,ref) if settings.relative_to_s0 else 1.05
            k_b=float(vs.at[key,"dv_bess"]);k_v=float(vs.at[key,"dv_v2g"])
            zl=b.var(settings.undervoltage_weight*dt,0,np.inf)
            zu=b.var(settings.overvoltage_weight*dt,0,np.inf)
            slack_idx["under"].append((key,zl,base,lo))
            slack_idx["over"].append((key,zu,base,hi))
            b.row([(bnet[t],k_b),(vnet[t],k_v),(zl,1)],
                  lo=lo-base+k_b*ref_bess[t]+k_v*ref_fleet[t])
            b.row([(bnet[t],k_b),(vnet[t],k_v),(zu,-1)],
                  hi=hi-base+k_b*ref_bess[t]+k_v*ref_fleet[t])

    # V1.1 AC-guided guard rows. These targets are absolute predicted values,
    # not slacks: a constraint may be infeasible and the script must report that.
    # The true AC checks MUST be repeated with automatic OpenDSS controls.
    guard_counts={"line": 0, "voltage_lower": 0, "voltage_upper": 0}
    for kind, entries, physical, derivative, base_column, k_b_col, k_v_col, sense in (
        ("line", settings.guarded_line_max_pct, line1, ls, "loading_pct", "dl_bess", "dl_v2g", "hi"),
        ("voltage_lower", settings.guarded_voltage_min_pu, volt1, vs, "v_pu", "dv_bess", "dv_v2g", "lo"),
        ("voltage_upper", settings.guarded_voltage_max_pu, volt1, vs, "v_pu", "dv_bess", "dv_v2g", "hi"),
    ):
        if len(entries)!=len({(t, name, ph) for t, name, ph, _ in entries}):
            raise ValueError(f"Guarda {kind} duplicada")
        for hour, element, phase, bound in entries:
            hour=int(hour)
            key=(hour, str(element), str(phase))
            if not (0<=hour<T and np.isfinite(bound)):
                raise ValueError(f"Limite de guarda {kind} inválido em {key}")
            if key not in physical.index or key not in derivative.index:
                raise ValueError(f"Guarda {kind} ausente no OpenDSS em {key}")
            if kind=="line" and not (0<=bound<=250):
                raise ValueError(f"Guarda de carregamento fora de 0..250% em {key}")
            if kind!="line" and not (0.7<=bound<=1.3):
                raise ValueError(f"Guarda de tensão fora de 0,7..1,3 p.u. em {key}")
            base=float(physical.at[key,base_column])
            k_b=float(derivative.at[key,k_b_col]);k_v=float(derivative.at[key,k_v_col])
            if not np.isfinite([base,k_b,k_v]).all():
                raise ValueError(f"Coeficientes inválidos na guarda {key}")
            rhs=float(bound)-base+k_b*ref_bess[hour]+k_v*ref_fleet[hour]
            terms=[(bnet[hour],k_b),(vnet[hour],k_v)]
            if sense=="hi":
                b.row(terms,hi=rhs)
            else:
                b.row(terms,lo=rhs)
            guard_counts[kind]+=1

    # Epsilon-constraint formulation for an approximate local Pareto sweep.
    # Slack variables represent violations relative to max(100%, S0) and
    # min(0.95, S0)/max(1.05, S0) (when relative_to_s0=True).
    for attr,kind in (("max_thermal_slack_pph","thermal"),
                      ("max_under_slack_puh","under"),
                      ("max_over_slack_puh","over")):
        cap=getattr(settings,attr)
        if cap is None:
            continue
        if not np.isfinite(cap) or cap<0:
            raise ValueError(f"{attr} deve ser não negativo e finito")
        slack_ids=[z for _,z,_,_ in slack_idx[kind]]
        if slack_ids:
            b.row([(z,dt) for z in slack_ids], hi=float(cap))

    peak=b.var(settings.peak_weight,lo=0,hi=np.inf)
    for t in range(T):
        k_b=float(ss.at[t,"dp_bess"]);k_v=float(ss.at[t,"dp_v2g"])
        source_kw=float(src.at[t,"p_kw"])
        b.row([(bnet[t],k_b),(vnet[t],k_v),(peak,-1)],
              hi=-source_kw+k_b*ref_bess[t]+k_v*ref_fleet[t])

    res,x,model_stats=b.solve(settings.solver_time_limit_s,settings.mip_rel_gap)
    bess_net=np.array([x[k] for k in bnet]);v2g_net=np.array([x[k] for k in vnet])
    bess_c=np.array([max(0,x[k]) for k in charge]);bess_d=np.array([max(0,x[k]) for k in disch]);
    bess_e=np.array([x[k] for k in energy]);
    power=np.zeros((n,T))
    for i,(v,order,vc,vd,ve) in enumerate(vehicle_blocks):
        for k,t in enumerate(order):
            power[i,t]=x[vd[k]]-x[vc[k]]
    # Correct small roundoff only, without altering significant decisions.
    power[np.abs(power)<1e-7]=0.
    if not np.allclose(power.sum(axis=0),v2g_net,atol=1e-4):
        raise RuntimeError("LP net V2G não coincide com as decisões individuais")
    if not np.allclose(bess_d-bess_c,bess_net,atol=1e-4):
        raise RuntimeError("LP net BESS não coincide com carga/descarga")
    mask=allowed.copy()
    # Reuse Cesar's exact physical/operational validator (individual SOC,
    # availability, reserve, departure, charger ratings, power connection).
    fleet_profile(station,grid,list(vehicles),power,independent_evcs_kw=ind,
                  independent_ids=("independent-demand-group",),
                  discharge_mask=mask,enable_v2g=True)
    if mode=="S2" and not np.allclose(power,old_vehicle,atol=1e-5):
        raise RuntimeError("S2 alterou indevidamente a recarga de um veículo do S1")
    report={"status":"MILP_PRIMAL_FEASIBLE_NOT_YET_AC_VALIDATED",
            "mode":mode,"solver_status":int(res.status),"solver_message":str(res.message),
            "objective":float(res.fun),"mip_gap":None if getattr(res,'mip_gap',None) is None else float(res.mip_gap),
            "net_sensitivity_scope":"frozen regulator/capacitor states; linearized around supplied AC reference", 
            "reference_bess_net_kw":ref_bess.tolist(),"reference_fleet_net_kw":ref_fleet.tolist(),
            "trust_region_kw":{"bess":settings.bess_trust_kw,"v2g":settings.v2g_trust_kw},
            "screen":screen_counts,"ac_guided_linear_guard_counts":guard_counts,
            "model_stats":model_stats,
            "thermal_slack_sum_pct_h":float(sum(x[z] for _,z,_,_ in slack_idx["thermal"])*dt),
            "undervoltage_slack_sum_pu_h":float(sum(x[z] for _,z,_,_ in slack_idx["under"])*dt),
            "overvoltage_slack_sum_pu_h":float(sum(x[z] for _,z,_,_ in slack_idx["over"])*dt),
            "epsilon_caps_predicted_only":{
                "thermal_pph":settings.max_thermal_slack_pph,
                "under_puh":settings.max_under_slack_puh,
                "over_puh":settings.max_over_slack_puh,
            },
            "peak_linearized_kw":float(x[peak]),
            "bess_energy_initial_kwh":em0,
            "bess_energy_final_kwh":float(bess_e[-1]),
            "v2g_export_energy_kwh":float(np.maximum(power,0).sum()*dt),
            "v2g_recharge_energy_kwh":float(np.maximum(-power,0).sum()*dt)}
    return {"bess_net_kw":bess_net,"bess_charge_kw":bess_c,"bess_discharge_kw":bess_d,
            "bess_energy_kwh":bess_e,"v2g_net_kw":v2g_net,"vehicle_power_kw":power,
            "report":report}
