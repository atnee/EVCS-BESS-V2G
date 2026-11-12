"""Regressions for V2.9.3.1 Windows WinError 183 from premature trial folder creation.

These tests mock solver and AC; they do NOT certify electrical results.
"""
import json
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
from distributed_stochastic import redespachar_milp_v2931 as m


def _champion(root):
    for rel in m.CHAMPION_FILES:
        f=root/rel
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_text('unchanged: '+rel,encoding='utf-8')


def _simulate_run(tmp_path,monkeypatch, *, fail_first=True, validation_failure=False):
    original=tmp_path/'original'/'best'
    old=tmp_path/'v293'/'best'
    _champion(original);_champion(old)
    out=tmp_path/'out';out.mkdir()
    incumbent={'path':original, 'metrics':{'orig':{}, 'policy':{}},'ac':{}}
    ctx={'output':out, 'v281cdir':original.parent,'incumbent':incumbent,
         'policy':{'name':'fase_C_reduzida'},'guard_table':pd.DataFrame()}
    args=SimpleNamespace(v293_dir=str(old.parent), max_attempts=2 if fail_first else 1,
         max_diagnostic_events=0,reuse_verified_v293_ac=True,
         progressive_cut_count=4,bess_trust_kw=100.,v2g_trust_kw=60.)
    monkeypatch.setattr(m.v292,'get_slopes',lambda *a,**kw:({'lines':{},'volts':{},'source':{}},'FAKE'))
    monkeypatch.setattr(m.v292,'seed_from_v291',lambda *a,**kw:({'lines':[], 'under':[], 'over':[]},{}))
    monkeypatch.setattr(m,'build_model_context',lambda *a,**kw:({}, {}, None, set(), {}, {}))
    monkeypatch.setattr(m,'make_cuts_for_mode',lambda *a,**kw:({}, {}, {}, {}, {}))
    monkeypatch.setattr(m,'plans_v293',lambda n:[{'name':'seed_all_full_trust','ratio':1.,'cut_mode':'full'},
        {'name':'no_seed_half_trust','ratio':.5,'cut_mode':'none'}][:n])
    calls={'solver':0,'validation':0}
    def solver(*a,**kw):
        calls['solver']+=1
        if fail_first and calls['solver']==1:
            raise RuntimeError('status=2; model_status is Infeasible')
        return {'diagnostic_mode':False,'phase1':False,
                'solver_status':0,'mip_gap':.01,'model_stats':{},
                'bess':{'s':[]},'cars':[]}
    monkeypatch.setattr(m,'solve_model',solver)
    def validation(ctx,sol,folder):
        calls['validation']+=1
        assert not folder.exists(), 'Regression: driver pre-created trial folder'
        if validation_failure:
            raise RuntimeError('dispatch was invalid')
        folder.mkdir(parents=True,exist_ok=False)  # matches V2.9 contract
        (folder/'bess_dispatch.csv').write_text('dummy')
        return pd.DataFrame([{'hour':0,'bus':'s','phase':'A','p_kw':0.,'q_kvar':0.}])
    monkeypatch.setattr(m.v29,'validate_model_solution',validation)
    monkeypatch.setattr(m,'prior_ac_if_identical',lambda *a,**kw:{'ac':{'branches':pd.DataFrame()},
       'metrics':{'orig':{'strict_thermal_pp_h':0},'policy':{'thermal_aggravation_pp_h':0,
                 'voltage_aggravation_pu_h':0}},'from':'test verified'})
    monkeypatch.setattr(m.v291,'gate_v291',lambda *a,**kw:(False,['UNIT_TEST_REJECTED'],{}))
    monkeypatch.setattr(m.v27,'guard_values',lambda *a,**kw:pd.DataFrame())
    monkeypatch.setattr(m.v292,'changed_taps',lambda *a,**kw:0)
    monkeypatch.setattr(m.v292,'full_ac_cuts',lambda *a,**kw:{'lines':[],'under':[],'over':[],'summary':{}})
    monkeypatch.setattr(m,'should_recalibrate',lambda *a,**kw:False)
    m.run(args,ctx)
    return out,calls


def test_milp_infeasible_then_valid_dispatch_directory_is_not_precreated(tmp_path,monkeypatch):
    out,calls=_simulate_run(tmp_path,monkeypatch,fail_first=True)
    assert calls=={'solver':2,'validation':1}
    final=json.loads((out/'final_report_v2931.json').read_text())
    assert [x['status'] for x in final['experiments']]==['MILP_INFEASIBLE','AC_REJECTED']
    root=out/'iter_01'
    for d in ('trial_00_seed_all_full_trust','trial_01_no_seed_half_trust'):
        assert (root/d/'model_configuration_v2931.json').is_file()
        assert (root/d/'report_v2931.json').is_file()
    assert (root/'trial_01_no_seed_half_trust'/'bess_dispatch.csv').exists()
    assert final['best_files_verified'] is True


def test_validation_exception_preserves_logs_and_original(tmp_path,monkeypatch):
    out,calls=_simulate_run(tmp_path,monkeypatch,fail_first=False,validation_failure=True)
    assert calls['validation']==1
    trial=out/'iter_01'/'trial_00_seed_all_full_trust'
    assert (trial/'model_configuration_v2931.json').is_file()
    assert (trial/'report_v2931.json').is_file()
    assert 'dispatch was invalid' in (trial/'error_v2931.json').read_text()
    assert json.loads((out/'final_report_v2931.json').read_text())['n_accepted']==0
