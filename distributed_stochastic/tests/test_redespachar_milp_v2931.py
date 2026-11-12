"""Regressoes V2.9.3.1. Testes nao acessam rede nem executam OpenDSS."""
from __future__ import annotations
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from distributed_stochastic import redespachar_milp_v2931 as m
from distributed_stochastic._milp_independente_v2931 import make_milp_v2931
from distributed_stochastic._milp_independente_v293 import make_milp_v293


class FakeBuilder:
    instances=[]
    def __init__(self):
        self.rows=[];self.cost=[];self.instances.append(self)
    def var(self,cost,*bounds):
        i=len(self.cost);self.cost.append(float(cost));return i
    def row(self,terms,lo=None,hi=None):
        self.rows.append((terms,lo,hi))
    def solve(self,time_limit,mip_gap):
        return SimpleNamespace(status=0,message='mock',mip_gap=0.,fun=0.), \
               np.zeros(len(self.cost)),{'rows':len(self.rows),'variables':len(self.cost)}


def mock_deps(monkeypatch):
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    r=types.ModuleType('rebuild.joint_optimization');r.MatrixBuilder=FakeBuilder
    monkeypatch.setitem(sys.modules,'rebuild.joint_optimization',r)
    monkeypatch.setitem(sys.modules,'v2g',types.ModuleType('v2g'))
    a=types.ModuleType('v2g.availability');a.session_steps=lambda *unused:[17]
    monkeypatch.setitem(sys.modules,'v2g.availability',a)
    s=types.ModuleType('v2g.strategies');s.fleet_profile=lambda *unused:None
    monkeypatch.setitem(sys.modules,'v2g.strategies',s)


