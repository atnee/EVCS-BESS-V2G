#!/usr/bin/env python3
"""V2.2 — restauração sequencial da viabilidade AC para projeto BESS–V2G distribuído.

Conserva o siting/sizing da V2.1 na primeira fase e reotimiza o despacho horario
individual dos 150 VEs e a carga/descarga dos BESS. Cada ponto de linearizacao e
cada candidato e avaliado no OpenDSS original com controles automaticos. O MILP
apenas fornece um candidato: nao certifica viabilidade da rede.

Sinal: p_kw > 0 injeta na rede; p_kw < 0 consome da rede.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import math
import sys
import traceback
import numpy as np
import pandas as pd

T=24
KEY_L=['hour','line','phase']
KEY_V=['hour','bus','phase']


def as_json(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=str),encoding='utf-8')


def get_indexed(df,keys,value):
    if df.duplicated(keys).any():raise ValueError(f'Chaves duplicadas: {keys}')
    return df.set_index(keys)[value].sort_index()


def score_ac(s0,ac, tol_load=1e-3,tol_v=1e-6):
    """Dois criterios INDEPENDENTES: absoluto e nao agravamento por fase/hora."""
    l=s0['branches'][KEY_L+['loading_pct']].merge(ac['branches'][KEY_L+['loading_pct']],on=KEY_L,
         how='outer',indicator=True,validate='one_to_one',suffixes=('_s0','_ac'))
    v=s0['voltages'][KEY_V+['v_pu']].merge(ac['voltages'][KEY_V+['v_pu']],on=KEY_V,
         how='outer',indicator=True,validate='one_to_one',suffixes=('_s0','_ac'))
    if (l._merge!='both').any() or (v._merge!='both').any():
        raise RuntimeError('Diferenca nos conjuntos fisicos de linha/barra-fase-hora')
    t_lim=np.maximum(100.,l.loading_pct_s0.to_numpy(float))
    v_min=np.minimum(.95,v.v_pu_s0.to_numpy(float));v_max=np.maximum(1.05,v.v_pu_s0.to_numpy(float))
    if set(l.hour)!=set(range(24)) or set(v.hour)!=set(range(24)):
        raise ValueError('Auditoria AC incompleta: necessario horizonte integral 0..23')
    la=l.loading_pct_ac.to_numpy(float); va=v.v_pu_ac.to_numpy(float)
    td=np.maximum(0.,la-t_lim);vd=np.maximum(0.,v_min-va)+np.maximum(0.,va-v_max)
    strict_l=(la>100.+tol_load);strict_v=(va<.95-tol_v)|(va>1.05+tol_v)
    ag_l=(la>t_lim+tol_load);ag_v=(va<v_min-tol_v)|(va>v_max+tol_v)
    old_lines=set(l.loc[l.loading_pct_s0>100.+tol_load,'line'])
    present_lines=set(l.loc[strict_l,'line'])
    peak=float(ac['source'].p_kw.max())
    return {'strict_pass':bool(not strict_l.any() and not strict_v.any()),
            'relative_s0_pass':bool(not ag_l.any() and not ag_v.any()),
            'strict_overload_samples':int(strict_l.sum()),'strict_voltage_samples':int(strict_v.sum()),
            'thermal_aggravation_samples':int(ag_l.sum()),'voltage_aggravation_samples':int(ag_v.sum()),
            'new_overloaded_lines_vs_s0':len(present_lines-old_lines),
            'thermal_aggravation_pp_h':float(td.sum()),'voltage_aggravation_pu_h':float(vd.sum()),
            'strict_thermal_pp_h':float(np.maximum(0.,la-100.).sum()),
            'strict_voltage_pu_h':float((np.maximum(0.,.95-va)+np.maximum(0.,va-1.05)).sum()),
            'max_loading_pct':float(la.max()),'vmin_pu':float(va.min()),'vmax_pu':float(va.max()),
            'source_peak_kw':peak,
            'score':float(100.*td.sum()+25000.*vd.sum()+.005*peak),
            'scope':'Todas as 24 horas, linha-fase e barra-fase do arquivo AC',
            'operational_certification':False}


def screen_keys(s0,ac,kind):
    keys=KEY_L if kind=='lines' else KEY_V
    c='loading_pct' if kind=='lines' else 'v_pu'
    table='branches' if kind=='lines' else 'voltages'
    q=s0[table][keys+[c]].merge(ac[table][keys+[c]],on=keys,suffixes=('_s0','_ac'),validate='one_to_one')
    if kind=='lines':
        keep=(q[c+'_s0']>=88)|(q[c+'_ac']>=88)
    else:
        # Guardar tambem pontos do S0 fora dos limites, mesmo se AC melhorou.
        keep=(q[c+'_s0']<.96)|(q[c+'_s0']>1.045)|(q[c+'_ac']<.96)|(q[c+'_ac']>1.045)
    return set(map(tuple,q.loc[keep,keys].itertuples(index=False,name=None)))


def inject_hour(frame,h,bus,amount):
    """Apenas alterar a injecao trifasica, sem modificar as cargas convencionais."""
    rows=frame[frame.hour==h].copy()
    for ph in 'ABC':
        mask=(rows.bus==bus)&(rows.phase==ph)
        if mask.any():rows.loc[mask,'p_kw']+=amount/3.
        else:rows=pd.concat([rows,pd.DataFrame([{'hour':h,'bus':bus,'phase':ph,
                                  'p_kw':amount/3.,'q_kvar':0.}])],ignore_index=True)
    return rows


def compare_replay(saved,actual):
    ans={}
    for table,keys,column,lim in [('branches',KEY_L,'loading_pct',.03),
                                  ('voltages',KEY_V,'v_pu',2e-5),('source',['hour'],'p_kw',3.)]:
        joined=saved[table][keys+[column]].merge(actual[table][keys+[column]],on=keys,
                  suffixes=('_ref','_replay'),indicator=True,validate='one_to_one',how='outer')
        if (joined._merge!='both').any():raise RuntimeError(f'IDs nao correspondem no replay {table}')
        err=float((joined[column+'_ref']-joined[column+'_replay']).abs().max())
        if err>lim:raise RuntimeError(f'Replay divergente no {table}: {err:.8g} > {lim}')
        ans[table]=err
    return ans


def read_ac_folder(path):
    return {kind:pd.read_csv(Path(path)/(kind+'.csv')) for kind in ('branches','voltages','source','regulators','capacitors')}


def make_engine(repo,profiles,site_caps):
    sys.path.insert(0,str(repo/'opendss_rebuild'))
    from rebuild.contracts import load_context
    from rebuild.engine import ODSSEngine
    ctx=load_context(repo,repo/'opendss_rebuild')
    engine=ODSSEngine(ctx,ctx['settings']['simulation']['source_mode'],ctx['settings']['simulation']['ampacity'])
    if engine.controls!='auto':raise ValueError('V2.2 exige controles OpenDSS automaticos')
    engine.compile()
    dummy=pd.DataFrame([dict(hour=-1,bus=s,phase=p,p_kw=(kw+500.)/3.,q_kvar=0.)
                          for s,kw in site_caps.items() for p in 'ABC'])
    frame=pd.concat([profiles,dummy],ignore_index=True)
    engine.define_assets(frame)
    for s in site_caps:
        if ctx['buses'].get(s)!=set('ABC'):
            raise ValueError(f'Local selecionado nao suporta tres fases fisicas em OpenDSS: {s}')
    return ctx,engine


def run_ac(repo,profiles,site_caps,out):
    ctx,engine=make_engine(repo,profiles,site_caps)
    answers={k:[] for k in ('branches','voltages','source','regulators','capacitors')}
    for h in range(24):
        result=engine.solve_hour(h,float(ctx['multipliers'][h]),profiles)
        for k in answers:answers[k].extend(result[k])
        print(f'  [AC] h={h:02d}: importacao={result["source"][0]["p_kw"]:.1f} kW',flush=True)
    data={k:pd.DataFrame(v) for k,v in answers.items()}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for k,df in data.items():df.to_csv(out/(k+'.csv'),index=False)
    return data


def calibrate(repo,profiles,reference,s0,site_caps,delta,out):
    """Jacobian piecewise em torno da solucao AC atual; controles congelados APOS cada snapshot."""
    if delta<=0:raise ValueError('delta_kw deve ser positivo')
    from rebuild.engine import error_checked  # imported after project path is inserted by make_engine
    ctx,engine=make_engine(repo,profiles,site_caps)
    line_keys=screen_keys(s0,reference,'lines');volt_keys=screen_keys(s0,reference,'voltages')
    # Save only electrical constraints screened, always evaluating ALL in postsolve AC.
    slopes_l={};slopes_v={};slopes_src={};actual={k:[] for k in ('branches','voltages','source','regulators','capacitors')}
    for h in range(24):
        base=engine.solve_hour(h,float(ctx['multipliers'][h]),profiles)
        for k in actual: actual[k].extend(base[k])
        base_l={(r['hour'],r['line'],r['phase']):r['loading_pct'] for r in base['branches']
                if (r['hour'],r['line'],r['phase']) in line_keys}
        base_v={(r['hour'],r['bus'],r['phase']):r['v_pu'] for r in base['voltages']
                if (r['hour'],r['bus'],r['phase']) in volt_keys}
        p0=float(base['source'][0]['p_kw'])
        error_checked(engine.dss,'Set controlmode=off')
        for site in sorted(site_caps):
            for sign in (+1,-1):
                rows=inject_hour(profiles,h,site,sign*delta)
                engine.update_assets(rows)
                error_checked(engine.dss,'Solve')
                if not engine.dss.Solution.Converged():
                    raise RuntimeError(f'Perturbacao nao convergiu: h={h} {site} sign={sign}')
                pert=engine.read_results(h)
                div=sign*delta
                for r in pert['branches']:
                    key=(h,r['line'],r['phase'])
                    if key in base_l:slopes_l.setdefault(key,{})[(site,sign)]=(r['loading_pct']-base_l[key])/div
                for r in pert['voltages']:
                    key=(h,r['bus'],r['phase'])
                    if key in base_v:slopes_v.setdefault(key,{})[(site,sign)]=(r['v_pu']-base_v[key])/div
                slopes_src.setdefault(h,{})[(site,sign)]=(pert['source'][0]['p_kw']-p0)/div
                engine.update_assets(profiles[profiles.hour==h])
                error_checked(engine.dss,'Solve')
                if not engine.dss.Solution.Converged():raise RuntimeError('Falha ao restaurar estado base AC')
        error_checked(engine.dss,'Set controlmode=static')
        print(f'  [Derivadas] h={h:02d}; {len(site_caps)*2} perturbacoes; {len(base_l)} linhas-fase; {len(base_v)} barras-fase',flush=True)
    real={k:pd.DataFrame(v) for k,v in actual.items()}
    errors=compare_replay(reference,real)
    # Ensure every selected element has + and - for each site.
    expected={(s,sign) for s in site_caps for sign in (-1,1)}
    for label,dct in [('thermal',slopes_l),('voltage',slopes_v),('source',slopes_src)]:
        if any(set(v)!=expected for v in dct.values()):raise RuntimeError(f'Sensibilidades incompletas: {label}')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame([{'hour':k[0],'line':k[1],'phase':k[2],'site':loc[0],
                   'direction':loc[1],'d_loading_pct_per_kw':val}
                  for k,locs in slopes_l.items() for loc,val in locs.items()]).to_csv(
                  out/'derivadas_linhas_v22.csv.gz',index=False,compression='gzip')
    pd.DataFrame([{'hour':k[0],'bus':k[1],'phase':k[2],'site':loc[0],
                   'direction':loc[1],'d_v_pu_per_kw':val}
                  for k,locs in slopes_v.items() for loc,val in locs.items()]).to_csv(
                  out/'derivadas_tensoes_v22.csv.gz',index=False,compression='gzip')
    pd.DataFrame([{'hour':h,'site':loc[0],'direction':loc[1],'d_source_kw_per_kw':val}
                  for h,locs in slopes_src.items() for loc,val in locs.items()]).to_csv(
                  out/'derivadas_fonte_v22.csv',index=False)
    as_json(out/'calibration_manifest.json',{'baseline_replay_errors':errors,'delta_kw':delta,
      'buses':sorted(site_caps),'line_constraints':len(slopes_l),'voltage_constraints':len(slopes_v),
      'type':'LOCAL_FROZEN_CONTROL_PIECEWISE_NOT_AC_CERTIFICATE'})
    return {'lines':slopes_l,'volts':slopes_v,'source':slopes_src,'errors':errors}


def make_baseline_controls(team,solution_dir,baseline):
    from distributed_stochastic.otimizar_siting_v21 import read_vehicle_matrix,build_injections
    ids=[v.id for v in team['vehicles']]
    fleet=read_vehicle_matrix(baseline/'modules/S4/v2g/vehicle_power.csv',ids)
    assigned=pd.read_csv(solution_dir/'vehicle_site_assignment.csv')
    chosen=dict(zip(assigned.vehicle_id,assigned.site))
    if set(ids)!=set(chosen):raise ValueError('Alocacao de 150 veiculos incompleta ou duplicada')
    locations=[str(chosen[i]).lower() for i in ids]
    sizing=pd.read_csv(solution_dir/'bess_siting_sizing.csv')
    if not np.isclose(sizing.power_kw.sum(),2200.,atol=1e-3) or not np.isclose(sizing.energy_kwh.sum(),7200.,atol=1e-3):
        raise ValueError('Capacidade BESS V2.1 nao coincide com o contrato: 2200 kW / 7200 kWh')
    cap={str(r.site).lower():{'power_kw':float(r.power_kw),'energy_kwh':float(r.energy_kwh)} for r in sizing.itertuples()}
    d=pd.read_csv(solution_dir/'bess_dispatch.csv')
    old={site:d.loc[d.site==site].sort_values('hour').p_kw.to_numpy(float) for site in cap}
    if any(a.shape!=(24,) for a in old.values()):raise ValueError('Despacho V2.1 sem 24 horas')
    return fleet,locations,cap,old


def make_milp(team,vehicle_places,cap,current_bess,current_cars,base_ac,s0,slopes,
              *,criterion='relative',trust_bess=300.,trust_v2g=200.,time_limit=360,mip_gap=.03):
    """Fixed site locations, 24-hour optimal recourse for all batteries and every individual vehicle."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'opendss_rebuild'))
    from rebuild.joint_optimization import MatrixBuilder
    from v2g.availability import session_steps
    from v2g.strategies import fleet_profile
    b=MatrixBuilder();vehicles=team['vehicles'];sitesB=sorted(cap);sitesV=sorted(set(vehicle_places));T=24
    sites=sorted(set(sitesB+sitesV)); dt=1.
    if criterion not in ('relative','strict'):raise ValueError('criterion deve ser relative ou strict')
    if len(vehicle_places)!=len(vehicles) or current_cars.shape!=(len(vehicles),24):raise ValueError('Veiculos invalidos')
    if set(current_bess)!=set(sitesB):raise ValueError('BESS inconsistente')
    # Numeric control for BESS: no charge at 17h-19h in this diagnostic stage.
    b_charge={};b_discharge={};b_soc={};b_net={};b_bin={}
    for site in sitesB:
        pw=float(cap[site]['power_kw']);E=float(cap[site]['energy_kwh']);e0=.3*E
        b_charge[site]=[];b_discharge[site]=[];b_bin[site]=[]
        b_soc[site]=[b.var(0, (e0 if t in (0,T) else .1*E),
                                (e0 if t in (0,T) else .9*E)) for t in range(T+1)]
        for t in range(T):
            c=b.var(.03,0.,0. if t in (17,18,19) else pw);d=b.var(.03,0.,pw)
            flag=b.var(0.,0,1,True)
            b.row([(c,1),(flag,-pw)],hi=0)
            b.row([(d,1),(flag,pw)],hi=pw)
            b.row([(b_soc[site][t+1],1),(b_soc[site][t],-1),(c,-.95*dt),(d,dt/.95)],lo=0,hi=0)
            b_charge[site].append(c);b_discharge[site].append(d);b_bin[site].append(flag)
        b_net[site]=[b.var(0,-pw,pw) for _ in range(T)]
        for t in range(T):b.row([(b_net[site][t],1),(b_discharge[site][t],-1),(b_charge[site][t],1)],lo=0,hi=0)
    v_charge=[];v_discharge=[];v_soc=[];v_net={s:[b.var(0.,-len([x for x in vehicle_places if x==s])*11.,len([x for x in vehicle_places if x==s])*11.) for _ in range(T)] for s in sitesV}
    vc_groups={(s,t):[] for s in sitesV for t in range(T)};vd_groups={(s,t):[] for s in sitesV for t in range(T)}
    for i,v in enumerate(vehicles):
        battery=v.battery;order=session_steps(T,v.arrival,v.departure);limit=min(11.,float(battery.power_kw));
        initial=float(battery.soc_initial*battery.capacity_kwh);target=float(v.departure_target_kwh)
        e=[b.var(0,initial if k==0 else battery.soc_min*battery.capacity_kwh,
                      initial if k==0 else battery.soc_max*battery.capacity_kwh) for k in range(len(order)+1)]
        b.row([(e[-1],1)],lo=target)
        vc={};vd={}
        for k,t in enumerate(order):
            c=b.var(.001,0,0. if 17<=t<22 else limit)
            # windows disjoint: no simultaneous charging and discharging for any vehicle.
            d=b.var(.002,0,limit if 17<=t<20 else 0.)
            vc[t]=c;vd[t]=d
            b.row([(e[k+1],1),(e[k],-1),(c,-battery.eta_charge*dt),(d,dt/battery.eta_discharge)],lo=0,hi=0)
            if 17<=t<20:
                # Reserva de viagem so e exigida se o veiculo realmente descarregar.
                # Sem a binaria, EVs com SOC inicial abaixo da reserva tornariam
                # o MILP inviavel mesmo quando nao participam do V2G.
                z=b.var(0,0,1,True)
                b.row([(d,1),(z,-limit)],hi=0)
                emin=float(battery.soc_min*battery.capacity_kwh)
                b.row([(e[k+1],1),(z,-(float(v.reserve_kwh)-emin))],lo=emin)
            vc_groups[vehicle_places[i],t].append(c);vd_groups[vehicle_places[i],t].append(d)
        v_charge.append(vc);v_discharge.append(vd);v_soc.append((order,e))
    for site in sitesV:
        for t in range(T):
            b.row([(v_net[site][t],1)] + [(j,1) for j in vc_groups[site,t]] +
                  [(j,-1) for j in vd_groups[site,t]],lo=0,hi=0)
    # Define Δ injection at every physical connection. Piecewise direction with binary selection.
    dpos={};dneg={};mod={}
    for s in sites:
        for t in range(T):
            old=(float(current_bess[s][t]) if s in sitesB else 0.) + (
                    float(sum(current_cars[i,t] for i,place in enumerate(vehicle_places) if place==s)) if s in sitesV else 0.)
            trust=max(trust_bess if s in sitesB else 0.,trust_v2g if s in sitesV else 0.)
            if s in sitesB and t in (17,18,19):
                # The incumbent V2.1 was CHARGING during the peak. Prohibiting
                # that behavior requires a change greater than some trust
                # windows: enlarge ONLY the necessary local shift to reach 0.
                trust=max(trust,max(0.,-float(current_bess[s][t]))+1.)
            plus=b.var(.02,0,trust);minus=b.var(.02,0,trust);flag=b.var(0,0,1,True)
            expr=[(plus,-1),(minus,1)]
            if s in sitesB:expr.append((b_net[s][t],1))
            if s in sitesV:expr.append((v_net[s][t],1))
            b.row(expr,lo=old,hi=old)
            b.row([(plus,1),(flag,-trust)],hi=0)
            b.row([(minus,1),(flag,trust)],hi=trust)
            dpos[s,t]=plus;dneg[s,t]=minus
            mod[s,t]=old
    # All linear constraints use CURRENT AC as intercept, not S1.
    linebase=get_indexed(base_ac['branches'],KEY_L,'loading_pct');voltagebase=get_indexed(base_ac['voltages'],KEY_V,'v_pu')
    line0=get_indexed(s0['branches'],KEY_L,'loading_pct');voltage0=get_indexed(s0['voltages'],KEY_V,'v_pu')
    if not linebase.index.equals(line0.index) or not voltagebase.index.equals(voltage0.index):
        raise RuntimeError('Elementos fisicos S0 e candidato nao coincidem')
    all_slacks={'thermal':[],'under':[],'over':[]}
    def electric_terms(d,h):
        result=[]
        for s in sites:
            result.append((dpos[s,h],float(d[s,1])))
            result.append((dneg[s,h],-float(d[s,-1])))
        return result
    for key,coeff in slopes['lines'].items():
        h=int(key[0]);lim=100. if criterion=='strict' else max(100.,float(line0[key]))
        slack=b.var(100.,0.,1000.);all_slacks['thermal'].append(slack)
        b.row(electric_terms(coeff,h)+[(slack,-1)],hi=lim-float(linebase[key]))
    for key,coeff in slopes['volts'].items():
        h=int(key[0]);vl=.95 if criterion=='strict' else min(.95,float(voltage0[key]));vh=1.05 if criterion=='strict' else max(1.05,float(voltage0[key]))
        sla=b.var(25000.,0.,.5);slb=b.var(25000.,0.,.5)
        all_slacks['under'].append(sla);all_slacks['over'].append(slb)
        b.row(electric_terms(coeff,h)+[(sla,1)],lo=vl-float(voltagebase[key]))
        b.row(electric_terms(coeff,h)+[(slb,-1)],hi=vh-float(voltagebase[key]))
    peak=b.var(.005,0.,50000.)
    src=get_indexed(base_ac['source'],['hour'],'p_kw')
    for h in range(T):
        b.row(electric_terms(slopes['source'][h],h)+[(peak,-1)],hi=-float(src[h]))
    res,x,info=b.solve(time_limit,mip_gap)
    out_bess={s:np.array([x[b_net[s][t]] for t in range(T)],float) for s in sitesB}
    out_cars=np.zeros_like(current_cars)
    for i in range(len(vehicles)):
        for t in v_charge[i]:out_cars[i,t]=float(x[v_discharge[i][t]]-x[v_charge[i][t]])
    # Strict operation-model validation before electrical replay.
    mask=np.array([17<=t<20 for t in range(T)],bool)
    fleet_profile(team['station'],team['grid'],vehicles,out_cars,team['ind'],(),mask,True)
    for s,p in out_bess.items():
        capE=cap[s]['energy_kwh'];en=.3*capE;states=[en]
        for power in p:
            en+=.95*max(-power,0.)-max(power,0.)/.95
            states.append(en)
        if min(states)<.1*capE-1e-4 or max(states)>.9*capE+1e-4 or abs(states[-1]-.3*capE)>1e-3:
            raise RuntimeError(f'SOC BESS violado apos o MILP em {s}')
        if any(p[t]<-1e-6 for t in (17,18,19)):raise RuntimeError(f'Recarga BESS na ponta: {s}')
    return {'bess':out_bess,'cars':out_cars,'solver_status':int(res.status),
            'solver_message':str(res.message),'mip_gap':float(getattr(res,'mip_gap',float('nan'))),
            'objective':float(res.fun),'model_stats':info,
            'slack_sum':{k:float(sum(x[j] for j in vec)) for k,vec in all_slacks.items()},
            'trust_bess_kw':trust_bess,'trust_v2g_kw':trust_v2g}


