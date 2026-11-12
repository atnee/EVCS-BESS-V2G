"""Testes locais V2.6: contratos, cortes intertemporais, MILP sintetico.
Nao substitui o replay OpenDSS do usuario.
"""
from types import SimpleNamespace as NS
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from distributed_stochastic import redistribuir_carga_v26 as z


def sample(line_load=99., phase='C', voltage=.96):
    b = pd.DataFrame([{'hour':h,'line':z.PRIMARY_LINE,'phase':phase,'loading_pct':line_load}
                       for h in range(24)] + [
         {'hour':h,'line':'ln5860423-3','phase':'A','loading_pct':80.} for h in range(24)])
    v = pd.DataFrame([{'hour':h,'bus':'B','phase':'A','v_pu':voltage} for h in range(24)])
    s = pd.DataFrame({'hour':range(24),'p_kw':[1000.]*24})
    return {'branches':b, 'voltages':v, 'source':s}


def update(ac, h, line, phase, value):
    ac['branches'].loc[(ac['branches'].hour==h)&(ac['branches'].line==line)&
                       (ac['branches'].phase==phase),'loading_pct']=value


def make_pair():
    ref=sample(95.)
    case=sample(97.)
    for h,pct in [(8,109.48),(15,110.86),(23,107.62),(14,109.),(21,108.2),
                  (10,105.2),(18,97.)]:
        update(case,h,z.PRIMARY_LINE,'C',pct)
    update(case,17,'ln5860423-3','A',102.)
    return ref,case


def test_temporal_cuts_anchor_hours():
    s0,ac=make_pair()
    targets=z.temporal_targets(s0,ac,top_k=10)
    hard,soft=z.directed_cuts(targets,strategy='anchors3',keep_guards={(18,'ln5860423-3','A'):99.9})
    assert {(8,z.PRIMARY_LINE,'C'),(15,z.PRIMARY_LINE,'C'),(23,z.PRIMARY_LINE,'C')}.issubset(hard)
    assert hard[(18,'ln5860423-3','A')]==99.9
    assert (14,z.PRIMARY_LINE,'C') in soft
    assert soft[(14,z.PRIMARY_LINE,'C')][0]==99.9
    assert hard[(8,z.PRIMARY_LINE,'C')]==pytest.approx(99.9)
    assert len(targets)>=7


def test_soft_strategy_retains_previously_fixed_guards():
    ref,ac=make_pair();targets=z.temporal_targets(ref,ac)
    hard,soft=z.directed_cuts(targets,strategy='soft',keep_guards={(18,'ln5860423-3','A'):99.9})
    assert hard=={(18,'ln5860423-3','A'):99.9}
    assert (15,z.PRIMARY_LINE,'C') in soft
    assert (18,'ln5860423-3','A') not in soft


def test_s0_existing_overload_uses_nonaggravation_threshold():
    ref,ac=make_pair()
    update(ref,16,z.PRIMARY_LINE,'C',103.)
    update(ac,16,z.PRIMARY_LINE,'C',110.)
    t=z.temporal_targets(ref,ac)
    row=t[(t.line==z.PRIMARY_LINE)&(t.hour==16)].iloc[0]
    assert row.target_pct==103.
    _,soft=z.directed_cuts(t,strategy='soft')
    assert soft[(16,z.PRIMARY_LINE,'C')][0]==103.


def test_reject_different_topology():
    ref,ac=make_pair()
    ac['branches']=ac['branches'].iloc[:-1]
    with pytest.raises(RuntimeError,match='topologia'):
        z.temporal_targets(ref,ac)


def test_guard_invalid_parameters_and_strategy():
    ref,ac=make_pair()
    for kw in [{'top_k':-1},{'margin_pct':2.}]:
        with pytest.raises(ValueError): z.temporal_targets(ref,ac,**kw)
    with pytest.raises(ValueError):z.directed_cuts(pd.DataFrame(),strategy='undefined')


def q(thermal=126.,voltage=1.1,lines=0,max_loading=110.,peak=12409.,strict=False):
    return {'new_overloaded_lines_vs_s0':lines,'thermal_aggravation_pp_h':thermal,
            'voltage_aggravation_pu_h':voltage,'max_loading_pct':max_loading,
            'source_peak_kw':peak,'strict_pass':strict}


def test_accept_requires_thermal_gain_and_voltage_guard():
    initial={'critical_line_aggravation_pph':73.}
    final={'critical_line_aggravation_pph':60.}
    accepted,why=z.accept_candidate(q(thermal=124.,voltage=1.0),q(),
        q(thermal=110.,voltage=1.05),q(thermal=110.,voltage=1.15),initial,final)
    assert accepted
    no,why=z.accept_candidate(q(thermal=124.,voltage=1.0),q(),
        q(thermal=125.,voltage=1.05),q(thermal=110.,voltage=1.15),initial,final)
    assert not no and why=='THERMAL_REGRESSION_VS_ORIGINAL_S0'
    no,why=z.accept_candidate(q(thermal=124.,voltage=1.0),q(),
        q(thermal=110.,voltage=1.05),q(thermal=110.,voltage=1.5),initial,final)
    assert not no and why=='VOLTAGE_REGRESSION_VS_POLICY_S0'
    no,why=z.accept_candidate(q(thermal=124.,voltage=1.0),q(),
        q(thermal=110.,voltage=1.05),q(thermal=110.,voltage=1.15,lines=1),initial,final)
    assert not no and why=='NEW_LINES_VS_SAME_POLICY_S0'


