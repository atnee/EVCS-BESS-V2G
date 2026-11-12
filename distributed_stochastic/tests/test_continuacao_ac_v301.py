"""Regressoes da V3.0.1; incluindo alteracao de cwd por dependencias AC."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from distributed_stochastic import continuacao_ac_v301 as m


def ac_good():
    h=list(range(24))
    return {'branches':pd.DataFrame({'hour':h,'line':['L']*24,'phase':['A']*24,'loading_pct':[100.1]*24}),
     'voltages':pd.DataFrame({'hour':h,'bus':['B']*24,'phase':['C']*24,'v_pu':[.949]*24}),
     'source':pd.DataFrame({'hour':h,'p_kw':[1.]*24}),
     'regulators':pd.DataFrame({'hour':h,'regulator':['reg']*24,'tap':[1]*24}),
     'capacitors':pd.DataFrame({'hour':h,'capacitor':['cap']*24,'states':[0]*24})}


def scenario(tmp_path,monkeypatch,accepted=False,move_cwd=False,missing_ac=False):
    original=tmp_path/'reference'/'best'; original.mkdir(parents=True)
    for name in m.old.CHAMPION:
        p=original/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reference-'+name)
    repo=tmp_path/'repo';out=repo/'results'/'v301';out.mkdir(parents=True)
    other=tmp_path/'changed_cwd';other.mkdir()
    state={'team':{'vehicles':[],'old_fleet_profile':None},'places':[],'cap':{'s':{}},'sites':[],
          's1':None,'build_injections':lambda *a:pd.DataFrame({'hour':[0],'p_kw':[0.]})}
    def evaluate(repo,profiles,sites,inv,policy,folder):
        if not missing_ac:
            folder.mkdir(parents=True,exist_ok=False)
            for key,df in ac_good().items():df.to_csv(folder/f'{key}.csv',index=False)
        if move_cwd:os.chdir(other)
        return ac_good()
    ctx={'v281cdir':original.parent,'incumbent':{'bess':{'s':np.zeros(24)},
         'cars':np.zeros((150,24)),'metrics':{'orig':{},'policy':{},'focus':1},'ac':ac_good()},
         'state':state,'v22':SimpleNamespace(write_dispatch=lambda *a:None),
         'v24':SimpleNamespace(validate_resources=lambda *a:None,evaluate=evaluate),
         'repo':repo,'inv':{},'policy':{}}
    args=SimpleNamespace(output=str(out),sources='v291_bt0,v2931_bt2',alphas='0.05,0.1',
           max_ac_trials=2,include_mix=False,max_bess_repair_kw=15)
    sources={'v291_bt0':{},'v2931_bt2':{}}
    payload={'version':m.VERSION,'incumbent':str(original),
         'incumbent_sha256':m.old.baseline_digest(original),'sources':{},
         'parameters':{},'module_sha256':'digest','output_absolute':str(out),
         'selected':['v291_bt0_a0p05','v2931_bt2_a0p05']}
    monkeypatch.setattr(m,'manifest',lambda *a:payload)
    m.write_json_checked(out/m.PRECHECK,payload)
    monkeypatch.setattr(m.old,'build_blend',lambda *a:(ctx['incumbent']['bess'],
                                                     ctx['incumbent']['cars'],{}))
    monkeypatch.setattr(m.old.v28,'qmetrics',lambda *a: {'orig':{'strict_thermal_pp_h':120,
       'strict_voltage_samples':564,'strict_overload_samples':45,'new_overloaded_lines_vs_s0':0},
       'policy':{'thermal_aggravation_pp_h':110,'voltage_aggravation_pu_h':1.1}})
    monkeypatch.setattr(m.old.v291,'gate_v291',lambda *a:(accepted,[] if accepted else ['VOLTAGE_GUARD'],{}))
    return args,ctx,sources,original,out,other


def test_absolute_paths_independent_of_cwd(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    args=SimpleNamespace(repo='repo',baseline='ref',v281c_dir='ref',output='repo/results/run')
    m.normalize_paths(args)
    assert Path(args.output).is_absolute() and Path(args.baseline).is_absolute()
    monkeypatch.chdir(tmp_path.parent)
    assert Path(args.output)==tmp_path/'repo/results/run'


def test_filter_candidates_before_trial_budget():
    specs=m.old.combinations(['v291_bt0','v2931_bt2'],[.025,.05,.1],False)
    checks=[{'name':s['name'],'resources':'FAILED' if 'v2931' in s['name'] else 'OK'} for s in specs]
    good=m.selected_specifications(checks,specs,2)
    assert [s['name'] for s in good]==['v291_bt0_a0p025','v291_bt0_a0p05']


def test_json_and_csv_persist_with_readback(tmp_path):
    p=m.write_json_checked(tmp_path/'x.json',{'a':[1,2]})
    assert json.loads(p.read_text())=={'a':[1,2]}
    c=m.write_csv_checked(tmp_path/'x.csv',[{'name':'test','status':'AC_REJECTED'}])
    assert pd.read_csv(c).loc[0,'status']=='AC_REJECTED'


def test_cwd_change_during_ac_restored_and_reports_in_expected_folder(tmp_path,monkeypatch):
    args,ctx,sources,original,out,other=scenario(tmp_path,monkeypatch,False,move_cwd=True)
    start=Path.cwd()
    report=m.run(args,ctx,sources)
    assert Path.cwd()==start
    assert (out/m.FINAL).is_file() and (out/m.CSV).is_file() and (out/m.JOURNAL).is_file()
    assert len(pd.read_csv(out/m.CSV))==2
    assert report['candidates_accepted']==0
    assert len(report['cwd_changed_trials'])==2
    assert not list(other.rglob(m.CSV))
    assert m.old.baseline_digest(out/'best')==m.old.baseline_digest(original)
    with pytest.raises(FileExistsError):m.run(args,ctx,sources)


def test_missing_ac_files_fail_closed(tmp_path,monkeypatch):
    args,ctx,sources,original,out,_=scenario(tmp_path,monkeypatch,True,missing_ac=True)
    report=m.run(args,ctx,sources)
    assert report['candidates_errors']==2 and report['candidates_accepted']==0
    assert list(pd.read_csv(out/m.CSV).status)==['ERROR_NOT_EVALUATED']*2
    assert m.old.baseline_digest(out/'best')==m.old.baseline_digest(original)


def test_valid_ac_acceptance_requires_gate(tmp_path,monkeypatch):
    args,ctx,sources,original,out,_=scenario(tmp_path,monkeypatch,True)
    rep=m.run(args,ctx,sources)
    assert rep['candidates_accepted']==2
    assert (out/'best'/'report_v301.json').is_file()
    assert m.old.baseline_digest(original)['AC/branches.csv']==m.old.digest(original/'AC/branches.csv')


def test_audit_partial_does_not_promote(tmp_path,monkeypatch):
    args,ctx,sources,original,out,_=scenario(tmp_path,monkeypatch)
    (out/'trials'/'01_partial').mkdir(parents=True)
    result=m.audit_only(args)
    assert result['trial_records'][0]['status']=='INCOMPLETE_UNVERIFIED'
    assert not (out/'best').exists()


def test_precheck_records_invalid_and_omits_from_selected(tmp_path,monkeypatch):
    args,ctx,sources,original,out,_=scenario(tmp_path,monkeypatch)
    (out/m.PRECHECK).unlink()
    args.max_ac_trials=2
    original_blend=m.old.build_blend
    def blend(inc,sources,spec,cap,repair):
        if spec['source']=='v2931_bt2':raise ValueError('commercial-023: discharge below reserve')
        return inc['bess'],inc['cars'],{}
    monkeypatch.setattr(m.old,'build_blend',blend)
    result=m.precheck(args,ctx,sources)
    assert len(result['excluded'])==2
    assert result['selected']==['v291_bt0_a0p05','v291_bt0_a0p1']
    assert (out/m.PRECHECK).is_file()


def test_run_uses_only_prechecked_selection(tmp_path,monkeypatch):
    args,ctx,sources,original,out,_=scenario(tmp_path,monkeypatch)
    data=json.loads((out/m.PRECHECK).read_text())
    data['selected']=['v291_bt0_a0p05']
    m.write_json_checked(out/m.PRECHECK,data)
    monkeypatch.setattr(m,'manifest',lambda *a:{k:v for k,v in data.items() if k!='selected'})
    r=m.run(args,ctx,sources)
    assert r['candidates_selected']==1
    assert len(pd.read_csv(out/m.CSV))==1


def test_parser_exposes_audit():
    p=m.parser()
    assert p._option_string_actions['--task'].choices==('precheck','run','audit')
    assert p.get_default('output').endswith('v301_continuacao_ac_segura')
