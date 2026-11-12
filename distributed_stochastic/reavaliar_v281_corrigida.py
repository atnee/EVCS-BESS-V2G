#!/usr/bin/env python3
"""Reavalia resultados AC arquivados da V2.8.1 com orcamentos de tensao duplos.

Nao chama OpenDSS, nao recalcula despacho e NAO altera resultados originais.
Reconstitui as metricas e guardas no repositorio, confere dados de recursos,
politica e integridade dos candidatos, e copia apenas o melhor elegivel para
uma NOVA pasta. Nao certifica viabilidade absoluta da rede.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import traceback

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from distributed_stochastic import otimizar_conjunto_v281 as v281
from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic import validar_passo_v27 as v27

KEYS = {'branches': ['hour', 'line', 'phase'],
        'voltages': ['hour', 'bus', 'phase'],
        'source': ['hour'],
        'regulators': ['hour', 'regulator'],
        'capacitors': ['hour', 'capacitor']}
REQUIRED_TRIAL_FILES = ('report_v281.json', 'injections_reoptimized.csv',
                        'bess_dispatch.csv', 'vehicle_dispatch.csv')


def require_distinct_output(output: Path, paths: list[Path]):
    output = output.resolve()
    for p in paths:
        p = p.resolve()
        if output == p or output in p.parents or p in output.parents:
            raise ValueError(f'Saida contem ou sobrepoe uma entrada: {output} / {p}')
    if (output/'best').exists() or (output/'final_report_v281_corrigida.json').exists():
        raise FileExistsError('Ja existe resultado nesta pasta; selecione outra --output')


def compare_ac(ac1: dict, ac2: dict, caption: str):
    """Compara todas as tabelas AC; rejeita registros faltantes ou reordenados com erros."""
    for kind,keys in KEYS.items():
        a,b = ac1[kind],ac2[kind]
        for frame in (a,b):
            if frame.duplicated(keys).any() or frame.isna().any().any():
                raise ValueError(f'{caption}: AC {kind} duplicado ou com dados ausentes')
            if set(frame['hour'].astype(int)) != set(range(24)):
                raise ValueError(f'{caption}: AC {kind} deve cobrir todas as 24 h')
        assert_frame_equal(a.sort_values(keys).reset_index(drop=True),
                           b.sort_values(keys).reset_index(drop=True),
                           check_dtype=False, check_exact=False, rtol=0, atol=1e-9,
                           obj=f'{caption}/{kind}')


def check_archive_identity(previous: Path, ctx: dict, manifest: dict):
    """Somente resultado original sem candidatos aceitos (V2.7 ainda no best)."""
    old = v27.read_json(previous/'final_report_v281.json')
    if old.get('accepted_v281') != 0 or old.get('champion') != 'V2.7':
        raise RuntimeError('Reauditoria de incumbentes V2.8.1 aceitos nao suportada; nao encadear propostas')
    if old.get('state') != 'V27_PRESERVED_NO_ACCEPTED_V281':
        raise RuntimeError('Estado da V2.8.1 original inesperado')
    if old.get('policy') != ctx['policy']:
        raise RuntimeError('Politica diferente da V2.7')
    if abs(float(manifest.get('voltage_budget_puh', -1)) - .03) > 1e-10:
        raise RuntimeError('Rodada original nao usou o orcamento incremental documentado de 0.03 pu.h')
    for k,v in (('orig',ctx['start_metrics']['orig']),
                ('policy',ctx['start_metrics']['policy'])):
        v27.score_close(v,old['reference_v27'][k],f'incumbente {k}')
    if abs(ctx['start_metrics']['focus'] - old['reference_v27']['focus'])>1e-6:
        raise RuntimeError('Metrica foco V2.7 divergente')
    for pkey in ('repo','baseline','v22_dir','v23_dir','v24_dir',
                 'v25_dir','v26_dir','v27_dir','v28_dir'):
        # A origem historica pode ter sido movida para outra pasta. Os arquivos
        # atuais sao sempre revalidados por politica, recursos e AC.
        if pkey not in manifest:
            raise ValueError(f'Manifest original incompleto: {pkey}')
    oldbest=ctx['v22'].read_ac_folder(previous/'best'/'AC')
    compare_ac(ctx['ac27'],oldbest,'best V2.8.1 = V2.7 original')
    return old


def check_resources_and_injections(ctx: dict, trial:Path):
    for name in REQUIRED_TRIAL_FILES:
        if not (trial/name).is_file():
            raise FileNotFoundError(trial/name)
    applied=v27.read_json(trial/'controles_aplicados.json')
    policy=ctx['policy']
    if applied.get('policy') != policy['name']:
        raise RuntimeError(f'Politica AC divergente em {trial.name}')
    for kind,offset_key in (('regulators','reg_vreg_offset_v'),
                            ('capcontrols','cap_onoff_offset_v')):
        expected=policy[offset_key]
        recorded=applied.get(kind,{})
        if set(expected) != set(recorded):
            raise RuntimeError(f'Controles diferentes da politica em {trial.name}/{kind}')
        for name,delta in expected.items():
            data=recorded[name]
            if kind=='regulators':
                ok=(abs(float(data['new_v'])-float(data['original_v'])-delta)<1e-5
                    and abs(float(data['delta_v'])-delta)<1e-5)
            else:
                ok=(abs(float(data['new_on_v'])-float(data['original_on_v'])-delta)<1e-5
                    and abs(float(data['new_off_v'])-float(data['original_off_v'])-delta)<1e-5)
            if not ok:raise RuntimeError(f'Delta de controle inconsistente {trial.name}/{name}')
    v27.assert_dispatch_identity(trial,ctx['state'])
    b=ctx['v24'].read_bess(trial,ctx['state'])
    c=ctx['v24'].read_cars(trial,ctx['state'])
    ctx['v24'].validate_resources(ctx['state']['team'],b,c,
                                   ctx['state']['places'],ctx['state']['cap'])
    expected=ctx['state']['build_injections'](
        ctx['state']['s1'],ctx['state']['team']['old_fleet_profile'],
        b,c,ctx['state']['places'])
    recorded=pd.read_csv(trial/'injections_reoptimized.csv')
    keys=['hour','bus','phase']
    if not {'p_kw','q_kvar'}.issubset(recorded.columns):
        raise ValueError('Tabela de injecoes sem potencia ativa ou reativa')
    if recorded.duplicated(keys).any() or expected.duplicated(keys).any():
        raise ValueError('Injecoes com chaves duplicadas')
    assert_frame_equal(expected.sort_values(keys).reset_index(drop=True),
                       recorded.sort_values(keys).reset_index(drop=True),
                       check_dtype=False,rtol=0,atol=1e-5,
                       obj=f'injecoes {trial.name}')


def parse_saved_manifest(previous: Path, cli:argparse.Namespace) -> argparse.Namespace:
    manifest=v27.read_json(previous/'run_manifest_v281.json')
    if manifest.get('task') != 'run':
        raise ValueError('Manifest precisa corresponder a uma V2.8.1 executada')
    args=argparse.Namespace(**manifest)
    for k in ('repo','baseline','v22_dir','v23_dir','v24_dir',
              'v25_dir','v26_dir','v27_dir','v28_dir'):
        setattr(args,k,getattr(cli,k))
    args.output=str(cli.output)
    # Mantem todos os thresholds da execucao original, corrigindo SOMENTE a
    # referencia do budget historico; preserva 0.03 no gate incremental.
    args.voltage_budget_puh=float(cli.incremental_voltage_budget_puh)
    args.historical_voltage_budget_puh=float(cli.historical_voltage_budget_puh)
    return args,manifest


def check_candidate(ctx:dict,trial:Path,saved:dict,args:argparse.Namespace):
    check_resources_and_injections(ctx,trial)
    ac=ctx['v22'].read_ac_folder(trial/'AC')
    metrics=v28.qmetrics(ctx,ac)
    for side in ('orig','policy'):
        v27.score_close(metrics[side],saved['metrics'][side],
                        f'candidate {trial.name}/{side}',atol=1e-4)
    if abs(metrics['focus']-saved['metrics']['focus'])>1e-5:
        raise RuntimeError(f'Foco {trial.name} inconsistente')
    for side in ('orig','policy'):
        for key in ('vmin_pu','max_loading_pct','thermal_aggravation_samples',
                    'voltage_aggravation_samples','strict_thermal_pp_h',
                    'strict_voltage_pu_h'):
            if abs(float(metrics[side][key])-float(saved['metrics'][side][key]))>1e-4:
                raise RuntimeError(f'Metrica AC inconsistente {trial.name}/{side}/{key}')
    if metrics['ops']!=saved['metrics']['ops']:
        raise RuntimeError(f'Operacoes autom. {trial.name} inconsistentes')
    if saved.get('status')!='AC_REJECTED':
        raise RuntimeError('Reavaliacao exige candidato originalmente rejeitado')
    okay, reasons, detail=v281.dual_voltage_budget_gate(ctx,ac,metrics,ctx['start'],args)
    return okay,reasons,detail,metrics


def run_audit(cli):
    origin=Path(cli.v281_dir).resolve();out=Path(cli.output).resolve()
    if not origin.is_dir():raise FileNotFoundError(origin)
    paths=[origin,Path(cli.baseline),Path(cli.v22_dir),Path(cli.v23_dir),
           Path(cli.v24_dir),Path(cli.v25_dir),Path(cli.v26_dir),Path(cli.v27_dir),
           Path(cli.v28_dir),Path(cli.repo)/'distributed_stochastic']
    require_distinct_output(out,paths)
    args,manifest=parse_saved_manifest(origin,cli)
    ctx=v281.prepare(args)
    old=check_archive_identity(origin,ctx,manifest)
    folders=sorted(origin.glob('iter_*/trial_bt*'))
    folders=[p for p in folders if (p/'report_v281.json').is_file()]
    if not folders:raise FileNotFoundError('Nenhum trial V2.8.1 com relatorio arquivado')
    rows=[];diagnostics=[]
    for trial in folders:
        saved=v27.read_json(trial/'report_v281.json')
        if not (trial/'AC'/'branches.csv').is_file():
            raise FileNotFoundError(f'Candidato sem simulacao AC: {trial}')
        ok,reasons,audit,metrics=check_candidate(ctx,trial,saved,args)
        detail={
            'candidate':trial.name,'iteration':int(saved['iteration']),
            'backtrack':int(saved['backtrack']),
            'source_folder':str(trial),
            'original_status':saved['status'],
            'original_reasons':saved.get('reasons',[]),
            'corrected_status':'AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
            'corrected_reasons':reasons,
            'budget_audit':audit,
            'metrics':metrics,
            'original_report_preserved':True,
            'replay_source':'ALREADY_COMPUTED_OPEN_DSS_AC; NOT_A_NEW_SIMULATION',
            'absolute_certification':False,
        }
        diagnostics.append(detail)
        rows.append({'candidate':trial.name,'accepted':ok,'reasons':';'.join(reasons),
                     'thermal_pph':metrics['policy']['thermal_aggravation_pp_h'],
                     'voltage_policy_puh':metrics['policy']['voltage_aggravation_pu_h'],
                     'voltage_orig_puh':metrics['orig']['voltage_aggravation_pu_h'],
                     'focus_pph':metrics['focus'],'strict_voltage_samples':metrics['orig']['strict_voltage_samples'],
                     'strict_overload_samples':metrics['orig']['strict_overload_samples'],
                     'source_folder':str(trial)})
        print(f'[V2.8.1-corrigida] {trial.name}: T={rows[-1]["thermal_pph"]:.3f}, '
              f'V={rows[-1]["voltage_policy_puh"]:.5f}, aceito={ok}, motivo={reasons}',flush=True)
    if cli.task == 'precheck':
        print('[V2.8.1-corrigida] Precheck concluido; nenhum arquivo foi modificado.',flush=True)
        return diagnostics
    # Somente depois de auditar TODOS os candidatos, publicar nova pasta.
    out.mkdir(parents=True,exist_ok=False)
    for d in diagnostics:
        dst=out/f'iter_{d["iteration"]:02d}'/f'trial_bt{d["backtrack"]}'
        v27.write_json(dst/'report_v281_corrigida.json',d)
    accepted=[x for x in diagnostics if x['corrected_status']=='AC_ELIGIBLE_NOT_CERTIFIED']
    accepted.sort(key=lambda r:(r['metrics']['policy']['thermal_aggravation_pp_h'],
                 r['metrics']['policy']['voltage_aggravation_pu_h'],r['candidate']))
    if accepted:
        best_candidate=accepted[0]
        src=Path(best_candidate['source_folder'])
        champion=f'V2.8.1_CORRIGIDA_{best_candidate["candidate"]}'
    else:
        best_candidate=None
        src=Path(cli.v27_dir).resolve()/'best'
        champion='V2.7_PRESERVED'
    shutil.copytree(src,out/'best')
    if best_candidate:
        orig_report=out/'best'/'report_v281.json'
        if orig_report.is_file():
            orig_report.rename(out/'best'/'report_v281_original.json')
        v27.write_json(out/'best'/'report_v281.json',best_candidate)
        v27.write_json(out/'best'/'report_v281_corrigida.json',best_candidate)
        v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
        ac_best=ctx['v22'].read_ac_folder(src/'AC')
        v27.guard_values(ctx['guard_table'],ac_best['branches']).to_csv(
            out/'best'/'guardas_v27_ac.csv',index=False)
        v27.write_json(out/'best'/'ac_score_vs_original_s0.json',best_candidate['metrics']['orig'])
        v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',best_candidate['metrics']['policy'])
    v27.write_json(out/'best'/'provenance_v281_corrigida.json',{
        'champion':champion,'source':str(src),'used_archived_AC':True,
        'new_OpenDSS_replay':False,'eligibility_only':True,'absolute_certification':False,
        'historical_budget_v25_puh':cli.historical_voltage_budget_puh,
        'incremental_budget_v27_puh':cli.incremental_voltage_budget_puh})
    pd.DataFrame(rows).to_csv(out/'todos_candidatos_ac_v281_corrigida.csv',index=False)
    final={
        'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else 'V27_PRESERVED_NO_ACCEPTED_V281_CORRIGIDA',
        'champion':champion,'accepted_candidates':len(accepted),'evaluated_candidates':len(diagnostics),
        'best_metrics':best_candidate['metrics'] if best_candidate else ctx['start_metrics'],
        'reference_v27':ctx['start_metrics'],
        'v25_original_s0_voltage_puh':ctx['ref_orig']['voltage_aggravation_pu_h'],
        'v25_policy_s0_voltage_puh':ctx['ref_policy']['voltage_aggravation_pu_h'],
        'historical_budget_v25_puh':cli.historical_voltage_budget_puh,
        'incremental_budget_v27_puh':cli.incremental_voltage_budget_puh,
        'source_original_v281':str(origin),'source_final_state':old['state'],
        'no_new_opendss_simulation':True,'absolute_certification':False,
        'scope':'AC replay 24h previously computed; all scientific gates rescreened',
        'limitations':['Operacao absoluta ainda viola limites no S0/rede',
                       'Transformadores, protecoes, N-1 e robustez estocastica nao certificados'],
    }
    v27.write_json(out/'final_report_v281_corrigida.json',final)
    print(f'[V2.8.1-corrigida] Final: {final["state"]}; '
          f'aceitos={len(accepted)}; campeao={champion}',flush=True)
    print(f'[V2.8.1-corrigida] Relatorio: {out/"final_report_v281_corrigida.json"}',flush=True)
    return diagnostics


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('baseline','v22-dir','v23-dir','v24-dir','v25-dir',
              'v26-dir','v27-dir','v28-dir','v281-dir'):
        p.add_argument('--'+k,required=True)
    p.add_argument('--repo',default='.')
    p.add_argument('--output',default='./results/v281_aceitacao_corrigida')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--historical-voltage-budget-puh',type=float,default=.15)
    p.add_argument('--incremental-voltage-budget-puh',type=float,default=.03)
    return p


def main(argv=None):
    p=make_parser();args=p.parse_args(argv)
    if not (0 <= args.incremental_voltage_budget_puh <= .03 + 1e-12 and
            args.incremental_voltage_budget_puh <= args.historical_voltage_budget_puh <= .15 + 1e-12 and
            all(np.isfinite(v) for v in (args.incremental_voltage_budget_puh,
                    args.historical_voltage_budget_puh))):
        p.error('A reauditoria nao pode relaxar limites: historico <= 0.15, incremental <= 0.03')
    try:
        run_audit(args)
        return 0
    except Exception as exc:
        print(f'[V2.8.1-corrigida] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__=='__main__':
    raise SystemExit(main())
