"""Testes autocontidos do encerramento V2.9.3.1; nao executam MILP/OpenDSS."""
from __future__ import annotations
import csv
import json
import shutil
from pathlib import Path
import pytest
from distributed_stochastic import finalizar_v2931 as fin


def write_csv(p,fields,rows):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def ac_fixture(best):
    root=best/'AC'
    write_csv(root/'branches.csv',['hour','line','phase','loading_pct'],[
         {'hour':h,'line':'lnA','phase':'A','loading_pct':101 if h==0 else 90} for h in range(24)])
    write_csv(root/'voltages.csv',['hour','bus','phase','v_pu'],[
         {'hour':h,'bus':'busA','phase':'A','v_pu':.94 if h==0 else 1.} for h in range(24)])
    write_csv(root/'source.csv',['hour','p_kw'],[{'hour':h,'p_kw':1000+h} for h in range(24)])
    write_csv(root/'regulators.csv',['hour','regulator','tap'],[{'hour':h,'regulator':'r','tap':1} for h in range(24)])
    write_csv(root/'capacitors.csv',['hour','capacitor','states'],[{'hour':h,'capacitor':'c','states':'ON'} for h in range(24)])
    return {'strict_pass':False,'strict_overload_samples':1,'strict_voltage_samples':1,
            'strict_thermal_pp_h':1.,'strict_voltage_pu_h':.01,'max_loading_pct':101.,
            'vmin_pu':.94,'vmax_pu':1.,'source_peak_kw':1023.,
            'thermal_aggravation_pp_h':1.,'voltage_aggravation_pu_h':.01}


def scenario(tmp_path):
    ref=tmp_path/'v281_aceitacao_corrigida'; best=ref/'best'
    score=ac_fixture(best)
    for f in ('bess_dispatch.csv','vehicle_dispatch.csv','injections_reoptimized.csv','control_policy_v25.json'):
        (best/f).write_text('same',encoding='utf-8')
    for filename in ('ac_score_vs_original_s0.json','ac_score_vs_policy_s0.json'):
        (best/filename).write_text(json.dumps(score),encoding='utf-8')
    out=tmp_path/'v2931_hotfix'
    shutil.copytree(best,out/'best')
    data={p:fin.sha256(best/p) for p in fin.CHAMPION_FILES}
    iterdir=out/'iter_01';iterdir.mkdir()
    (iterdir/'incumbent_sha256_v2931.json').write_text(json.dumps(data),encoding='utf-8')
    (out/'run_manifest_v2931.json').write_text(json.dumps({
        'task':'run','output':'.\\results\\v2931_hotfix',
        'v281c_dir':'.\\results\\v281_aceitacao_corrigida','max_attempts':4}),encoding='utf-8')
    (out/'precheck_v2931.json').write_text(json.dumps({'status':'V2931_PRECHECK_OK'}),encoding='utf-8')
    for i,name in enumerate(('seed_all_full_trust','no_seed_half_trust','recalibrated_no_cuts_half_trust','recalibrated_progressive_full_trust')):
        trial=iterdir/f'trial_{i:02d}_{name}';trial.mkdir()
        report={'attempt':i,'strategy':name,'status':'MILP_INFEASIBLE' if i in (0,3) else 'AC_REJECTED',
                'gate_reasons':['UNIT_TEST_REJECT'] if i in (1,2) else [],
                'ac':{'policy':score,'orig':score} if i in (1,2) else {}}
        if i in (1,2):
            shutil.copytree(best/'AC',trial/'AC')
            for f in ('bess_dispatch.csv','vehicle_dispatch.csv','injections_reoptimized.csv'):
                shutil.copy(best/f,trial/f)
        else:(trial/'error_v2931.json').write_text('{}',encoding='utf-8')
        (trial/'report_v2931.json').write_text(json.dumps(report),encoding='utf-8')
    return out,ref


