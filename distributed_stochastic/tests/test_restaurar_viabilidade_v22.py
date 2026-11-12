import importlib.util, sys
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
import pytest

HERE=Path(__file__).resolve().parents[1]/'restaurar_viabilidade_v22.py'
spec=importlib.util.spec_from_file_location('v22',HERE);v22=importlib.util.module_from_spec(spec);spec.loader.exec_module(v22)

def frame(kind,value):
    return pd.DataFrame([{'hour':h,'line':'line1','phase':'A','loading_pct':value} if kind=='branches'
                         else {'hour':h,'bus':'bus1','phase':'A','v_pu':value} for h in range(24)])

def ac(load,volt,peak):
    return {'branches':frame('branches',load),'voltages':frame('voltages',volt),
            'source':pd.DataFrame({'hour':list(range(24)),'p_kw':[peak]*24})}

def test_score_strict_vs_relative():
    base=ac(106.,.94,1000.)
    a=v22.score_ac(base,ac(103.,.946,900.))
    assert a['relative_s0_pass'] and not a['strict_pass']
    assert a['thermal_aggravation_samples']==0 and a['voltage_aggravation_samples']==0
    bad=v22.score_ac(base,ac(108.,.935,1050.))
    assert bad['thermal_aggravation_samples']==24
    assert bad['voltage_aggravation_samples']==24


def test_screen_near_both_bounds():
    a=ac(99.,1.047,500.)
    b=ac(100.,1.02,500.)
    assert len(v22.screen_keys(a,b,'lines'))==24
    assert len(v22.screen_keys(a,b,'volts'))==24


def test_delta_injection_splits_phases():
    a=pd.DataFrame([{'hour':18,'bus':'bar1','phase':'B','p_kw':-10.,'q_kvar':0.}])
    b=v22.inject_hour(a,18,'bar1',300.)
    assert b.p_kw.sum()==290.
    assert set(b.phase)==set('ABC')
    assert b.loc[b.phase=='B','p_kw'].iloc[0]==90.


def test_decision_guard():
    a=v22.score_ac(ac(105.,.94,100.),ac(106.,.935,99.))
    b=v22.score_ac(ac(105.,.94,100.),ac(104.,.945,110.))
    assert v22.choose_result(a,b)[0]
    assert not v22.choose_result(b,a)[0]


def test_small_milp_vehicles_can_decline_discharge(monkeypatch):
    # Fake dependencies are contract stubs, not a real OpenDSS solve.
    repo=Path(__file__).resolve().parents[2]
    if not (repo/'opendss_rebuild'/'rebuild'/'joint_optimization.py').exists():
        pytest.skip('Requer o projeto original, incluindo opendss_rebuild V0.8 ou superior')
    sys.path.insert(0,str(repo/'opendss_rebuild'))
    sys.path.insert(0,str(repo/'src'))
    # Isolate the MILP in this numerical test. The complete fleet validator is
    # invoked by the V2.2 CLI, using the real repository modules.
    import v2g.strategies as strategies
    monkeypatch.setattr(strategies,'fleet_profile',lambda *a,**kw: True)
    batt=NS(capacity_kwh=45.,soc_initial=.11,soc_min=.1,soc_max=.9,power_kw=11.,eta_charge=.95,eta_discharge=.95)
    veh=NS(id='low-arrival',battery=batt,arrival=17,departure=8,
           reserve_kwh=12.,departure_target_kwh=12.)
    team={'vehicles':[veh],'station':NS(charger_kw=11.),'grid':NS(steps=24),'ind':np.zeros(24)}
    cap={'siteB':{'power_kw':150.,'energy_kwh':600.}}
    base=ac(102.,.94,4000.)
    st={'lines':{},'volts':{},'source':{}}
    for h in range(24):
        keyL=(h,'line1','A');keyV=(h,'bus1','A')
        st['lines'][keyL]={(s,sign):-.015 for s in ['siteB','siteV'] for sign in (-1,1)}
        st['volts'][keyV]={(s,sign):.00005 for s in ['siteB','siteV'] for sign in (-1,1)}
        st['source'][h]={(s,sign):-1. for s in ['siteB','siteV'] for sign in (-1,1)}
    # Original low SOC vehicle must not be forced to discharge to its trip reserve.
    result=v22.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
          base,base,st,time_limit=20,mip_gap=.1,trust_bess=100.,trust_v2g=11.)
    assert result['cars'].shape==(1,24)
    assert np.max(result['cars'])<1e-6
    assert not np.any(result['bess']['siteB'][17:20]<-1e-6)
