"""Independent native S0 checks; no changes to the historical electrical model."""
from pathlib import Path
import json
import math
import os
import sys
import traceback
import pandas as pd
from audit import A, B, OUT, INSTALLED, digest, dump


def physical_voltage_rows(names, values, allowed):
    if len(names) != len(values):
        raise ValueError('Node/voltage length mismatch')
    rows=[]
    for name,value in zip(names,values):
        bus,_,node=name.lower().rpartition('.')
        phase={'1':'A','2':'B','3':'C'}.get(node)
        if (bus,phase) in allowed:
            if not math.isfinite(value):
                raise ValueError('Nonfinite physical voltage')
            rows.append(dict(bus=bus,phase=phase,v_pu=value))
    if len({(r['bus'],r['phase']) for r in rows})!=len(rows):
        raise ValueError('Duplicate physical node')
    return rows


def main():
    from opendssdirect import dss
    sys.path.insert(0,str(B))
    from validar_fases_s0_ae import inspect_phase_topology
    sys.path.insert(0,str(B/'opendss_rebuild'))
    from rebuild.contracts import load_context
    from rebuild.engine import ODSSEngine
    ctx=load_context(B,B/'opendss_rebuild')
    out=OUT/'native_s0'
    out.mkdir(exist_ok=False)
    manifest=dict(engine=dss.Basic.Version(),conditions='Independent snapshots; native source, full secondary network, automatic native controls; study ampacities for MT reporting',errors=[],runs=[])
    cwd=Path.cwd()
    for label,master in [('project',B/'data/ieee8500/source/Master.dss'),('installed',INSTALLED/'Master.dss')]:
        for hour in [3,18]:
            tag=f'{label}_h{hour:02d}'
            try:
                local={**ctx,'files':{**ctx['files'],'master':master}}
                engine=ODSSEngine(local,mode='original',ampacity='study')
                engine.compile()
                dss.Text.Command(f'Set DataPath="{out.as_posix()}"')
                result=engine.solve_hour(hour,float(ctx['multipliers'][hour]),pd.DataFrame(columns=['hour','bus','phase','p_kw','q_kvar']))
                vr=physical_voltage_rows(dss.Circuit.AllNodeNames(),dss.Circuit.AllBusMagPu(),ctx['physical_voltage_nodes'])
                found={(r['bus'],r['phase']) for r in vr}
                missing=sorted(ctx['physical_voltage_nodes']-found)
                phase,topology=inspect_phase_topology(dss,ctx['network'])
                load_p=load_q=cap_p=cap_q=0.
                for name in dss.Loads.AllNames():
                    dss.Circuit.SetActiveElement('Load.'+name)
                    if dss.CktElement.Enabled():
                        pq=dss.CktElement.Powers();load_p+=sum(pq[::2]);load_q+=sum(pq[1::2])
                for name in dss.Capacitors.AllNames():
                    dss.Circuit.SetActiveElement('Capacitor.'+name)
                    if dss.CktElement.Enabled():
                        pq=dss.CktElement.Powers();cap_p+=sum(pq[::2]);cap_q+=sum(pq[1::2])
                source=result['source'][0]
                lp,lq=[x/1000 for x in dss.Circuit.Losses()]
                rp=source['p_kw']-load_p-cap_p-lp
                rq=source['q_kvar']-load_q-cap_q-lq
                row=dict(case=tag,hour=hour,master=str(master),master_sha256=digest(master),loadmult=float(ctx['multipliers'][hour]),converged=bool(dss.Solution.Converged()),topology=topology,physical_nodes=len(vr),missing_nodes=missing,vmin_pu=min(r['v_pu'] for r in vr),vmax_pu=max(r['v_pu'] for r in vr),source_kw=source['p_kw'],source_kvar=source['q_kvar'],loss_kw=lp,loss_kvar=lq,load_kw=load_p,load_kvar=load_q,cap_kvar=cap_q,balance_residual_kw=rp,balance_residual_kvar=rq,balance_pass=abs(rp)<=1 and abs(rq)<=1)
                manifest['runs'].append(row)
                for key,table in {**result,'voltages':vr,'topology':phase}.items():
                    pd.DataFrame(table).to_csv(out/f'{tag}_{key}.csv',index=False)
                print(tag,'Vmin',row['vmin_pu'],'balance kW/kvar',rp,rq,'missing',missing,flush=True)
            except Exception:
                manifest['errors'].append(dict(case=tag,traceback=traceback.format_exc()))
                print(traceback.format_exc(),flush=True)
            finally:
                os.chdir(cwd)
                (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    comparisons=[]
    for hour in [3,18]:
        x=pd.read_csv(out/f'project_h{hour:02d}_voltages.csv')
        y=pd.read_csv(out/f'installed_h{hour:02d}_voltages.csv')
        paired=x.merge(y,on=['bus','phase'],validate='one_to_one',suffixes=('_project','_installed'))
        d=paired.v_pu_project-paired.v_pu_installed
        comparisons.append(dict(hour=hour,pairs=len(paired),mae_pu=float(d.abs().mean()),rmse_pu=float(d.pow(2).mean()**.5),max_error_pu=float(d.abs().max()),meaning='same DSS engine, two local source datasets; not independent solver validation'))
    pd.DataFrame(comparisons).to_csv(out/'project_vs_installed.csv',index=False)
    return 1 if manifest['errors'] else 0


if __name__=='__main__':
    raise SystemExit(main())
