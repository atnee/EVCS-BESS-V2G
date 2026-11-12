#!/usr/bin/env python3
"""MILP V2.9.2 de redespacho individual BESS/V2G com envoltorias de seguranca.

Derivado do modelo fisico V2.2 do projeto com salvaguardas electricas adicionais.
As sensibilidades sao locais e aproximadas: apenas AC de 24h valida a proposta.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
from distributed_stochastic.restaurar_viabilidade_v22 import get_indexed, KEY_L, KEY_V

def make_milp_v292(team,vehicle_places,cap,current_bess,current_cars,base_ac,s0,slopes,
              *,criterion='relative',trust_bess=300.,trust_v2g=200.,time_limit=360,mip_gap=.03,hard_line_caps=None,
              protected_voltage=None, focus_target=None, peak_growth_kw=60.,
              absolute_thermal_target=None, strict_voltage_budget_puh=None,
              max_line_growth_pp=.02, nonoverloaded_margin_pp=.005,
              hard_line_bias=None, hard_voltage_bias=None, voltage_ceilings=None):
    """Fixed site locations, 24-hour optimal recourse for all batteries and every individual vehicle."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'opendss_rebuild'))
    from rebuild.joint_optimization import MatrixBuilder
    from v2g.availability import session_steps
    from v2g.strategies import fleet_profile
    hard_line_caps=hard_line_caps or {}
    protected_voltage=protected_voltage or {}
    hard_line_bias=hard_line_bias or {}
    hard_voltage_bias=hard_voltage_bias or {}
    voltage_ceilings=voltage_ceilings or {}
    collision=set(protected_voltage)&set(voltage_ceilings)
    if collision:
        raise ValueError(f"Mesma chave em piso e teto de tensao: {sorted(collision)[:3]}")
    if not np.isfinite(max_line_growth_pp) or max_line_growth_pp < 0:
        raise ValueError('Crescimento local maximo de carregamento invalido')
    if not 0 < nonoverloaded_margin_pp <= .2:
        raise ValueError('Margem de protecao de novas amostras invalida')
    for key, v in [*hard_line_bias.items(), *hard_voltage_bias.items()]:
        if not np.isfinite(v) or v < 0:
            raise ValueError(f'Correcao AC nao finita ou negativa: {key}')
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
        slack=b.var(140.,0.,1000.);all_slacks['thermal'].append(slack)
        b.row(electric_terms(coeff,h)+[(slack,-1)],hi=lim-float(linebase[key]))
    for key,coeff in slopes['volts'].items():
        h=int(key[0]);vl=.95 if criterion=='strict' else min(.95,float(voltage0[key]));vh=1.05 if criterion=='strict' else max(1.05,float(voltage0[key]))
        sla=b.var(1200.,0.,.5);slb=b.var(1200.,0.,.5)
        all_slacks['under'].append(sla);all_slacks['over'].append(slb)
        b.row(electric_terms(coeff,h)+[(sla,1)],lo=vl-float(voltagebase[key]))
        b.row(electric_terms(coeff,h)+[(slb,-1)],hi=vh-float(voltagebase[key]))
    peak=b.var(.005,0.,50000.)
    src=get_indexed(base_ac['source'],['hour'],'p_kw')
    for h in range(T):
        b.row(electric_terms(slopes['source'][h],h)+[(peak,-1)],hi=-float(src[h]))
    # V2.9: restricoes eletricas simultaneas e localizadas na aproximacao MILP.
    # Exigir que a calibracao contenha todas as chaves; nunca dispensar guarda.
    for key, target in sorted(hard_line_caps.items()):
        if key not in slopes['lines'] or key not in linebase:
            raise ValueError(f'Linha critica sem sensibilidade local: {key}')
        h=int(key[0])
        b.row(electric_terms(slopes['lines'][key],h),hi=float(target)-float(linebase[key])-float(hard_line_bias.get(key,0.)))
    for key, floor in sorted(protected_voltage.items()):
        if key not in slopes['volts'] or key not in voltagebase:
            raise ValueError(f'Barra/fase critica sem sensibilidade: {key}')
        h=int(key[0])
        b.row(electric_terms(slopes['volts'][key],h),lo=float(floor)-float(voltagebase[key])+float(hard_voltage_bias.get(key,0.)))
    # V2.9.2: teto explicito de sobretensao, simetrico ao piso de subtensao.
    for key, ceiling in sorted(voltage_ceilings.items()):
        if key not in slopes['volts'] or key not in voltagebase:
            raise ValueError(f'Barra/fase sem sensibilidade de teto: {key}')
        if not np.isfinite(ceiling):
            raise ValueError(f'Teto de tensao nao finito: {key}')
        h=int(key[0])
        b.row(electric_terms(slopes['volts'][key],h),
              hi=float(ceiling)-float(voltagebase[key])-float(hard_voltage_bias.get(key,0.)))
    if focus_target is not None:
        focus_key, goal=focus_target
        if focus_key not in slopes['lines']:
            raise ValueError(f'Foco ausente da calibracao: {focus_key}')
        b.row(electric_terms(slopes['lines'][focus_key],int(focus_key[0])),
              hi=float(goal)-float(linebase[focus_key]))
    if not np.isfinite(peak_growth_kw) or peak_growth_kw<0:
        raise ValueError('peak_growth_kw invalido')
    # Fonte em cada hora: permite no maximo crescimento limitado vs. incumbente.
    for h in range(T):
        b.row(electric_terms(slopes['source'][h],h),hi=float(peak_growth_kw))
    # V2.9.2: limites numericos para severidade TERMICA ABSOLUTA.
    # Nunca confundir agravamento relativo ao S0 com sobrecarga absoluta.
    # O calibrador 2.4.1 deve conter TODAS as amostras que ja excedem 100%.
    line_tuples=set(slopes['lines'])
    missing_overloaded=[k for k, value in linebase.items()
                        if float(value)>100.+0.001 and k not in line_tuples]
    if missing_overloaded:
        raise RuntimeError(f'Calibracao omitiu {len(missing_overloaded)} amostras '
                           f'ja sobrecarregadas; nao podemos certificar envoltoria absoluta')
    absolute_slacks=[]
    for key, coeff in slopes['lines'].items():
        h=int(key[0]); value=float(linebase[key]); bias=float(hard_line_bias.get(key,0.))
        # Guardar qualquer linha previamente segura, sem permitir que vire uma
        # nova amostra de sobrecarga no surrogate. OpenDSS decide no fim.
        if value<=100.0:
            b.row(electric_terms(coeff,h),
                  hi=100.0-nonoverloaded_margin_pp-value-bias)
        else:
            # Proibe deslocamentos grandes de sobrecarga entre horas/fases.
            b.row(electric_terms(coeff,h),hi=max_line_growth_pp-bias)
        if absolute_thermal_target is not None:
            u=b.var(240.,0.,1000.)
            absolute_slacks.append(u)
            b.row(electric_terms(coeff,h)+[(u,-1)],hi=100.-value-bias)
    if absolute_thermal_target is not None:
        if not np.isfinite(absolute_thermal_target) or absolute_thermal_target<0:
            raise ValueError('Meta de severidade absoluta invalida')
        b.row([(v,1) for v in absolute_slacks],hi=float(absolute_thermal_target))

    # Envoltoria de tensao absoluta em pu.h como restricao EXPLICITA.
    # O score real considera todas as 91680 amostras, independentemente de
    # estarem ou nao presentes no subconjunto de sensibilidades.
    v_keys=set(slopes['volts'])
    absent_v=[k for k, value in voltagebase.items()
              if (float(value)<.95-1e-9 or float(value)>1.05+1e-9) and k not in v_keys]
    if strict_voltage_budget_puh is not None and absent_v:
        raise RuntimeError(f'Calibracao omitiu {len(absent_v)} tensoes fora da faixa')
    strict_voltage_slacks=[]
    if strict_voltage_budget_puh is not None:
        if not np.isfinite(strict_voltage_budget_puh) or strict_voltage_budget_puh<0:
            raise ValueError('Orcamento absoluto de tensao invalido')
        for key, coeff in slopes['volts'].items():
            h=int(key[0]); value=float(voltagebase[key])
            # nao se permite aliviar a protecao AC de barras criticas
            drop=float(hard_voltage_bias.get(key,0.))
            lo=b.var(600.,0.,.5); hi=b.var(600.,0.,.5)
            strict_voltage_slacks.extend([lo,hi])
            b.row(electric_terms(coeff,h)+[(lo,1)],lo=.95-value+drop)
            b.row(electric_terms(coeff,h)+[(hi,-1)],hi=1.05-value-drop)
        b.row([(v,1) for v in strict_voltage_slacks],
              hi=float(strict_voltage_budget_puh))

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
    raw_gap=getattr(res,'mip_gap',None)
    safe_gap=(float(raw_gap) if raw_gap is not None and np.isfinite(raw_gap) else None)
    return {'bess':out_bess,'cars':out_cars,'solver_status':int(res.status),
            'solver_message':str(res.message),'mip_gap':safe_gap,
            'objective':float(res.fun),'model_stats':info,
            'slack_sum':{k:float(sum(x[j] for j in vec)) for k,vec in all_slacks.items()},
            'trust_bess_kw':trust_bess,'trust_v2g_kw':trust_v2g,
            'hard_line_constraints':len(hard_line_caps),
            'hard_voltage_constraints':len(protected_voltage),
            'hard_voltage_ceiling_constraints':len(voltage_ceilings),
            'has_focus_target':focus_target is not None,
            'absolute_thermal_envelope_rows':len(absolute_slacks),
            'absolute_voltage_envelope_rows':len(strict_voltage_slacks),
            'AC_line_bias_count':len(hard_line_bias),
            'AC_voltage_bias_count':len(hard_voltage_bias)}