def miniature_case():
    batt=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,
        soc_min=.1,soc_max=.9,eta_charge=.95,eta_discharge=.95)
    car=SimpleNamespace(battery=batt,arrival=17,departure=18,
        departure_target_kwh=15.,reserve_kwh=8.)
    team={'vehicles':[car],'station':None,'grid':None,'ind':None}
    loads=pd.DataFrame([{'hour':23,'line':'focus','phase':'C','loading_pct':104.}])
    volts=pd.DataFrame([{'hour':17,'bus':'low','phase':'A','v_pu':.94},
                        {'hour':19,'bus':'high','phase':'C','v_pu':1.056}])
    src=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':loads,'voltages':volts,'source':src}
    coeff={('s',1):.001,('s',-1):.001,('m',1):.001,('m',-1):.001}
    slopes={'lines':{(23,'focus','C'):coeff},
            'volts':{(17,'low','A'):coeff,(19,'high','C'):coeff},
            'source':{h:coeff for h in range(24)}}
    basic=(team,['m'],{'s':{'power_kw':100.,'energy_kwh':200.}},
          {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes)
    kw={'hard_line_caps':{(23,'focus','C'):103.9},
        'protected_voltage':{(17,'low','A'):.939},
        'voltage_ceilings':{(19,'high','C'):1.057},
        'absolute_thermal_target':4.,'strict_voltage_budget_puh':.02,
        'focus_target':((23,'focus','C'),103.9)}
    return basic,kw


def model_args():
    return SimpleNamespace(bess_trust_kw=100,v2g_trust_kw=60,time_limit_s=60,
       mip_gap=.03,local_peak_growth_kw=60,
       predicted_absolute_gain_pph=.05,strict_voltage_budget_puh=.02,
       max_line_growth_pp=.02,nonoverloaded_margin_pp=.005,
       diagnostic_time_limit_s=20,progressive_cut_count=4,
       max_diagnostic_solves=4,max_diagnostic_events=1)


def cuts():
    return {'lines':[(1.,(23,'focus','C'),103.9,.01)],
            'under':[(.2,(17,'low','A'),.939,.0001)],
            'over':[(.1,(19,'high','C'),1.057,.0002)],'summary':{}}


def test_strict_milp_identical_to_v293(monkeypatch):
    mock_deps(monkeypatch);FakeBuilder.instances=[]
    x,kw=miniature_case()
    original=make_milp_v293(*x,**kw)
    first=FakeBuilder.instances[-1]
    updated=make_milp_v2931(*x,**kw)
    second=FakeBuilder.instances[-1]
    assert len(first.rows)==len(second.rows)
    assert first.rows==second.rows
    assert first.cost==second.cost
    assert not updated['diagnostic_mode']
    assert not updated['phase1']
    assert original['model_stats']==updated['model_stats']


def test_phase1_only_electrical_diagnostic_costs(monkeypatch):
    mock_deps(monkeypatch);FakeBuilder.instances=[]
    x,kw=miniature_case()
    result=make_milp_v2931(*x,**kw,diagnostic_elastic=True,phase1=True)
    diag=FakeBuilder.instances[-1]
    assert result['diagnostic_mode'] and result['phase1']
    assert result['phase1_objective_only_electrical_slacks']
    assert all(k in result['diagnostic_slacks'] for k in (
        'learned_line','learned_under','learned_over','sample_line','focus',
        'source_peak','aggregate_thermal','aggregate_voltage'))
    diag_counts={k:len(v) for k,v in result['diagnostic_slacks'].items()}
    assert diag_counts['sample_line']==1 and diag_counts['focus']==1
    assert diag_counts['source_peak']==24
    assert diag_counts['aggregate_voltage']==1
    assert diag_counts['aggregate_thermal']==1
    assert sum(v>0 for v in diag.cost)==sum(diag_counts.values())


def test_reject_phase1_in_official_milp(monkeypatch):
    mock_deps(monkeypatch)
    x,kw=miniature_case()
    with pytest.raises(ValueError,match='Phase I'):
        make_milp_v2931(*x,**kw,phase1=True)
    with pytest.raises(ValueError,match='EXCLUSIVAS'):
        make_milp_v2931(*x,**kw,elastic_line_keys=[(23,'focus','C')])


def test_diagnostic_ablation_does_not_send_elastic_keys_to_strict(monkeypatch,tmp_path):
    observed=[]
    def fake(*a,**kw):
        observed.append(kw)
        assert kw['diagnostic'] == kw['phase1']
        if not kw['diagnostic']:
            assert not kw.get('diag_line_keys')
            assert not kw.get('diag_under_keys')
            assert not kw.get('diag_over_keys')
            return {'solver_status':0,'mip_gap':.01,'phase1':False,
                    'diagnostic_mode':False}
        return {'solver_status':0,'mip_gap':.01,'phase1':True,
                'diagnostic_mode':True,'objective':.0,
                'diagnostic_slacks':{'learned_line':[],'learned_under':[],
                    'learned_over':[],'sample_line':[],'focus':[],'source_peak':[],
                    'aggregate_thermal':[],'aggregate_voltage':[]}}
    monkeypatch.setattr(m,'solve_model',fake)
    res=m.diagnose_infeasibility({},model_args(),{},None,None,cuts(),
                               ({},{},{}),.5,tmp_path/'diag')
    assert res['diagnostic_only'] and res['never_run_AC']
    assert len(observed)==4
    assert all(x['status']=='MILP_PRIMAL_FOUND' for x in res['outcomes'])
    assert res['group_classification_not_IIS']=='POSSIBLE_JOINT_GROUP_CONFLICT'


def test_phase1_report_keeps_positive_slacks_as_rejected(monkeypatch,tmp_path):
    def fake(*a,**kw):
        return {'diagnostic_mode':True,'phase1':True,'objective':.05,
                'diagnostic_slacks':{'learned_line':[], 'learned_under':[],
                  'learned_over':[{'key':[8,'m1209800','B'],'slack':.00001347}],
                  'sample_line':[],'focus':[],'source_peak':[],
                  'aggregate_thermal':[],'aggregate_voltage':[]}}
    monkeypatch.setattr(m,'solve_model',fake)
    r=m.phase1_audit({},model_args(),{},None,None,({}, {}, {}, {}, {}),
                     .5,tmp_path/'diagnosis')
    assert r['status']=='PHASE1_POSITIVE_SLACK' and not r['zero_electrical_slacks']
    assert r['never_run_AC'] and r['never_accept']
    assert r['nonzero_groups']['learned_over']==1


def test_phase1_zero_means_only_local_surrogate_reachable(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'solve_model',lambda *a,**kw:{
        'diagnostic_mode':True,'phase1':True,'objective':0.,
        'diagnostic_slacks':{'learned_over':[{'slack':1e-9}]}})
    r=m.phase1_audit({},model_args(),{},None,None,({}, {}, {}, {}, {}),
                     .5,tmp_path/'diag')
    assert r['status']=='PHASE1_ZERO_SLACK'
    assert r['qualification'].startswith('No certification')


def test_snapshot_restores_from_memory_without_rereading_v29(monkeypatch,tmp_path):
    for f in m.CHAMPION_FILES:
        path=tmp_path/f;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(f)
    inc={'bess':{'s':np.array([3.,-2.])},'cars':np.array([[1.,2.]]),
         'ac':{'voltages':pd.DataFrame({'v_pu':[.94]})},'path':tmp_path}
    slopes={'lines':{(15,'x','C'):{('s',1):.3}},'volts':{},'source':{}}
    snapshot=m.snapshot_incumbent(inc,slopes,'MEMORY',tmp_path)
    inc['bess']['s'][0]=999
    slopes['lines'][(15,'x','C')][('s',1)]=-1
    monkeypatch.setattr(m.v292,'get_slopes',lambda *a,**kw:
        (_ for _ in ()).throw(RuntimeError('cache V2.9 unavailable')))
    restored,derivs,src=m.restore_from_memory(snapshot,tmp_path)
    assert restored['bess']['s'][0]==3.
    assert derivs['lines'][(15,'x','C')][('s',1)]==.3
    assert src=='MEMORY'


def test_snapshot_detects_upstream_mutation(tmp_path):
    for f in m.CHAMPION_FILES:
        path=tmp_path/f;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(f)
    inc={'ac':{'branches':pd.DataFrame()},'path':tmp_path}
    snap=m.snapshot_incumbent(inc,{},'cache',tmp_path)
    (tmp_path/'AC/branches.csv').write_text('alterado')
    with pytest.raises(RuntimeError,match='Arquivo base modificado'):
        m.restore_from_memory(snap,tmp_path)


def test_source_best_identical_by_hash_and_detect_mismatch(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b'
    for name in m.CHAMPION_FILES:
        for root in (a,b):
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name)
    assert len(m.verify_champion_files(a,b))==len(m.CHAMPION_FILES)
    (a/'AC/branches.csv').write_text('BAD')
    with pytest.raises(RuntimeError,match='Incumbente historico alterado'):
        m.verify_champion_files(a,b)


def test_duplicate_signature_stable_under_reordering():
    a=pd.DataFrame([{'hour':0,'bus':'m2','phase':'C','p_kw':2.,'q_kvar':0.},
                    {'hour':0,'bus':'m1','phase':'A','p_kw':3.,'q_kvar':1.}])
    assert m.injection_signature(a)==m.injection_signature(a.iloc[::-1])
    b=a.copy();b.loc[0,'p_kw']=2.1
    assert m.injection_signature(a)!=m.injection_signature(b)
    c=pd.concat([a,a.iloc[[0]]],ignore_index=True)
    with pytest.raises(ValueError,match='duplicadas'):
        m.injection_signature(c)


def test_cannot_reuse_prior_ac_without_files(tmp_path):
    args=SimpleNamespace(v293_dir=str(tmp_path))
    assert m.prior_ac_if_identical({},args,pd.DataFrame()) is None


def test_best_publish_has_rollback_when_rename_fails(monkeypatch,tmp_path):
    out=tmp_path/'out';out.mkdir()
    (out/'best').mkdir();(out/'best'/'marker').write_text('original')
    trial=tmp_path/'trial';trial.mkdir();(trial/'marker').write_text('new')
    original_rename=Path.rename
    def fail_staging(self,target):
        if self.name=='_candidate_best_staging':
            raise OSError('disk failure')
        return original_rename(self,target)
    monkeypatch.setattr(Path,'rename',fail_staging)
    with pytest.raises(OSError,match='disk failure'):
        m.copy_best_atomically(out,trial,{'orig':{},'policy':{}},{'name':'policy'})
    assert (out/'best'/'marker').read_text()=='original'
    assert not (out/'_previous_best_backup').exists()


def test_parser_new_mode_and_source_path():
    p=m.parser()
    actions={x.dest:x for x in p._actions}
    assert 'v293_dir' in actions
    assert 'reuse_verified_v293_ac' in actions
    # parser dynamically adds diagnose at command time; other tasks preserved.
    assert 'run' in actions['task'].choices


def test_real_archive_historical_reference_matches_original():
    # Dados reais desta conversa, teste opcional quando arquivo montado.
    a=Path('/mnt/data/v293_audit_work/v293_milp_diagnostico_recalibracao/best')
    b=Path('/mnt/data/v281_audit_work/v281_aceitacao_corrigida/best')
    if not (a.exists() and b.exists()):pytest.skip('Arquivos reais nao estao disponiveis localmente')
    assert len(m.verify_champion_files(a,b))==9


def test_real_archive_diagnostic_legacy_failures_not_repeated():
    f=Path('/mnt/data/v293_audit_work/v293_milp_diagnostico_recalibracao/iter_01/trial_00_seed_all_full_trust/diagnostics/diagnostic_summary_v293.json')
    if not f.exists():pytest.skip('Historico real nao disponivel')
    old=json.loads(f.read_text())
    wrong=[o for o in old['outcomes'] if o['name'] in ('APENAS_CORTES_TERMICOS','APENAS_CORTES_TENSAO')]
    assert len(wrong)==2 and all(o['status']=='UNKNOWN_OR_ERROR' for o in wrong)
    assert all('EXCLUSIVAS' in o.get('error','') for o in wrong)


def test_run_preserves_best_when_all_milps_infeasible(monkeypatch,tmp_path):
    source=tmp_path/'original_best';prior=tmp_path/'v293'/'best'
    for name in m.CHAMPION_FILES:
        for root in (source,prior):
            f=root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(name)
    out=tmp_path/'new_run';out.mkdir()
    incumbent={'ac':{'branches':pd.DataFrame()},'bess':{},'cars':np.zeros((1,24)),
               'profiles':pd.DataFrame(),'metrics':{'orig':{},'policy':{}},'path':source}
    ctx={'output':out,'incumbent':incumbent,'v281cdir':tmp_path,
         'policy':{'name':'p'}}
    # source = v281cdir/best
    saved=tmp_path/'best';source.rename(saved)
    incumbent['path']=saved
    args=SimpleNamespace(v293_dir=str(tmp_path/'v293'),max_attempts=3,
        max_diagnostic_events=0,progressive_cut_count=4,
        bess_trust_kw=100,v2g_trust_kw=60)
    monkeypatch.setattr(m.v292,'get_slopes',lambda *a,**kw:({'lines':{},'volts':{},'source':{}},'CACHE'))
    monkeypatch.setattr(m.v292,'seed_from_v291',lambda *a,**kw:({'lines':[],'under':[],'over':[]},None))
    monkeypatch.setattr(m,'build_model_context',lambda *a,**kw:({}, {}, None,set(),{},{}))
    monkeypatch.setattr(m,'solve_model',lambda *a,**kw:(_ for _ in ()).throw(
        RuntimeError('status=2 model_status is Infeasible')))
    m.run(args,ctx)
    report=json.loads((out/'final_report_v2931.json').read_text())
    assert report['champion']=='V281_CORRIGIDA_BT0'
    assert report['n_accepted']==0 and report['attempts']==3
    assert len(m.verify_champion_files(out/'best',saved))==9
