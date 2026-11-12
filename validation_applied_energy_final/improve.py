"""Bounded experiments using the existing joint optimizer and native AC backend."""
import copy
import json
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
from audit import A,B,OUT,digest

sys.path[:0]=[str(B/'src'),str(B/'opendss_rebuild')]
from rebuild.contracts import load_context,canonical_profile
from rebuild.joint_optimization import solve_joint,OptSettings
from reotimizar_bess_v2g import read_existing,read_team,calibrate,build_scenario_injections,simulate_solution,score
from core.assets import Station
from evcs.runner import run_integrated as evcs_run
from v2g.runner import run_integrated as v2g_run,vehicles_from_config
from bess.model import simulate


def save(path,obj):
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=lambda x:x.tolist() if isinstance(x,np.ndarray) else str(x)),encoding='utf-8')


def read_ac(folder):
    return {k:pd.read_csv(folder/f'{k}.csv') for k in ['source','voltages','branches','regulators','capacitors']}


def metric_row(name,ac,s0,s1):
    summary,verified,_,_=score(s0,s1,ac)
    m=summary['S4_reoptimized']
    return dict(case=name,peak_kw=m['peak_import_kw'],vmin=m['v_min_pu'],vmax=m['v_max_pu'],max_loading_pct=m['loading_max_pct'],new_lines=m['new_distinct_overloaded_lines_vs_S0'],thermal_pph=m['thermal_severity_pp_hour'],under_puh=m['undervoltage_severity_pu_hour'],over_puh=m['overvoltage_severity_pu_hour'],loss_kwh=m['loss_kwh'],no_aggravation_s0=verified['S4_completely_satisfies_no_aggravation'])


def acceptable(row,reference,cfg):
    # Global guards prevent hiding a voltage deterioration behind a thermal gain.
    return bool(row['max_loading_pct']<=reference['max_loading_pct']+cfg['line_guard_tolerance_pp'] and row['vmin']>=reference['vmin']-cfg['voltage_guard_tolerance_pu'] and row['vmax']<=reference['vmax']+cfg['voltage_guard_tolerance_pu'] and row['thermal_pph']<=reference['thermal_pph']+1e-6 and row['under_puh']<=reference['under_puh']+1e-6 and row['over_puh']<=reference['over_puh']+1e-6)


def prepare_case(name,n,bus_b,bus_v,window,team0,root,cfg,ctx):
    team=copy.deepcopy(team0)
    team['bess_bus']=bus_b;team['v2g_bus']=bus_v
    team['discharge_window']=tuple(window)
    team['no_charge_window']=tuple(cfg['no_charge_window']) if window!=[17,20] else (17,22)
    ec=copy.deepcopy(team['configs']['evcs']);vc=copy.deepcopy(team['configs']['v2g'])
    ec['station']['bus']=bus_v
    ec['integration']['evs']=cfg['total_vehicles']-n
    if n!=150:
        ec['station'].update(chargers=n+cfg['ports_reserve'],connection_kw=(n+cfg['ports_reserve'])*11.,capex=(n+cfg['ports_reserve'])*cfg['port_cost_usd'])
    vc['fleet']['n_vehicles']=n
    vc['standalone']['station']=copy.deepcopy(ec['station'])
    # Generate the uncontrolled S1 with its original tariff/window contract.
    # The optimization-only window is recorded in design.json and team above.
    # Configuration is a self-contained experimental copy, never an official YAML.
    conf=root/'configs';conf.mkdir()
    for file,data in [('evcs',ec),('v2g',vc)]:
        (conf/(file+'.yaml')).write_text(yaml.safe_dump(data,sort_keys=False),encoding='utf-8')
    ev,tables=evcs_run(conf,team['grid'])
    vp,vtables=v2g_run(conf,team['grid'],ev,False)
    for key,table in {**tables,**vtables}.items():table.to_csv(root/(key+'_S1.csv'),index=False)
    team['vehicles']=vehicles_from_config(vc)
    team['station']=Station(**ec['station'])
    team['old_fleet']=np.asarray(vp.metadata['vehicle_power_kw'])
    team['old_fleet_net']=team['old_fleet'].sum(axis=0)
    team['old_fleet_profile']=canonical_profile(vp.data,list(range(24)),ctx['buses'])
    team['ind']=np.asarray(ev.metadata['station_demand_kw'])
    # All original 75 public sites stay; the public session model is recomputed for 3000-N.
    raw=pd.concat([ev.data,vp.data]).groupby(['time','bus','phase'],as_index=False)[['p_kw','q_kvar']].sum()
    profile=canonical_profile(raw,list(range(24)),ctx['buses'])
    save(root/'design.json',dict(name=name,n_v2g=n,n_public=cfg['total_vehicles']-n,bess_bus=bus_b,v2g_bus=bus_v,bess_power_kw=team['battery'].power_kw,bess_energy_kwh=team['battery'].capacity_kwh,discharge_window=window,no_charge_window=team['no_charge_window'],station=ec['station'],evcs_kpis=ev.metadata['kpis'],note='Same seed, regenerated fleet and public sessions for quantity cases; not a paired nested-vehicle sample. Site relocation includes the independent host demand. Civil/connection costs excluded.'))
    return team,profile


