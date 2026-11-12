#!/usr/bin/env python3
"""V2.1-A — spatial BESS allocation and per-vehicle V2G station siting, IEEE 8500.

Scientifically controlled EXPERIMENT: the *per-vehicle S4 schedule remains fixed*
(its SOC/mobility was previously validated). This isolates the value of siting.
BESS siting, MW/MWh allocation and 24-hour dispatch are optimized jointly with
vehicle-to-station assignments. Original S1 fleet loads are REMOVED from host
before relocating the SAME 150 vehicles. Linear AC surrogate is checked with
24-hour OpenDSS automatic controls; there is NO claim of global AC feasibility.
V2.1-B will additionally optimize each vehicle's time schedule; V2.2 stochastic.

Run from the NEW project root:
 python -m distributed_stochastic.otimizar_siting_v21 \
   --repo . --baseline ../EVCS-BESS-V2G/results/replay_s0_s4_opendss \
   --probe-dir results/v21_sondagem_24h --output results/v21_siting_pilot
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

PENALTY_THERMAL=40.0
PENALTY_UNDER=5000.0
PENALTY_OVER=5000.0


def read_probes(folder:Path):
    path=folder/'sondagens_ac_v20.csv'
    raw=pd.read_csv(path)
    if set(raw.probe_kw.unique())!={200.,-200.}:
        raise ValueError('Esperadas as duas sondagens exatas: +200 e -200 kW')
    if set(raw.hour.unique())!=set(range(24)):
        raise ValueError('Faltam horários da calibração')
    groups=raw.groupby(['hour','bus','probe_kw'],sort=False)
    if any((g[['source_delta_kw','loss_delta_kw','change_thermal_severity_pp','change_undervoltage_severity_pu','change_overvoltage_severity_pu']].max()-g[['source_delta_kw','loss_delta_kw','change_thermal_severity_pp','change_undervoltage_severity_pu','change_overvoltage_severity_pu']].min()).abs().max()>1e-7
           for _,g in groups if len(g)>1):
        raise ValueError('Divergência entre registros BESS/V2G da mesma barra')
    probes=raw.drop_duplicates(['hour','bus','probe_kw']).copy()
    return raw,probes


def load_line_voltage_coeffs(folder:Path, names:set[str], selected_keys:set[tuple],
                             kind:str, base_col:str, derivative_col:str):
    """Read compressed OpenDSS derivatives in chunks; keep ONLY screened phase/hour keys."""
    path=folder/f'derivadas_{kind}.csv.gz'
    if not path.exists():raise FileNotFoundError(path)
    parts=[]
    chunk_cols=['hour',*(['line','phase'] if kind=='linhas' else ['bus','phase']),
                'bus_injection','probe_kw',derivative_col]
    keycols=['hour','line','phase'] if kind=='linhas' else ['hour','bus','phase']
    for ch in pd.read_csv(path,usecols=chunk_cols,chunksize=200000):
        ch=ch[ch.bus_injection.isin(names)].copy()
        if ch.empty:continue
        keys=pd.MultiIndex.from_frame(ch[keycols])
        keep=keys.isin(selected_keys)
        if keep.any():parts.append(ch.loc[keep])
    if not parts:raise RuntimeError(f'Matriz de sensibilidade sem elementos selecionados: {kind}')
    df=pd.concat(parts,ignore_index=True)
    if df.duplicated(keycols+['bus_injection','probe_kw']).any():
        raise RuntimeError('Sensibilidades duplicadas por barra-direção/elemento')
    return df


def build_indexed_coeffs(df:pd.DataFrame, field:str, element:str):
    """{(hour, element, phase): {(injection_bus, sign): slope}}"""
    value=f'd_{field}_per_kw'
    # voltage uses d_v_pu_per_kw, line uses d_loading_pct_per_kw
    slopes={}
    for row in df.itertuples(index=False):
        key=(int(row.hour),str(getattr(row,element)),str(row.phase))
        slopes.setdefault(key,{})[(str(row.bus_injection),int(math.copysign(1,row.probe_kw)))] = float(getattr(row,value))
    return slopes


def read_vehicle_matrix(filename:Path, ids:list[str]):
    vp=pd.read_csv(filename)
    needed={'time','vehicle_id','p_kw'}
    if not needed<=set(vp.columns):raise ValueError(f'{filename}: tabela de veículos inválida')
    vp['time']=pd.to_datetime(vp.time,utc=True)
    time_values=sorted(vp.time.unique())
    if len(time_values)!=24:raise ValueError('Faltam horas nos perfis individuais V2G')
    tmap={pd.Timestamp(t):i for i,t in enumerate(time_values)}
    vp['hour']=vp.time.map(tmap)
    piv=vp.pivot(index='vehicle_id',columns='hour',values='p_kw')
    if set(ids)!=set(piv.index) or set(piv.columns)!=set(range(24)):
        raise ValueError('Os 150 veículos não coincidem com as configurações')
    return piv.loc[ids,list(range(24))].to_numpy(float)


def choose_screened_keys(s1:pd.DataFrame,s0:pd.DataFrame,kind:str,crit:float):
    keys=['hour','line','phase'] if kind=='lines' else ['hour','bus','phase']
    col='loading_pct' if kind=='lines' else 'v_pu'
    x=s1[keys+[col]].merge(s0[keys+[col]],on=keys,how='left',validate='one_to_one',suffixes=('_s1','_s0'))
    if x[f'{col}_s0'].isna().any():raise ValueError('S0/S1 possuem elementos físicos distintos')
    if kind=='lines':
        subset=x.loc[(x.loading_pct_s1>=crit) | (x.loading_pct_s0>=100.)]
    else:
        # intentionally broad; all skipped elements MUST be checked in AC replay
        subset=x.loc[(x.v_pu_s1<=0.965)|(x.v_pu_s1>=1.04)| (x.v_pu_s0<=0.95)|(x.v_pu_s0>=1.05)]
    return subset.set_index(keys)


def forecast_constant(slope:dict,h:int,oldfleet:np.ndarray,host:str):
    """Removing historical S1 fleet charging at the host is a POSITIVE injection."""
    delta=-float(oldfleet[h])
    if delta < -1e-7:
        raise ValueError('S1 reference fleet should only CHARGE, not export')
    return delta*slope[(host,1)]


def build_injections(s1:pd.DataFrame,original_fleet:pd.DataFrame,
                     bess_net:dict[str,np.ndarray],car_power:np.ndarray,assignments:list[str]):
    """Move every individual V2G profile exactly ONCE; keep conventional EVCS unchanged."""
    keys=['hour','bus','phase']
    old=original_fleet.groupby(keys,as_index=False)[['p_kw','q_kvar']].sum()
    tmp=s1.merge(old,on=keys,how='outer',suffixes=('','_old'))
    for c in ['p_kw','q_kvar','p_kw_old','q_kvar_old']:tmp[c]=tmp[c].fillna(0.)
    tmp['p_kw']-=tmp['p_kw_old'];tmp['q_kvar']-=tmp['q_kvar_old']
    fixed=tmp[keys+['p_kw','q_kvar']]
    additions=[]
    for h in range(24):
        for b,profile in bess_net.items():
            if abs(profile[h])>1e-10:
                additions.extend(dict(hour=h,bus=b,phase=ph,p_kw=float(profile[h])/3.,q_kvar=0.) for ph in 'ABC')
        for i,b in enumerate(assignments):
            if abs(car_power[i,h])>1e-10:
                additions.extend(dict(hour=h,bus=b,phase=ph,p_kw=float(car_power[i,h])/3.,q_kvar=0.) for ph in 'ABC')
    result=pd.concat([fixed,pd.DataFrame(additions,columns=keys+['p_kw','q_kvar'])],ignore_index=True)
    result=result.groupby(keys,as_index=False)[['p_kw','q_kvar']].sum()
    expected=float(s1.p_kw.sum()-original_fleet.p_kw.sum()+car_power.sum()+sum(v.sum() for v in bess_net.values()))
    if abs(result.p_kw.sum()-expected)>1e-4:
        raise RuntimeError('Double counting or lost charging energy in S1 relocation')
    return result


def build_and_solve(*, bsites:list[str],vsites:list[str],s1l:pd.DataFrame,s0l:pd.DataFrame,
                    s1v:pd.DataFrame,s0v:pd.DataFrame,source:pd.DataFrame,
                    line_slope:dict,volt_slope:dict,source_slope:dict,
                    car_power:np.ndarray,oldfleet:np.ndarray,host:str,
                    n_bess:int=3,n_v2g:int=3,solver_seconds:int=300,mip_gap:float=.03,
                    thermal_weight:float=PENALTY_THERMAL,voltage_weight:float=PENALTY_UNDER,
                    site_penalty:float=10.0):
    # MatrixBuilder from the V1.1 installed OpenDSS reconstruction.
    from rebuild.joint_optimization import MatrixBuilder
    b=MatrixBuilder();T=24;nv=car_power.shape[0]
    if car_power.shape[1]!=24 or len(oldfleet)!=24:raise ValueError('Horizonte precisa ter 24 horas')
    if host not in vsites:raise ValueError('A barra antiga de V2G precisa integrar o conjunto de candidatos')
    yb={s:b.var(site_penalty,0,1,True) for s in bsites}
    yv={s:b.var(site_penalty if s!=host else 0.,0,1,True) for s in vsites}
    b.row([(j,1) for j in yb.values()],lo=1,hi=n_bess)
    b.row([(j,1) for j in yv.values()],lo=1,hi=n_v2g)
    capP={s:b.var(0,0,2200) for s in bsites}
    capE={s:b.var(0,0,7200) for s in bsites}
    for s in bsites:
        b.row([(capP[s],1),(yb[s],-2200)],hi=0)
        b.row([(capP[s],1),(yb[s],-100)],lo=0)
        b.row([(capE[s],1),(yb[s],-7200)],hi=0)
        b.row([(capE[s],1),(yb[s],-300)],lo=0)
        b.row([(capE[s],1),(capP[s],-1.5)],lo=0)  # min 1.5h storage
        b.row([(capE[s],1),(capP[s],-6.0)],hi=0)  # max 6h storage
    b.row([(j,1) for j in capP.values()],lo=2200,hi=2200)
    b.row([(j,1) for j in capE.values()],lo=7200,hi=7200)
    charging={}; discharging={}; states={}
    for s in bsites:
        charging[s]=[b.var(.03,0,2200) for _ in range(T)]
        discharging[s]=[b.var(.03,0,2200) for _ in range(T)]
        states[s]=[b.var(0,0,7200) for _ in range(T+1)]
        b.row([(states[s][0],1),(capE[s],-.30)],lo=0,hi=0)
        b.row([(states[s][-1],1),(capE[s],-.30)],lo=0,hi=0)
        for t in range(T):
            x=b.var(0,0,1,True)
            b.row([(charging[s][t],1),(capP[s],-1)],hi=0)
            b.row([(discharging[s][t],1),(capP[s],-1)],hi=0)
            b.row([(charging[s][t],1),(x,-2200)],hi=0)
            b.row([(discharging[s][t],1),(x,2200)],hi=2200)
            b.row([(states[s][t+1],1),(states[s][t],-1),(charging[s][t],-.95),
                   (discharging[s][t],1/.95)],lo=0,hi=0)
        for e in states[s]:
            b.row([(e,1),(capE[s],-.1)],lo=0)
            b.row([(e,1),(capE[s],-.9)],hi=0)
    # Assign each vehicle to ONE site for its complete previously-valid 24-hour schedule.
    assign={(i,s):b.var(0,0,1,True) for i in range(nv) for s in vsites}
    for i in range(nv):b.row([(assign[i,s],1) for s in vsites],lo=1,hi=1)
    for s in vsites:
        b.row([(assign[i,s],1) for i in range(nv)]+[(yv[s],-nv)],hi=0)
        b.row([(assign[i,s],1) for i in range(nv)]+[(yv[s],-1)],lo=0)
        # Dedicated bidirectional port per assigned vehicle, no assumed spare capacity.
    # Construct objective/constraints using the finite difference matrices.
    def perturb_items(h:int, slope:dict):
        if (host,1) not in slope:raise ValueError(f'Host sensitivity missing at h={h}')
        c=forecast_constant(slope,h,oldfleet,host)
        items=[]
        for s in bsites:
            items.append((discharging[s][h],slope[s,1]))
            items.append((charging[s][h],-slope[s,-1]))
        for s in vsites:
            pos=car_power[:,h]>=0
            for i in range(nv):
                p=float(car_power[i,h]);
                if abs(p)>1e-9:
                    items.append((assign[i,s],p*slope[s,1 if p>=0 else -1]))
        return c,items
    # Predict physically relative to S0: pre-existing overloads are not erased by definition.
    for (h,name,phase),r in s1l.iterrows():
        sl=line_slope[(int(h),str(name),str(phase))]
        c,items=perturb_items(int(h),sl)
        limit=max(100.,float(r.loading_pct_s0))
        slack=b.var(thermal_weight,0,1e4)
        b.row(items+[(slack,-1)],hi=limit-float(r.loading_pct_s1)-c)
    for (h,name,phase),r in s1v.iterrows():
        sl=volt_slope[(int(h),str(name),str(phase))]
        c,items=perturb_items(int(h),sl)
        lo=min(.95,float(r.v_pu_s0));hi=max(1.05,float(r.v_pu_s0))
        if float(r.v_pu_s1)<1.0:
            slack=b.var(voltage_weight,0,1e4)
            b.row(items+[(slack,1)],lo=lo-float(r.v_pu_s1)-c)
        if float(r.v_pu_s1)>1.0:
            slack=b.var(voltage_weight,0,1e4)
            b.row(items+[(slack,-1)],hi=hi-float(r.v_pu_s1)-c)
    # Approximate network peak; full OpenDSS will evaluate actual source power.
    peak=b.var(.5,0,100000)
    sourceh=source.set_index('hour').p_kw
    for h in range(T):
        c,items=perturb_items(h,source_slope[h])
        b.row(items+[(peak,-1)],hi=-float(sourceh.loc[h])-c)
    result,x,info=b.solve(solver_seconds,mip_gap)
    if result.status==1 and not np.isfinite(x).all():raise RuntimeError('MILP time limit without valid primal')
    selected_b=[s for s in bsites if x[yb[s]]>.5]
    selected_v=[s for s in vsites if x[yv[s]]>.5]
    assigned=[max(vsites,key=lambda s:x[assign[i,s]]) for i in range(nv)]
    if any(x[assign[i,assigned[i]]]<.999 for i in range(nv)):
        raise RuntimeError('Non-integral vehicle assignment')
    bess={s:np.array([x[discharging[s][t]]-x[charging[s][t]] for t in range(T)]) for s in bsites}
    soc={s:np.array([x[e]/x[capE[s]] for e in states[s]]) for s in selected_b}
    return dict(selected_bess=selected_b,selected_v2g=selected_v,
       allocation_bess={s:{'power_kw':float(x[capP[s]]),'energy_kwh':float(x[capE[s]])} for s in selected_b},
       bess_dispatch=bess,soc=soc,vehicle_sites=assigned,
       objective=float(result.fun),mip_status=int(result.status),mip_message=str(result.message),
       mip_gap=float(getattr(result,'mip_gap',float('nan'))),model_stats=info)


def run(args):
    repo=Path(args.repo).resolve(); baseline=Path(args.baseline).resolve()
    probe=Path(args.probe_dir).resolve();out=Path(args.output).resolve()
    for path in [baseline/'S1/branches.csv',baseline/'S1/voltages.csv',baseline/'S0/branches.csv',
                 baseline/'S0/voltages.csv',baseline/'S1/injections_replayed.csv',
                 baseline/'modules/S4/v2g/vehicle_power.csv',probe/'derivadas_linhas.csv.gz',
                 probe/'derivadas_tensoes.csv.gz']:
        if not path.exists():raise FileNotFoundError(path)
    sys.path.insert(0,str(repo/'opendss_rebuild'))
    from rebuild.contracts import load_context
    from rebuild.engine import ODSSEngine
    from reotimizar_bess_v2g import read_team,read_existing
    from v2g.strategies import fleet_profile
    from core.schemas import make_profile
    print('[V2.1] Lendo S0/S1 e 150 veículos... ',flush=True)
    references,s1=read_existing(baseline)
    team=read_team(repo,baseline)
    ids=[v.id for v in team['vehicles']]
    car_power=read_vehicle_matrix(baseline/'modules/S4/v2g/vehicle_power.csv',ids)
    if len(ids)!=150:raise ValueError('A frota precisa ter 150 veículos')
    # Physically validate original S4 schedules BEFORE moving connection buses.
    from v2g.strategies import fleet_profile
    mask=np.array([a<=i<b for i in range(24) for a,b in [team['discharge_window']]])
    fleet_profile(team['station'],team['grid'],team['vehicles'],car_power,team['ind'],(),mask,True)
    oldfleet=np.asarray(team['old_fleet_net'],float)
    raw,probes=read_probes(probe)
    bsites=sorted(set(raw.loc[raw.resource.eq('BESS'),'bus']))
    vsites=sorted(set(raw.loc[raw.resource.eq('V2G'),'bus']))
    if team['v2g_bus'] not in vsites:raise ValueError('Falta barra V2G original')
    if len(bsites)!=6 or len(vsites)!=7:raise ValueError(f'Conjuntos inesperados BESS/V2G: {len(bsites)}/{len(vsites)}')
    s1l=choose_screened_keys(references['S1']['branches'],references['S0']['branches'],'lines',args.thermal_screen_pct)
    s1v=choose_screened_keys(references['S1']['voltages'],references['S0']['voltages'],'volts',0)
    print(f'[V2.1] Matriz local: {len(s1l)} fases de linha, {len(s1v)} fases de tensão (24 h)',flush=True)
    line_frame=load_line_voltage_coeffs(probe,set(bsites+vsites),set(s1l.index),'linhas','loading_pct_base','d_loading_pct_per_kw')
    volt_frame=load_line_voltage_coeffs(probe,set(bsites+vsites),set(s1v.index),'tensoes','v_pu_base','d_v_pu_per_kw')
    line_slopes=build_indexed_coeffs(line_frame,'loading_pct','line')
    volt_slopes=build_indexed_coeffs(volt_frame,'v_pu','bus')
    del line_frame,volt_frame
    source_slopes={}
    for h in range(24):
        source_slopes[h]={}
        for _,r in probes.loc[probes.hour.eq(h)].iterrows():
            source_slopes[h][str(r.bus),int(math.copysign(1,r.probe_kw))]=float(r.source_delta_kw/r.probe_kw)
    if any(len(source_slopes[h])!=24 for h in range(24)):
        raise RuntimeError('Fonte sem 12 locais e ambas as direções por hora')
    print('[V2.1] Matrizes prontas; consistência verificada.',flush=True)
    if args.validate_only:
        out.mkdir(parents=True,exist_ok=True)
        status=dict(status='INPUTS_VALIDATED_ONLY_NO_MILP_NO_AC',n_bess_sites=len(bsites),n_v2g_sites=len(vsites),
                    n_physical_buses=len(set(bsites+vsites)),n_vehicles=len(ids),
                    n_screened_line_phases=len(s1l),n_screened_voltage_phases=len(s1v),
                    notes='S4 vehicle profiles fixed; no new solution computed')
        (out/'precheck_v21.json').write_text(json.dumps(status,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(status,indent=2,ensure_ascii=False),flush=True)
        return status
    print('[V2.1] Resolvendo MILP espacial/temporal (V2G com despacho individual S4 FIXO)...',flush=True)
    solution=build_and_solve(bsites=bsites,vsites=vsites,s1l=s1l,s0l=None,s1v=s1v,s0v=None,
            source=references['S1']['source'],line_slope=line_slopes,volt_slope=volt_slopes,
            source_slope=source_slopes,car_power=car_power,oldfleet=oldfleet,host=team['v2g_bus'],
            n_bess=args.max_bess_sites,n_v2g=args.max_v2g_sites,solver_seconds=args.time_limit_s,
            mip_gap=args.mip_gap,site_penalty=args.site_penalty)
    out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame([{'vehicle_id':v.id,'site':s} for v,s in zip(team['vehicles'],solution['vehicle_sites'])]).to_csv(out/'vehicle_site_assignment.csv',index=False)
    pd.DataFrame([{'site':s,**p} for s,p in solution['allocation_bess'].items()]).to_csv(out/'bess_siting_sizing.csv',index=False)
    pd.DataFrame([{'hour':t,'site':s,'p_kw':float(p[t])} for s,p in solution['bess_dispatch'].items() for t in range(24)]).to_csv(out/'bess_dispatch.csv',index=False)
    pd.DataFrame([{'hour':t,'site':s,'soc':float(p[t])} for s,p in solution['soc'].items() for t in range(25)]).to_csv(out/'bess_soc.csv',index=False)
    newprofiles=build_injections(s1,team['old_fleet_profile'],solution['bess_dispatch'],car_power,solution['vehicle_sites'])
    newprofiles.to_csv(out/'injections_reoptimized.csv',index=False)
    print('[V2.1] Replay AC automático 24h. MILP NÃO certifica viabilidade elétrica.',flush=True)
    ctx=load_context(repo,repo/'opendss_rebuild')
    engine=ODSSEngine(ctx,ctx['settings']['simulation']['source_mode'],ctx['settings']['simulation']['ampacity'])
    if engine.controls!='auto':raise RuntimeError('Necessário controle auto OpenDSS')
    engine.compile();engine.define_assets(newprofiles)
    ac={name:[] for name in ('source','voltages','branches','regulators','capacitors')}
    for h in range(24):
        rr=engine.solve_hour(h,float(ctx['multipliers'][h]),newprofiles)
        for name in ac:ac[name].extend(rr[name])
        print(f'[OpenDSS V2.1] h={h:02d} source={rr["source"][0]["p_kw"]:.1f} kW',flush=True)
    acdir=out/'AC';acdir.mkdir(exist_ok=True)
    for name,rows in ac.items():pd.DataFrame(rows).to_csv(acdir/f'{name}.csv',index=False)
    br=pd.DataFrame(ac['branches']);vo=pd.DataFrame(ac['voltages'])
    s0=references['S0'];s1r=references['S1']
    old=set(s0['branches'].loc[s0['branches'].loading_pct>100,'line'])
    new=set(br.loc[br.loading_pct>100,'line'])
    result=dict(status='MILP_SPATIAL_PILOT_AC_REPLAY_NOT_FULL_OPERATIONAL_FEASIBILITY',
                solver_status=solution['mip_status'],solver_message=solution['mip_message'],
                mip_gap=solution['mip_gap'], objective_surrogate=solution['objective'],
                bess_sites=solution['selected_bess'],v2g_sites=solution['selected_v2g'],
                bess_total_kw=sum(x['power_kw'] for x in solution['allocation_bess'].values()),
                bess_total_kwh=sum(x['energy_kwh'] for x in solution['allocation_bess'].values()),
                v2g_vehicle_count=len(solution['vehicle_sites']),
                v2g_site_vehicle_counts={s:solution['vehicle_sites'].count(s) for s in solution['selected_v2g']},
                max_line_loading_pct=float(br.loading_pct.max()),
                thermal_severity_pp_hour=float(np.maximum(br.loading_pct-100,0).sum()),
                new_distinct_overloaded_lines_vs_s0=len(new-old),
                overload_samples=int((br.loading_pct>100).sum()),
                minimum_voltage_pu=float(vo.v_pu.min()),maximum_voltage_pu=float(vo.v_pu.max()),
                voltage_violation_samples=int(((vo.v_pu<.95)|(vo.v_pu>1.05)).sum()),
                source_peak_kw=float(pd.DataFrame(ac['source']).p_kw.max()),
                solver_model=solution['model_stats'],
                limitations=['V2G S4 per-vehicle time schedules are FIXED; only vehicle site assignment is optimized',
                 'No stochastic scenarios, no vehicle travel-to-site costs, no site transformer headroom study',
                 'Linear surrogate local frozen-control slopes 200 kW; not globally valid',
                 'OpenDSS AC with automatic controls is the actual electrical check; violations may remain',
                 'Dedicated 11 kW bidirectional port per assigned car must be built or retrofitted',
                 'Objective weights and site_penalty are NOT verified CAPEX/cost estimates'])
    (out/'report_v21.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False),flush=True)
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',default='.')
    ap.add_argument('--baseline',required=True)
    ap.add_argument('--probe-dir',required=True)
    ap.add_argument('--output',default='results/v21_siting_pilot')
    ap.add_argument('--max-bess-sites',type=int,default=3)
    ap.add_argument('--max-v2g-sites',type=int,default=3)
    ap.add_argument('--thermal-screen-pct',type=float,default=70.)
    ap.add_argument('--time-limit-s',type=int,default=360)
    ap.add_argument('--mip-gap',type=float,default=.03)
    ap.add_argument('--site-penalty',type=float,default=10.)
    ap.add_argument('--validate-only',action='store_true',help='Confere entradas, 150 veículos e matrizes, sem executar MILP/OpenDSS')
    a=ap.parse_args()
    if not 1<=a.max_bess_sites<=6 or not 1<=a.max_v2g_sites<=7:ap.error('Número de locais fora do intervalo')
    run(a)


if __name__=='__main__':main()
