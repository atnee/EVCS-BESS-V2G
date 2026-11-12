"""V2.5 — testes do contrato, guardas térmicas e solver MILP sintético.

Não é um teste de convergência de OpenDSS: a rede AC roda na estação de trabalho.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
import pytest

from distributed_stochastic import cootimizar_controles_v25 as z


def sample(load=99., voltage=.96):
    b=pd.DataFrame([{'hour':h,'line':'L','phase':'A','loading_pct':load}
                    for h in range(24)]+[
        {'hour':h,'line':'M','phase':'A','loading_pct':75.} for h in range(24)])
    v=pd.DataFrame([{'hour':h,'bus':'B','phase':'A','v_pu':voltage} for h in range(24)])
    return {'branches':b,'voltages':v,'source':pd.DataFrame({'hour':range(24),'p_kw':[1000.]*24})}


def test_target_new_line_only():
    ref=sample(load=97.)
    case=sample(load=97.)
    case['branches'].loc[(case['branches'].line=='L') & (case['branches'].hour==18),'loading_pct']=100.177
    caps=z.detect_new_line_events(ref,case,.10)
    assert caps=={(18,'L','A'):99.9}
    audit=z.guard_report(case,caps)
    assert audit.iloc[0].above_100_pct==pytest.approx(.177)
    assert audit.iloc[0].exceeds_target_pp==pytest.approx(.277)


def test_ignore_preexisting_overload():
    ref=sample(load=107.)
    case=sample(load=108.)
    assert not z.detect_new_line_events(ref,case)


def test_phases_and_hours_not_merged():
    ref=sample(load=99.)
    case=sample(load=99.)
    case['branches'].loc[(case['branches'].line=='L') & (case['branches'].hour==17),'loading_pct']=100.1
    caps=z.detect_new_line_events(ref,case,.1)
    assert list(caps)==[(17,'L','A')]


def test_incomplete_ids_rejected():
    ref=sample(); case=sample()
    case['branches']=case['branches'].iloc[:-1]
    with pytest.raises(ValueError,match='diferentes'):
        z.detect_new_line_events(ref,case)


def test_invalid_margin():
    with pytest.raises(ValueError):
        z.detect_new_line_events(sample(),sample(),2.0)


def q(new=0, thermal=120.,voltage=1.0,peak=12000.,strict=False):
    return {'new_overloaded_lines_vs_s0':new,'thermal_aggravation_pp_h':thermal,
        'source_peak_kw':peak,'strict_pass':strict,'relative_s0_pass':False}


def test_accept_voltage_gain_and_guards():
    before=q(thermal=120)
    candidate=q(thermal=123)
    old={'voltage_rel_puh':2.15}
    new={'voltage_rel_puh':1.15}
    guard=pd.DataFrame([{'above_100_pct':0., 'exceeds_target_pp':0.}])
    assert z.compare_and_accept(before,candidate,candidate,old,new,guard)[0]
    assert not z.compare_and_accept(before,q(new=1),candidate,old,new,guard)[0]
    assert not z.compare_and_accept(before,candidate,q(new=1),old,new,guard)[0]
    assert not z.compare_and_accept(before,q(thermal=150),candidate,old,new,guard)[0]
    assert not z.compare_and_accept(before,candidate,candidate,old,old,guard)[0]
    over=pd.DataFrame([{'above_100_pct':0.025, 'exceeds_target_pp':0.125}])
    assert not z.compare_and_accept(before,candidate,candidate,old,new,over)[0]


def test_pareto_with_errors_and_dominance():
    rows=[{'thermal_original_pph':120,'voltage_original_puh':2,'peak_kw':12000},
          {'thermal_original_pph':100,'voltage_original_puh':1,'peak_kw':11900},
          {'status':'ERROR','reason':'milp infeasible'}]
    r=z.pareto(rows)
    assert [a['pareto_ac'] for a in r]==[False,True,False]


def test_no_overwrite_originals(tmp_path):
    with pytest.raises(ValueError):
        z.ensure_safe_output(tmp_path/'parent'/'results',[tmp_path/'parent'])
    z.ensure_safe_output(tmp_path/'other'/'results',[tmp_path/'parent'])


def test_milp_hard_line_guard(monkeypatch):
    from v2g import strategies
    monkeypatch.setattr(strategies,'fleet_profile',lambda *a,**kw: True)
    batt=NS(capacity_kwh=45.,soc_initial=.11,soc_min=.1,soc_max=.9,
            power_kw=11.,eta_charge=.95,eta_discharge=.95)
    car=NS(id='one',battery=batt,arrival=17,departure=8,
           reserve_kwh=12.,departure_target_kwh=12.)
    team={'vehicles':[car],'station':NS(charger_kw=11.),'grid':NS(steps=24),'ind':np.zeros(24)}
    cap={'siteB':{'power_kw':150.,'energy_kwh':600.}}
    base=sample(load=100.1,voltage=.951)
    slope={}
    for h in range(24):
        slope[h]={(s,sign):-.015 for s in ('siteB','siteV') for sign in (-1,1)}
    slopes={'lines':{(h,'L','A'):slope[h] for h in range(24)},
        'volts':{(h,'B','A'):{(s,sign):.00005 for s in ('siteB','siteV')
                              for sign in (-1,1)} for h in range(24)},
        'source':{h:{(s,sign):-1. for s in ('siteB','siteV') for sign in (-1,1)} for h in range(24)}}
    sol=z.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
        base,base,slopes,trust_bess=100.,trust_v2g=11.,time_limit=30,mip_gap=.10,
        voltage_weights=z.v23.PROFILES['balanced'],thermal_budget=200.,
        hard_line_caps={(18,'L','A'):99.9})
    assert sol['hard_line_caps']==1
    assert sol['cars'].shape==(1,24)
    # As restrições elétricas são locais; o teste só verifica que o MILP as aplica.
    assert -.015*sol['bess']['siteB'][18] <= -.2+0.005
    with pytest.raises(ValueError,match='ausentes'):
        z.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
          base,base,slopes,hard_line_caps={(18,'missing','A'):99.9})