def choose_result(previous,candidate,min_improve=.0005):
    if candidate['strict_pass'] and not previous['strict_pass']:return True,'strict_feasibility_reached'
    if candidate['relative_s0_pass'] and not previous['relative_s0_pass']:return True,'relative_s0_feasibility_reached'
    if candidate['new_overloaded_lines_vs_s0']>previous['new_overloaded_lines_vs_s0']:
        return False,'new_distinct_overloaded_lines_increased'
    if (candidate['score']<previous['score']*(1-min_improve)):
        return True,'AC_residual_weighted_score_improved'
    return False,'AC_score_did_not_improve'


def write_dispatch(path,bess,car_power,vehicles,vehicle_places,cap):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    pd.DataFrame([{'hour':h,'site':s,'p_kw':float(v[h])} for s,v in bess.items() for h in range(24)]).to_csv(path/'bess_dispatch.csv',index=False)
    pd.DataFrame([{'vehicle_id':v.id,'site':vehicle_places[i],'hour':h,'p_kw':float(car_power[i,h])}
                  for i,v in enumerate(vehicles) for h in range(24)]).to_csv(path/'vehicle_dispatch.csv',index=False)
    pd.DataFrame([{'site':s,**size} for s,size in cap.items()]).to_csv(path/'bess_siting_sizing.csv',index=False)
    states=[]
    for site,p in bess.items():
        capacity=cap[site]['energy_kwh'];energy=.3*capacity
        states.append({'site':site,'hour':0,'soc':.3,'energy_kwh':energy})
        for h,power in enumerate(p):
            energy+=.95*max(-power,0.)-max(power,0.)/.95
            states.append({'site':site,'hour':h+1,'soc':energy/capacity,'energy_kwh':energy})
    pd.DataFrame(states).to_csv(path/'bess_soc.csv',index=False)
    from v2g.availability import session_energy
    states=[]
    for i,v in enumerate(vehicles):
        e=session_energy(car_power[i],v.battery,v.arrival,v.departure,1.)
        for h,val in enumerate(e):
            states.append({'vehicle_id':v.id,'site':vehicle_places[i],
                           'hour_boundary':h,'soc':float(val/v.battery.capacity_kwh)})
    pd.DataFrame(states).to_csv(path/'vehicle_soc.csv.gz',index=False,compression='gzip')


