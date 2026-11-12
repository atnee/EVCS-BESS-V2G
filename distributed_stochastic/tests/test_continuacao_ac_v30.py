"""Regressao V3.0: preservacao, hashes, selecao, AC e confiabilidade."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from distributed_stochastic import continuacao_ac_v30 as m


def test_names_valid():
    assert m.parse_names('v291_bt0,v2931_bt2', m.SOURCES)==['v291_bt0','v2931_bt2']

@pytest.mark.parametrize('s',['','foo','v291_bt0,foo','v291_bt0,v291_bt0','v291_bt0,'])
def test_names_invalid(s):
    with pytest.raises(ValueError):m.parse_names(s,m.SOURCES)


def test_alphas_sorted():
    assert m.parse_alphas('.2,.025,.05')==[.025,.05,.2]

@pytest.mark.parametrize('s',['','0','1.1','-0.5','foo'])
def test_alphas_invalid(s):
    with pytest.raises(ValueError):m.parse_alphas(s)


def test_interleaved_sources_and_mixes():
    a=m.combinations(['v291_bt0','v2931_bt2'],[.05,.1],True)
    assert len(a)==6
    assert [v['source'] for v in a[:3]]==['v291_bt0','v2931_bt2','v291_bt0+v2931_bt2']
    assert a[2]['weights']=={'v291_bt0':.5,'v2931_bt2':.5}


def test_output_separation(tmp_path):
    protected=tmp_path/'results'/'old';protected.mkdir(parents=True)
    assert m.validate_output(tmp_path/'results'/'new',[protected]) == (tmp_path/'results'/'new').resolve()
    for bad in [protected,protected/'other',tmp_path/'results']:
        with pytest.raises(ValueError):m.validate_output(bad,[protected])


def test_atomic_json_and_hash(tmp_path):
    p=tmp_path/'a.json';m.json_write_atomic(p,{'pass':True});h=m.digest(p)
    assert json.loads(p.read_text())=={'pass':True}
    m.json_write_atomic(p,{'pass':True});assert m.digest(p)==h
    assert not list(tmp_path.glob('*.tmp-*'))


def test_atomic_json_nan_refused(tmp_path):
    p=tmp_path/'x.json'
    with pytest.raises(ValueError):m.json_write_atomic(p,{'bad':float('nan')})
    assert not p.exists()
    assert not list(tmp_path.glob('*.tmp-*'))


def test_manifest_immutable():
    a={'version':'v3','incumbent':'x','incumbent_sha256':{'x':'1'},'sources':{},'parameters':{},'module_sha256':'z'}
    m.validate_manifest(a,a.copy())
    for k in a:
        b=a.copy();b[k]='mismatch'
        with pytest.raises(RuntimeError):m.validate_manifest(a,b)


def test_choose_best_respects_only_accepted():
    rows=[{'status':'AC_REJECTED','absolute_pph':0},
          {'status':'AC_ELIGIBLE_NOT_CERTIFIED','absolute_pph':124,'thermal_policy_pph':119,'voltage_policy_puh':1.1},
          {'status':'AC_ELIGIBLE_NOT_CERTIFIED','absolute_pph':122,'thermal_policy_pph':120,'voltage_policy_puh':1.4},
          {'status':'ERROR_NOT_EVALUATED','absolute_pph':1}]
    assert m.choose_best(rows,{}) is rows[2]
    assert m.choose_best(rows[:1],{}) is None


def ac_good():
    h=list(range(24))
    return {'branches':pd.DataFrame({'hour':h,'line':['L']*24,'phase':['A']*24,'loading_pct':[100.1]*24}),
            'voltages':pd.DataFrame({'hour':h,'bus':['B']*24,'phase':['C']*24,'v_pu':[.949]*24}),
            'source':pd.DataFrame({'hour':h,'p_kw':[1.]*24}),
            'regulators':pd.DataFrame({'hour':h,'regulator':['reg']*24,'tap':[1]*24}),
            'capacitors':pd.DataFrame({'hour':h,'capacitor':['cap']*24,'states':[0]*24})}


def test_full_ac_schema():
    m.validate_ac_frame(ac_good())

@pytest.mark.parametrize('error',["missing_hour","duplicate_key","nan","not_full_controls"])
def test_invalid_ac_schema(error):
    ac=ac_good()
    if error=='missing_hour':ac['source']=ac['source'].iloc[:-1]
    elif error=='duplicate_key':ac['branches'].loc[1,'hour']=0
    elif error=='nan':ac['voltages'].loc[0,'v_pu']=float('nan')
    else:ac['capacitors']=ac['capacitors'].iloc[:-1]
    with pytest.raises(ValueError):m.validate_ac_frame(ac)


def test_blend_and_soc_repair_called(monkeypatch):
    calls=[]
    def fake_repair(bess,cap,limit):
        calls.append(limit)
        return bess,{'s':{'max_adjustment_kw':0.}}
    monkeypatch.setattr(m.v27,'repair_bess_soc',fake_repair)
    start={'bess':{'s':np.ones(24)*10},'cars':np.ones((150,24))*2}
    sources={'a':{'bess':{'s':np.ones(24)*30},'cars':np.ones((150,24))*6},
             'b':{'bess':{'s':np.ones(24)*20},'cars':np.ones((150,24))*4}}
    spec={'alpha':.25,'weights':{'a':.5,'b':.5}}
    bess,cars,repair=m.build_blend(start,sources,spec,{},15)
    assert np.allclose(cars,2.75)
    assert np.allclose(bess['s'],13.75)
    assert calls==[15]


def test_reject_bad_blend(monkeypatch):
    start={'bess':{'s':np.zeros(24)},'cars':np.zeros((150,24))}
    sources={'a':{'bess':{'s':np.ones(24)},'cars':np.ones((150,24))}}
    for weights in ({'a':2.},{'a':-.1}):
        with pytest.raises(ValueError):m.build_blend(start,sources,{'alpha':.1,'weights':weights},{},15)


def test_csv_partial_writing(tmp_path):
    p=tmp_path/'candidates.csv'
    m.csv_write_atomic(p,[{'name':'x','status':'ERROR_NOT_EVALUATED'}])
    assert pd.read_csv(p).loc[0,'status']=='ERROR_NOT_EVALUATED'
    m.csv_write_atomic(p,[{'name':'x','status':'AC_REJECTED'}])
    assert pd.read_csv(p).loc[0,'status']=='AC_REJECTED'
    assert not list(tmp_path.glob('*.tmp-*'))


def test_hash_detects_mutation(tmp_path):
    p=tmp_path/'x';p.write_bytes(b'abc')
    assert m.digest(p) != m.digest(__file__)
    h=m.digest(p);p.write_bytes(b'abc+');assert m.digest(p)!=h


def test_parser_options():
    p=m.parser()
    assert p._option_string_actions['--task'].choices==('precheck','run') or 'precheck' in p._option_string_actions['--task'].choices
    assert '--v2931-dir' in p._option_string_actions
    assert '--v291-dir' in p._option_string_actions
    assert '--max-ac-trials' in p._option_string_actions


def fake_run_env(tmp_path, monkeypatch, accepted):
    """Simula erros de AC e aceites, com os nove arquivos reais esperados."""
    src=tmp_path/'reference'/'best';src.mkdir(parents=True)
    for name in m.CHAMPION:
        p=src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reference-'+name)
    out=tmp_path/'results'/'v30';out.mkdir(parents=True)
    state={'team':{'vehicles':[], 'old_fleet_profile':None},'places':[],'cap':{'s':{}},'sites':[],
           's1':None,'build_injections':lambda *a:pd.DataFrame({'hour':[0], 'p_kw':[0]})}
    ctx={'v281cdir':src.parent,'incumbent':{'bess':{'s':np.zeros(24)},'cars':np.zeros((150,24)),
       'metrics':{'orig':{},'policy':{},'focus':1},'ac':{}},
       'state':state,'v22':SimpleNamespace(write_dispatch=lambda *a:None),
       'v24':SimpleNamespace(validate_resources=lambda *a:None,
                             evaluate=lambda *a: ac_good()),
       'repo':tmp_path,'inv':{},'policy':{}}
    args=SimpleNamespace(output=str(out),sources='v291_bt0,v2931_bt2',alphas='0.05',
      max_ac_trials=2,include_mix=False,max_bess_repair_kw=15)
    sources={'v291_bt0':{},'v2931_bt2':{}}
    expected={'version':'V3.0-AC-CONTINUATION','incumbent':str(src),
      'incumbent_sha256':m.baseline_digest(src),'sources':{},
      'parameters':{},'module_sha256':'sha'}
    monkeypatch.setattr(m,'check_manifest',lambda *a:expected)
    m.json_write_atomic(out/'precheck_v30.json',expected)
    monkeypatch.setattr(m,'build_blend',lambda *a:(ctx['incumbent']['bess'],ctx['incumbent']['cars'],{}))
    monkeypatch.setattr(m.v28,'qmetrics',lambda *a: {'orig':{'strict_thermal_pp_h':120,
       'strict_voltage_samples':564,'strict_overload_samples':45,'new_overloaded_lines_vs_s0':0},
       'policy':{'thermal_aggravation_pp_h':110,'voltage_aggravation_pu_h':1.1}})
    monkeypatch.setattr(m.v291,'gate_v291',lambda *a: (accepted, [] if accepted else ['VOLTAGE_GUARD'],{}))
    return args,ctx,sources,src,out


def test_run_no_accepted_preserves_incumbent(tmp_path,monkeypatch):
    args,ctx,sources,src,out=fake_run_env(tmp_path,monkeypatch,False)
    report=m.run(args,ctx,sources)
    assert report['state']=='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V30'
    assert report['candidates_accepted']==0
    assert m.baseline_digest(out/'best')==m.baseline_digest(src)
    assert report['reference_preserved'] is True
    assert len(pd.read_csv(out/'todos_candidatos_v30.csv'))==2
    with pytest.raises(FileExistsError):m.run(args,ctx,sources)


def test_run_accepts_only_ac_gate(tmp_path,monkeypatch):
    args,ctx,sources,src,out=fake_run_env(tmp_path,monkeypatch,True)
    report=m.run(args,ctx,sources)
    assert report['state']=='AC_ELIGIBLE_NOT_CERTIFIED'
    assert report['candidates_accepted']==2
    assert (out/'best'/'report_v30.json').is_file()
    assert (out/'best'/'control_policy_v25.json').is_file()
    assert m.baseline_digest(src)['AC/branches.csv']==m.digest(src/'AC/branches.csv')


def test_run_simulation_exception_never_accepted(tmp_path,monkeypatch):
    args,ctx,sources,src,out=fake_run_env(tmp_path,monkeypatch,True)
    def failure(*args):raise RuntimeError('OpenDSS failure')
    ctx['v24'].evaluate=failure
    report=m.run(args,ctx,sources)
    assert report['candidates_errors']==2
    assert report['candidates_simulated']==0
    assert report['candidates_accepted']==0
    assert report['state']=='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V30'
    assert m.baseline_digest(src)==m.baseline_digest(out/'best')
    assert all(pd.read_csv(out/'todos_candidatos_v30.csv').status=='ERROR_NOT_EVALUATED')