def test_finalize_works_without_v293_reference(tmp_path):
    out,ref=scenario(tmp_path)
    result=fin.finalize_existing_run(out,ref,tmp_path/'v293_missing')
    assert result['state']=='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V2931'
    assert result['ac_checked']==2
    assert result['v293_reference']=='UNAVAILABLE_AFTER_RUN'
    state=json.loads((out/'final_report_v2931.json').read_text())
    assert state['best_files_verified'] is True and state['n_accepted']==0
    assert state['absolute_AC_certification'] is False


def test_finalize_idempotent_and_no_new_ac(tmp_path):
    out,ref=scenario(tmp_path)
    fin.finalize_existing_run(out,ref)
    file=out/'final_report_v2931.json';before=file.read_bytes()
    fin.finalize_existing_run(out,ref)
    assert file.read_bytes()==before


def test_verify_only_makes_no_files(tmp_path):
    out,ref=scenario(tmp_path)
    fin.finalize_existing_run(out,ref,verify_only=True)
    assert not (out/'final_report_v2931.json').exists()
    assert not (out/'finalizacao_v2931_integridade.json').exists()


def test_tampered_best_fails_closed(tmp_path):
    out,ref=scenario(tmp_path)
    (out/'best'/'AC'/'voltages.csv').write_text('tampered',encoding='utf-8')
    with pytest.raises(RuntimeError,match='Best diverge'):
        fin.finalize_existing_run(out,ref)
    assert not (out/'final_report_v2931.json').exists()


def test_incomplete_ac_cannot_be_finalized(tmp_path):
    out,ref=scenario(tmp_path)
    f=out/'iter_01'/'trial_01_no_seed_half_trust'/'AC'/'voltages.csv'
    f.unlink()
    with pytest.raises(FileNotFoundError,match='AC de candidato incompleto'):
        fin.finalize_existing_run(out,ref)
    assert not (out/'final_report_v2931.json').exists()


def test_do_not_override_conflicting_final_report(tmp_path):
    out,ref=scenario(tmp_path)
    p=out/'final_report_v2931.json';p.write_text('{"not_the_real_report": true}')
    with pytest.raises(RuntimeError,match='ja existente e divergente'):
        fin.finalize_existing_run(out,ref)
    assert p.read_text()=='{"not_the_real_report": true}'


def test_accepted_candidate_is_not_silently_recovered(tmp_path):
    out,ref=scenario(tmp_path)
    path=out/'iter_01'/'trial_01_no_seed_half_trust'/'report_v2931.json'
    data=json.loads(path.read_text());data['status']='AC_ELIGIBLE_NOT_CERTIFIED';path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError,match='Status nao reconciliavel'):
        fin.finalize_existing_run(out,ref)


def test_missing_manifest_fails_closed(tmp_path):
    out,ref=scenario(tmp_path)
    (out/'iter_01'/'incumbent_sha256_v2931.json').unlink()
    with pytest.raises(FileNotFoundError,match='Manifesto SHA256'):
        fin.finalize_existing_run(out,ref)


def test_driver_does_not_abort_when_v293_disappears_only_after_precheck(tmp_path,monkeypatch):
    # A referencia historica foi verificada no inicio. Uma falha posterior
    # deve ser reportada, mas nao pode ocultar os resultados do MILP/AC.
    from distributed_stochastic import redespachar_milp_v2931 as driver
    from distributed_stochastic.tests import test_v2931_hotfix_directory as legacy
    original=driver.verify_champion_files
    calls={'v293':0}
    def with_late_missing(left,right):
        if Path(left).parts[-2:] == ('v293','best'):
            calls['v293']+=1
            if calls['v293']==2:
                raise FileNotFoundError('AC/branches.csv desapareceu depois do inicio')
        return original(left,right)
    monkeypatch.setattr(driver,'verify_champion_files',with_late_missing)
    out,_=legacy._simulate_run(tmp_path,monkeypatch,fail_first=False)
    report=json.loads((out/'final_report_v2931.json').read_text())
    assert report['n_accepted']==0
    assert report['best_files_verified'] is True
    assert report['historical_v293_postrun']['status']=='UNAVAILABLE_AFTER_RUN'
    assert calls['v293']==2