def run(args):
    repo=Path(args.repo).resolve();baseline=Path(args.baseline).resolve();v21=Path(args.v21_dir).resolve();output=Path(args.output).resolve()
    sys.path.insert(0,str(repo/'opendss_rebuild'));sys.path.insert(0,str(repo/'src'));sys.path.insert(0,str(repo))
    from reotimizar_bess_v2g import read_existing,read_team
    from distributed_stochastic.otimizar_siting_v21 import build_injections
    refs,s1=read_existing(baseline)
    team=read_team(repo,baseline)
    cars,locations,cap,bess=make_baseline_controls(team,v21,baseline)
    if len(cars)!=150 or len(set(locations))>3 or len(cap)>3:raise ValueError('Configuracao espacial inesperada')
    site_caps={s:float(size['power_kw']) for s,size in cap.items()}
    for place in set(locations):site_caps[place]=max(site_caps.get(place,0.),11.*locations.count(place))
    profiles=build_injections(s1,team['old_fleet_profile'],bess,cars,locations)
    saved=pd.read_csv(v21/'injections_reoptimized.csv')
    cols=['hour','bus','phase','p_kw','q_kvar']
    j=profiles.merge(saved,on=cols[:3],suffixes=('_rebuild','_saved'),validate='one_to_one',how='outer',indicator=True)
    if (j._merge!='both').any() or max((j[f'{x}_rebuild']-j[f'{x}_saved']).abs().max() for x in ('p_kw','q_kvar'))>1e-3:
        raise RuntimeError('As injecoes reconstruidas nao coincidem com a V2.1. Abortar.')
    incumbent_ac=read_ac_folder(v21/'AC')
    incumbent=score_ac(refs['S0'],incumbent_ac)
    if args.validate_only:
        output.mkdir(parents=True,exist_ok=True)
        as_json(output/'precheck_v22.json',{'sites_bess':list(cap),'sites_v2g':sorted(set(locations)),
           'n_vehicles':len(cars),'initial_ac':incumbent,'state':'INPUTS_CHECKED_NO_MILP'})
        print('[V2.2] Precheck OK; resultados em',output)
        return
    output.mkdir(parents=True,exist_ok=True)
    as_json(output/'baseline_v21_score.json',incumbent)
    pd.DataFrame([{'vehicle_id':v.id,'site':s} for v,s in zip(team['vehicles'],locations)]).to_csv(output/'vehicle_site_assignment.csv',index=False)
    # Preserve a complete usable incumbent even if all correction attempts fail.
    write_dispatch(output/'best',bess,cars,team['vehicles'],locations,cap)
    profiles.to_csv(output/'best'/'injections_reoptimized.csv',index=False)
    (output/'best'/'AC').mkdir(parents=True,exist_ok=True)
    for kind,table in incumbent_ac.items():table.to_csv(output/'best'/'AC'/(kind+'.csv'),index=False)
    as_json(output/'best'/'ac_score.json',incumbent)
    accepted=0;attempts=[];best_score=incumbent;original_score=incumbent
    for iteration in range(1,args.max_iterations+1):
        print(f'\n[V2.2] Iteracao {iteration}: recalibracao OpenDSS no candidato atual.',flush=True)
        calib=calibrate(repo,profiles,incumbent_ac,refs['S0'],site_caps,args.delta_kw,output/f'iter_{iteration:02d}'/'calibration')
        done=False
        for backtrack in range(args.backtracks+1):
            trustB=args.bess_trust_kw*(.5**backtrack);trustV=args.v2g_trust_kw*(.5**backtrack)
            trial=output/f'iter_{iteration:02d}'/f'trial_{backtrack:02d}'
            try:
                sol=make_milp(team,locations,cap,bess,cars,incumbent_ac,refs['S0'],calib,
                    criterion=args.criterion,trust_bess=trustB,trust_v2g=trustV,
                    time_limit=args.time_limit_s,mip_gap=args.mip_gap)
                write_dispatch(trial,sol['bess'],sol['cars'],team['vehicles'],locations,cap)
                trial_profile=build_injections(s1,team['old_fleet_profile'],sol['bess'],sol['cars'],locations)
                trial_profile.to_csv(trial/'injections_reoptimized.csv',index=False)
                ac=run_ac(repo,trial_profile,site_caps,trial/'AC')
                quality=score_ac(refs['S0'],ac)
                accept,why=choose_result(incumbent,quality,args.min_improve)
                status={'iteration':iteration,'backtrack':backtrack,'state':'AC_ACCEPTED' if accept else 'AC_REJECTED',
                        'reason':why,'ac_score':quality,'milp':{k:sol[k] for k in ('solver_status','solver_message','mip_gap','objective','model_stats','slack_sum')},
                        'trust_bess_kw':trustB,'trust_v2g_kw':trustV}
                as_json(trial/'report_v22.json',status)
                attempts.append({'iteration':iteration,'backtrack':backtrack,'state':status['state'],
                                 'ac_score':quality['score'],'relative_pass':quality['relative_s0_pass'],
                                 'strict_pass':quality['strict_pass'],'milp_gap':sol['mip_gap']})
                print(f'  [AC] score {quality["score"]:.3f} (antes {incumbent["score"]:.3f}), aceite={accept}',flush=True)
                if accept:
                    incumbent=quality;incumbent_ac=ac;profiles=trial_profile;cars=sol['cars'];bess=sol['bess'];accepted+=1
                    write_dispatch(output/'best',bess,cars,team['vehicles'],locations,cap)
                    profiles.to_csv(output/'best'/'injections_reoptimized.csv',index=False)
                    for k,df in ac.items():
                        (output/'best'/'AC').mkdir(parents=True,exist_ok=True)
                        df.to_csv(output/'best'/'AC'/f'{k}.csv',index=False)
                    as_json(output/'best'/'ac_score.json',quality)
                    done=True
                    break
            except Exception as e:
                attempts.append({'iteration':iteration,'backtrack':backtrack,'state':'ERROR','error':str(e)})
                as_json(trial/'error.json',{'error':str(e),'traceback':traceback.format_exc()})
                print(f'  [Falhou] trial {iteration}.{backtrack}: {e}',flush=True)
        if not done:break
        if incumbent['strict_pass'] or (args.stop_relative and incumbent['relative_s0_pass']):break
    as_json(output/'final_report_v22.json',{'status':'AC_STRICT_PASS' if incumbent['strict_pass'] else (
       'AC_RELATIVE_S0_PASS_NOT_ABSOLUTE' if incumbent['relative_s0_pass'] else 'AC_VIOLATIONS_REMAIN'),
       'baseline_v21':original_score,'best_ac':incumbent,'accepted_iterations':accepted,'attempts':attempts,
       'criterion_in_milp':args.criterion,'unverified':[
       'MV transformer headroom, charger installation, protection and N-1',
       'Out-of-sample stochastic robustness, per-site mobility reassignment costs',
       'Full network security is not guaranteed by MILP slacks or sampled AC only']})
    pd.DataFrame(attempts).to_csv(output/'historico_v22.csv',index=False)
    print('\n[V2.2] Resultado AC:', 'STRICT PASS' if incumbent['strict_pass'] else 'VIOLACOES REMANESCENTES')
    print(json.dumps({k:incumbent[k] for k in ('strict_pass','relative_s0_pass','strict_overload_samples','strict_voltage_samples','thermal_aggravation_samples','voltage_aggravation_samples')},indent=2))
    print('Relatorio:',output/'final_report_v22.json')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v21-dir',required=True)
    p.add_argument('--output',default='results/v22_restauracao')
    p.add_argument('--validate-only',action='store_true')
    p.add_argument('--criterion',choices=['relative','strict'],default='relative')
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--max-iterations',type=int,default=1)
    p.add_argument('--backtracks',type=int,default=1)
    p.add_argument('--bess-trust-kw',type=float,default=250.)
    p.add_argument('--v2g-trust-kw',type=float,default=150.)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    p.add_argument('--min-improve',type=float,default=.0005)
    p.add_argument('--stop-relative',action='store_true')
    args=p.parse_args(argv)
    if args.delta_kw<=0 or args.max_iterations<1 or args.backtracks<0 or args.bess_trust_kw<=0 or args.v2g_trust_kw<=0:
        p.error('parametros numericos invalidos')
    run(args)

if __name__=='__main__':main()