def test_no_improvement_on_focus_line_rejected():
    base=q()
    no,reason=z.accept_candidate(base,base,q(thermal=100.),q(thermal=100.),
        {'critical_line_aggravation_pph':70.},{'critical_line_aggravation_pph':70.})
    assert not no and reason=='FOCUS_LINE_NOT_IMPROVED'


def test_v25_real_targets_when_available():
    # Teste opcional com dados do arquivo RAR V2.5 descompactado nesta sessao.
    root=Path('/mnt/data/_v25_received/v25_politica_C')
    if not (root/'best/AC/branches.csv').is_file(): pytest.skip('V25 real not mounted')
    ref=z.v22.read_ac_folder(root/'policies/fase_C_reduzida/S0_policy_AC')
    ac=z.v22.read_ac_folder(root/'best/AC')
    targets=z.temporal_targets(ref,ac)
    hard,soft=z.directed_cuts(targets,strategy='anchors3')
    assert all((h,z.PRIMARY_LINE,'C') in hard for h in z.ANCHOR_HOURS)
    assert 12 <= len(targets) <= 40
    assess,j=z.temporal_assessment(ref,ac)
    assert assess['critical_line_aggravation_pph']==pytest.approx(73.354,abs=.02)
    assert assess['thermal_total_aggravation_pph']==pytest.approx(126.444764,abs=1e-3)


def test_temporal_milp_synthetic(monkeypatch):
    from v2g import strategies
    monkeypatch.setattr(strategies,'fleet_profile',lambda *a,**kw: True)
    battery=NS(capacity_kwh=45.,soc_initial=.11,soc_min=.1,soc_max=.9,
               power_kw=11.,eta_charge=.95,eta_discharge=.95)
    car=NS(id='one',battery=battery,arrival=17,departure=8,reserve_kwh=12.,departure_target_kwh=12.)
    team={'vehicles':[car],'station':NS(charger_kw=11.),'grid':NS(steps=24),'ind':np.zeros(24)}
    cap={'siteB':{'power_kw':150.,'energy_kwh':600.}}
    ref=sample(100.1)
    slopes={'lines':{},'volts':{},'source':{}}
    for h in range(24):
        slopes['lines'][(h,z.PRIMARY_LINE,'C')]={(s,d):-.015 for s in ('siteB','siteV') for d in (-1,1)}
        slopes['volts'][(h,'B','A')]={(s,d):.00005 for s in ('siteB','siteV') for d in (-1,1)}
        slopes['source'][h]={(s,d):-1 for s in ('siteB','siteV') for d in (-1,1)}
    sol=z.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
        ref,ref,slopes,trust_bess=150.,trust_v2g=11.,time_limit=20.,mip_gap=.10,
        voltage_weights=z.v23.PROFILES['balanced'],thermal_budget=500.,
        hard_line_caps={(8,z.PRIMARY_LINE,'C'):99.9},
        temporal_soft_caps={(15,z.PRIMARY_LINE,'C'):(99.9,500.),
                            (23,z.PRIMARY_LINE,'C'):(99.9,500.)})
    assert sol['hard_line_caps']==1
    assert sol['soft_temporal_caps']==2
    assert sol['soft_temporal_slack_pp']>=0
    assert -.015*sol['bess']['siteB'][8] <= -.2+.005
    with pytest.raises(ValueError,match='temporal ausente'):
        z.make_milp(team,['siteV'],cap,{'siteB':np.zeros(24)},np.zeros((1,24)),
          ref,ref,slopes,temporal_soft_caps={(2,'missing','C'):(99.9,500.)})


def test_unchanged_guard_provenance_and_safe_path(tmp_path):
    old=tmp_path/'v25'/'best';old.mkdir(parents=True)
    (old/'control_policy_v25.json').write_text('{}')
    out=tmp_path/'v26';out.mkdir()
    import shutil
    shutil.copytree(old,out/'best')
    assert (out/'best'/'control_policy_v25.json').read_text()=='{}'
    with pytest.raises(ValueError):
        z.v25.ensure_safe_output(tmp_path/'v25'/'changed',[tmp_path/'v25'])


def test_all_24_hours_critical_line_audit():
    ref,ac=make_pair()
    metrics,j=z.temporal_assessment(ref,ac)
    assert metrics['critical_line_overload_hours']==[8,10,14,15,21,23]
    assert len(j)==48
    assert metrics['critical_line_aggravation_pph']>0