def main():
    cfg=yaml.safe_load((OUT/'improvement_settings.yaml').read_text(encoding='utf-8'))
    out=OUT/'improvement_experiment';out.mkdir(exist_ok=True)
    ctx=load_context(B,B/'opendss_rebuild')
    old=A/'results/replay_s0_s4_opendss'
    data,original_s1=read_existing(old)
    team0=read_team(B,old)
    s0=read_ac(OUT/'exact_profile_pilot/S0_auto')
    s1_original=read_ac(OUT/'exact_profile_pilot/S1_auto')
    historical=read_ac(OUT/'exact_profile_pilot/S4_auto')
    ref=metric_row('historical',historical,s0,s1_original)
    rows=[ref];runs=[];errors=[];cwd=Path.cwd()
    cases=[dict(name='dispatch_original_window',n=150,b=team0['bess_bus'],v=team0['v2g_bus'],window=[17,20]),dict(name='dispatch_extended_window',n=150,b=team0['bess_bus'],v=team0['v2g_bus'],window=cfg['extended_discharge_window'])]
    cases.extend(dict(name=s['name'],n=150,b=s['bess_bus'],v=s['v2g_bus'],window=cfg['extended_discharge_window']) for s in cfg['sites'])
    cache={}
    for index in range(6):
        if index==4:
            successful=[x for x in runs if x['row']['accepted_without_voltage_tradeoff']]
            # Do not buy more participation when an absolute feasible solution already exists.
            if any(x['row']['absolute_feasible'] for x in successful):break
            best=min(successful,key=lambda x:(x['row']['thermal_pph'],x['row']['under_puh'])) if successful else None
            selected=best['case'] if best else cases[1]
            cases.extend(dict(name=f'participation_{n}',n=n,b=selected['b'],v=selected['v'],window=cfg['extended_discharge_window']) for n in cfg['quantity_cases'])
        case=cases[index];folder=out/case['name']
        if (folder/'result.json').is_file():
            row=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            rows.append(row);runs.append(dict(case=case,row=row))
            if (folder/'sensibilidades/linhas.csv').exists():
                sf=folder/'sensibilidades'
                baseline=s1_original if case['n']==150 and case['v']==team0['v2g_bus'] else read_ac(folder/'uncontrolled_baseline')
                cache[(case['n'],case['b'],case['v'])]=(baseline,pd.read_csv(sf/'linhas.csv'),pd.read_csv(sf/'tensoes.csv'),pd.read_csv(sf/'fonte.csv'))
            print('REUSE COMPLETED',case['name'],flush=True)
            continue
        if folder.exists():
            if not (folder/'error.json').exists():raise FileExistsError('Incomplete case requires inspection: '+str(folder))
            folder=folder/'retry_01'
        folder.mkdir()
        print('START',case,flush=True)
        try:
            team,profile=prepare_case(case['name'],case['n'],case['b'],case['v'],case['window'],team0,folder,cfg,ctx)
            key=(case['n'],case['b'],case['v'])
            if key not in cache:
                if case['n']==150 and case['v']==team0['v2g_bus']:
                    p=profile.merge(original_s1,on=['hour','bus','phase'],validate='one_to_one',suffixes=('_new','_old'))
                    if len(p)!=len(profile) or not np.allclose(p.p_kw_new,p.p_kw_old,atol=1e-6,rtol=0):raise ValueError('Original S1 was not reproduced')
                    baseline=s1_original
                else:
                    baseline=simulate_solution(ctx,profile,team,None,folder/'uncontrolled_baseline')
                    os.chdir(cwd)
                dl,dv,ds,_=calibrate(ctx,profile,baseline,team,folder,cfg['delta_kw'])
                os.chdir(cwd)
                cache[key]=(baseline,dl,dv,ds)
            baseline,dl,dv,ds=cache[key]
            settings=OptSettings(thermal_screen_pct=cfg['thermal_screen_pct'],voltage_screen_margin_pu=cfg['voltage_screen_margin_pu'],thermal_weight=cfg['thermal_weight'],undervoltage_weight=cfg['voltage_weight'],overvoltage_weight=cfg['voltage_weight'],peak_weight=cfg['peak_weight'],relative_to_s0=False,solver_time_limit_s=cfg['solver_time_limit_s'],mip_rel_gap=cfg['mip_rel_gap'])
            solution=solve_joint(vehicles=team['vehicles'],grid=team['grid'],station=team['station'],battery=team['battery'],bess_bus=team['bess_bus'],v2g_bus=team['v2g_bus'],source_s1=baseline['source'],lines_s1=baseline['branches'],volt_s1=baseline['voltages'],lines_s0=s0['branches'],volt_s0=s0['voltages'],ds_lines=dl,ds_volt=dv,ds_source=ds,fleet_s1_kw=team['old_fleet_net'],fleet_s1_per_vehicle_kw=team['old_fleet'],independent_evcs_kw=team['ind'],discharge_window=team['discharge_window'],no_charge_window=team['no_charge_window'],tariffs=team['tariff'],remuneration=team['remuneration'],settings=settings,mode='S4')
            dispatch=simulate(team['battery'],solution['bess_net_kw'],1.,final_soc=team['battery'].soc_initial)
            if np.max(np.abs(dispatch.rejected_kw))>1e-6:raise ValueError('BESS commands violate resource limits')
            save(folder/'solution.json',solution)
            pd.DataFrame(dict(hour=range(24),bess_kw=solution['bess_net_kw'],v2g_kw=solution['v2g_net_kw'])).to_csv(folder/'dispatch.csv',index=False)
            full=build_scenario_injections(profile,team['old_fleet_profile'],team,solution)
            ac=simulate_solution(ctx,full,team,baseline,folder/'AC')
            os.chdir(cwd)
            row=metric_row(case['name'],ac,s0,baseline)
            row['accepted_without_voltage_tradeoff']=acceptable(row,ref,cfg)
            row['absolute_feasible']=bool(row['max_loading_pct']<=100 and row['vmin']>=.95 and row['vmax']<=1.05)
            row['vehicles']=case['n'];row['bess_bus']=case['b'];row['v2g_bus']=case['v']
            row['v2g_delivered_kwh']=float(np.maximum(solution['vehicle_power_kw'],0).sum())
            rows.append(row);runs.append(dict(case=case,row=row))
            save(folder/'result.json',row)
            print('RESULT',row,flush=True)
        except Exception:
            err=dict(case=case,traceback=traceback.format_exc());errors.append(err)
            save(folder/'error.json',err);print(err['traceback'],flush=True)
        finally:
            os.chdir(cwd)
            pd.DataFrame(rows).to_csv(out/'comparison.csv',index=False)
            save(out/'manifest.json',dict(status='RUNNING' if index<5 else 'COMPLETE',settings_sha256=digest(OUT/'improvement_settings.yaml'),completed=len(runs),errors=errors,scenarios=cases,reference=ref))
    save(out/'manifest.json',dict(status='COMPLETE_WITH_ERRORS' if errors else 'COMPLETE',completed=len(runs),errors=errors,settings=cfg,scenarios=cases,reference=ref,limitations=['Local surrogate; AC verification is authoritative','No global optimum certificate','No additional hardware engineering or independent solver validation','Regenerated fleet samples and public sessions in quantity experiments']))


if __name__=='__main__':
    main()
