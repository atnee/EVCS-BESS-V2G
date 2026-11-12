"""Testes locais V2.3. A convergência elétrica integral exige OpenDSS no PC do usuário."""
from __future__ import annotations
from types import SimpleNamespace as NS
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from distributed_stochastic import restaurar_tensao_v23 as z


def sample(value=.94, high=1.06, loading=105, source=1000):
    v=pd.DataFrame([{'hour':h,'bus':bus,'phase':'A','v_pu':val}
                     for h in range(24) for bus,val in [('low',value),('high',high)]])
    l=pd.DataFrame([{'hour':h,'line':'L','phase':'A','loading_pct':loading} for h in range(24)])
    p=pd.DataFrame({'hour':range(24),'p_kw':[source]*24})
    r=pd.DataFrame([{'hour':h,'regulator':'T','tap':1} for h in range(24)])
    c=pd.DataFrame([{'hour':h,'capacitor':'C','states':['[1]']} for h in range(24)])
    return {'voltages':v,'branches':l,'source':p,'regulators':r,'capacitors':c}


def test_components_relative_baseline():
    base=sample(.94,1.06)
    improved=sample(.945,1.055)
    x,_=z.audit_components(base,improved)
    assert x['under_rel_puh']==0 and x['over_rel_puh']==0
    assert x['under_abs_puh']>0 and x['over_abs_puh']>0
    assert x['under_abs_samples']==24 and x['over_abs_samples']==24


def test_components_arbitrary_worsening():
    base=sample(.94,1.06)
    worsen=sample(.935,1.07)
    x,_=z.audit_components(base,worsen)
    assert abs(x['under_rel_puh']-.005*24)<1e-9
    assert abs(x['over_rel_puh']-.01*24)<1e-9


def test_abs_and_relative_feasibility_separate():
    base=sample(.94,1.06)
    newer=sample(.945,1.055,loading=104)
    s=z.v22.score_ac(base,newer)
    assert s['relative_s0_pass'] and not s['strict_pass']


def test_acceptance_thermoguard_and_new_lines():
    original={'strict_pass':False,'relative_s0_pass':False,'new_overloaded_lines_vs_s0':0,'thermal_aggravation_pp_h':100}
    cand={**original,'thermal_aggravation_pp_h':105}
    pc={'under_rel_puh':.8,'over_rel_puh':.2,'voltage_rel_puh':1.0}
    cc={'under_rel_puh':.65,'over_rel_puh':.2,'voltage_rel_puh':.85}
    assert z.acceptance(original,cand,pc,cc)[0]
    assert not z.acceptance(original,{**cand,'new_overloaded_lines_vs_s0':1},pc,cc)[0]
    assert not z.acceptance(original,{**cand,'thermal_aggravation_pp_h':112},pc,cc)[0]
    assert not z.acceptance(original,cand,pc,{**cc,'over_rel_puh':.3})[0]


def test_acceptance_absolute_priority():
    o={'strict_pass':False,'relative_s0_pass':False,'new_overloaded_lines_vs_s0':1,'thermal_aggravation_pp_h':100}
    n={**o,'strict_pass':True,'relative_s0_pass':True,'new_overloaded_lines_vs_s0':0}
    assert z.acceptance(o,n,{},{} )[0]


def test_diagnostic_files_and_taps(tmp_path):
    base=sample(.94,1.06)
    alt=sample(.93,1.065)
    alt['regulators'].loc[alt['regulators'].hour==18,'tap']=3
    vals=z.rank_voltage(base,alt,tmp_path)
    controls=z.tap_differences(base,alt,tmp_path)
    assert vals['voltage_rel_puh']>0
    assert controls['tap_changes']==1 and controls['max_absolute_tap_change']==2
    for f in ('tensoes_criticas_v23.csv','tensoes_por_hora_v23.csv','taps_comparados_v23.csv','componentes_tensao_v23.json'):
        assert (tmp_path/f).is_file()


def test_pareto(tmp_path):
    first={'name':'baseline','state':'BASELINE_AC','thermal_aggravation_pp_h':10,
       'voltage_rel_puh':1.0,'source_peak_kw':100}
    trials=[{'name':'X','state':'AC_REJECTED','thermal_aggravation_pp_h':9,
       'voltage_rel_puh':1.2,'source_peak_kw':101},
       {'name':'Y','state':'AC_ACCEPTED','thermal_aggravation_pp_h':8,
       'voltage_rel_puh':.8,'source_peak_kw':90}]
    assert z.pareto_table(tmp_path,trials,first)==1


def test_milp_one_vehicle(monkeypatch):
    # Apenas teste de consistencia do MILP. Sem OpenDSS nesse teste.
    from v2g import strategies
    monkeypatch.setattr(strategies,'fleet_profile',lambda *a,**kw: True)
    batt=NS(capacity_kwh=45.,soc_initial=.11,soc_min=.1,soc_max=.9,
            power_kw=11.,eta_charge=.95,eta_discharge=.95)
    veh=NS(id='test',battery=batt,arrival=17,departure=8,
           reserve_kwh=12.,departure_target_kwh=12.)
    team={'vehicles':[veh],'station':NS(charger_kw=11.),'grid':NS(steps=24),'ind':np.zeros(24)}
    cap={'siteB':{'power_kw':150.,'energy_kwh':600.}}
    base=sample(.94,1.06,loading=100,source=3000)
    slopes={'lines':{},'volts':{},'source':{}}
    for h in range(24):
        slopes['lines'][(h,'L','A')]={(s,sign):-.015 for s in ('siteB','siteV') for sign in (-1,1)}
        for bus in ('low','high'):
            slopes['volts'][(h,bus,'A')]={(s,sign):.00005 for s in ('siteB','siteV') for sign in (-1,1)}
        slopes['source'][h]={(s,sign):-1. for s in ('siteB','siteV') for sign in (-1,1)}
    sol=z.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
        base,base,slopes,trust_bess=100.,trust_v2g=11.,time_limit=30,mip_gap=.10,
        voltage_weights=z.PROFILES['balanced'],thermal_budget=200.)
    assert sol['cars'].shape==(1,24)
    assert max(sol['cars'].flatten())<1e-5
    assert min(sol['bess']['siteB'][17:20])>=-1e-5
    assert sol['worst_under_pred_pu']>=0 and sol['worst_over_pred_pu']>=0
