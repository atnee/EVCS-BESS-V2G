#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IEEE 8500: preservacao de fases fisicas no OpenDSS + validacao pareada S0.

Executar na pasta EVCS-BESS-V2G-Distributed-Stochastic:
  python validar_fases_s0_ae.py --repo '..\\EVCS-BESS-V2G' --horas 3 18 --completo

A 'correcao' aqui e utilizar um backend OpenDSS com linhas fisicamente monofasicas,
 bifasicas e trifasicas; NAO modifica o backend pandapower legado, nem prova
 equivalencia eletrica. O pandapower continua sendo a REFERENCIA HISTORICA para
 quantificar o erro. Os resultados originais S0-S4 NAO sao sobrescritos.

Reutiliza a API do script existente validar_s0_pareado_v7.py, localizado na
mesma pasta. Compara em condicoes S0, taps fixos, sem EVCS/BESS/V2G e sem
controladores automaticos. A reducao MT no DSS mantem secundarios energizados
sem cargas (ainda NAO e uma reducao exata).
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import traceback
from datetime import datetime, timezone
from collections import Counter, defaultdict

PHNUM = {'A':1,'B':2,'C':3}
NUMPH = {v:k for k,v in PHNUM.items()}
PHASE_COLUMNS = ('id','from_bus','to_bus','expected_phases','dss_phases_t1',
                 'dss_phases_t2','expected_num_phases','dss_num_phases',
                 'dss_num_conductors','dss_from_bus','dss_to_bus','status','reason')
COMPARE_COLUMNS = ('hour','load_mult','mode','taps','capacitors','topology_status',
 'pp_vmin_pu','dss_vmin_pu','v_mae_pu','v_rmse_pu','v_max_err_pu','voltage_pairs',
 'current_pairs','pp_overloaded_lines','dss_overloaded_lines',
 'new_dss_overloads_vs_pp','missing_dss_overloads_vs_pp',
 'pp_source_kw','dss_source_kw','source_power_diff_pct',
 'pp_losses_kw','dss_losses_kw','loss_diff_pct',
 'v_target_met','thermal_sets_match','power_target_met','loss_target_met',
 'model_equivalent_for_s0')


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):
            h.update(chunk)
    return h.hexdigest()


def json_write(path, obj):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False),encoding='utf-8')


def csv_write(path, rows, columns):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(columns),extrasaction='ignore')
        w.writeheader();w.writerows(rows)


def output_path(repo, path):
    repo=repo.resolve()
    out=(Path(path) if Path(path).is_absolute() else repo/path).resolve()
    base=(repo/'results').resolve()
    if out==base or not out.is_relative_to(base) or out.is_symlink():
        raise ValueError('A saida precisa ficar numa SUBPASTA nova de --repo/results, sem sobrescrever originais')
    if out.exists():
        raise FileExistsError('A pasta de saida ja existe. Escolha outro --saida: '+str(out))
    return out


def phase_count_inventory(data):
    hist=Counter(len(l['phases']) for l in data['lines'])
    if any(n not in (1,2,3) for n in hist):
        raise ValueError('Linha com numero de fases nao suportado')
    physical=sum(len(l['phases']) for l in data['lines'])
    return {'lines':len(data['lines']), 'single_phase':hist[1],
      'two_phase':hist[2], 'three_phase':hist[3],
      'physical_phase_line_connections':physical,
      'legacy_three_phase_slots':3*len(data['lines']),
      'nonphysical_slots_in_legacy_3ph_approximation':3*len(data['lines'])-physical,
      'warning':'Contagens de slots, NAO equivalem a numero de ramos fisicos eletricamente demonstrados.'}


def pair_clusters(data):
    groups=defaultdict(list)
    for l in data['lines']:
        if len(l['phases'])==1:
            groups[tuple(sorted((l['from_bus'].lower(),l['to_bus'].lower())))].append(l)
    rows=[]
    for (a,b),lines in groups.items():
        if len(lines)>1:
            rows.append({'from_bus':a,'to_bus':b,'n_lines':len(lines),
              'phase_set':''.join(sorted({ph for l in lines for ph in l['phases']})),
              'elements':';'.join(sorted(l['id'] for l in lines))})
    return sorted(rows,key=lambda x:(x['from_bus'],x['to_bus']))


def as_set_from_order(order):
    return {NUMPH[int(n)] for n in order if int(n) in NUMPH}


def inspect_phase_topology(dss, data):
    """Audita o circuito OpenDSS JA compilado e solucionado, sem alterar estados."""
    known={str(x).lower() for x in dss.Lines.AllNames()}
    rows=[]
    for l in data['lines']:
        name=l['id']; expected=set(l['phases'])
        row={'id':name,'from_bus':l['from_bus'],'to_bus':l['to_bus'],
             'expected_phases':''.join(sorted(expected)),
             'expected_num_phases':len(expected),'status':'FAIL','reason':''}
        if name.lower() not in known or dss.Circuit.SetActiveElement('Line.'+name)<=0:
            row['reason']='LINE_NOT_FOUND';rows.append(row);continue
        nc=int(dss.CktElement.NumConductors());np_=int(dss.CktElement.NumPhases())
        order=list(dss.CktElement.NodeOrder())
        # Um terminal contem nc condutores (incluindo eventual neutro). Ignore no 0.
        p1=as_set_from_order(order[:nc]);p2=as_set_from_order(order[nc:2*nc])
        bus_names=list(dss.CktElement.BusNames())
        bus1=bus_names[0].split('.')[0].lower() if len(bus_names)>0 else ''
        bus2=bus_names[1].split('.')[0].lower() if len(bus_names)>1 else ''
        row.update(dss_phases_t1=''.join(sorted(p1)),dss_phases_t2=''.join(sorted(p2)),
                   dss_num_phases=np_,dss_num_conductors=nc,dss_from_bus=bus1,dss_to_bus=bus2)
        faults=[]
        if np_!=len(expected): faults.append('NUM_PHASES')
        if p1!=expected:faults.append('TERMINAL_1_PHASES')
        if p2!=expected:faults.append('TERMINAL_2_PHASES')
        if len(order)<2*nc:faults.append('NODE_ORDER_TOO_SHORT')
        if bus1!=l['from_bus'].lower() or bus2!=l['to_bus'].lower():
            faults.append('BUS_ENDPOINT_MISMATCH')
        row['status']='PASS' if not faults else 'FAIL'
        row['reason']=';'.join(faults)
        rows.append(row)
    failures=[x for x in rows if x['status']!='PASS']
    return rows,{'status':'PASS' if not failures else 'FAIL','lines_checked':len(rows),
                  'errors':len(failures),'examples':failures[:25],
                  'meaning':'Checagem de conectividade fisica das fases das linhas MT; nao compara fidelidade do fluxo eletrico.'}


def overload_lines(result):
    return {line for (line,phase),v in result['lines'].items() if v['loading_pct']>100.0}


def rel_pct(a,b):
    return None if a is None or b is None or not math.isfinite(b) or abs(b)<1e-12 else 100*(a-b)/abs(b)


def ratio_rows(data, pp_res, dss_res, hour, mode):
    rows=[]
    for r in data['regulators']:
        for ph in r['phases']:
            start=(r['from_bus'].lower(),ph);end=(r['to_bus'].lower(),ph)
            vals=[]
            for model,res in [('pandapower',pp_res),('opendss',dss_res)]:
                a=res['voltages'].get(start);b=res['voltages'].get(end)
                vals.append(b/a if a is not None and b is not None and a>0 else None)
            rows.append(dict(hour=hour,mode=mode,regulator=r['id'],phase=ph,
                 nominal_tap=sum(r['taps'])/len(r['taps']),
                 pp_vout_over_vin=vals[0],dss_vout_over_vin=vals[1],
                 abs_diff=None if None in vals else abs(vals[0]-vals[1])))
    return rows


def parallel_rows(data,pp_res,dss_res,hour,mode):
    byname={l['id']:l for l in data['lines']}
    rows=[]
    for cluster in pair_clusters(data):
        for name in cluster['elements'].split(';'):
            ph=byname[name]['phases']
            p=pp_res['lines'].get((name.lower(),ph));d=dss_res['lines'].get((name.lower(),ph))
            a=p['current_a'] if p is not None else None
            b=d['current_a'] if d is not None else None
            rows.append({'hour':hour,'mode':mode,'cluster':cluster['from_bus']+'|'+cluster['to_bus'],
              'element':name,'phase':ph,'pp_current_a':a,'dss_current_a':b,
              'dss_over_pp_ratio':None if a is None or b is None or a==0 else b/a})
    return rows


def validate_scores(hour,mode,pp_res,dss_res,comp,topsum, voltage_rmse_target=0.005,
                    power_target_pct=1.0,loss_target_pct=5.0):
    a=pp_res['metrics'];b=dss_res['metrics'];o1=overload_lines(pp_res);o2=overload_lines(dss_res)
    source_diff=rel_pct(b['p_subestacao_kw'],a['p_subestacao_kw'])
    loss_diff=rel_pct(b['perdas_kw'],a['perdas_kw'])
    rmse=comp['tensao_erro_pu']['rmse']
    voltage_ok=rmse is not None and rmse<=voltage_rmse_target and comp['fases_ausentes_dss']==0
    thermal_ok=o1==o2
    power_ok=source_diff is not None and abs(source_diff)<=power_target_pct
    loss_ok=loss_diff is not None and abs(loss_diff)<=loss_target_pct
    return {'hour':hour,'load_mult':None,'mode':mode,'taps':None,'capacitors':None,
       'topology_status':topsum['status'],'pp_vmin_pu':a['vmin_pu'],'dss_vmin_pu':b['vmin_pu'],
       'v_mae_pu':comp['tensao_erro_pu']['mae'],'v_rmse_pu':rmse,
       'v_max_err_pu':comp['tensao_erro_pu']['max_erro_absoluto'],
       'voltage_pairs':comp['fases_comparadas'],'current_pairs':comp['correntes_comparadas'],
       'pp_overloaded_lines':len(o1),'dss_overloaded_lines':len(o2),
       'new_dss_overloads_vs_pp':len(o2-o1),'missing_dss_overloads_vs_pp':len(o1-o2),
       'pp_source_kw':a['p_subestacao_kw'],'dss_source_kw':b['p_subestacao_kw'],
       'source_power_diff_pct':source_diff,'pp_losses_kw':a['perdas_kw'],
       'dss_losses_kw':b['perdas_kw'],'loss_diff_pct':loss_diff,
       'v_target_met':voltage_ok,'thermal_sets_match':thermal_ok,
       'power_target_met':power_ok,'loss_target_met':loss_ok,
       'model_equivalent_for_s0':topsum['status']=='PASS' and all((voltage_ok,thermal_ok,power_ok,loss_ok))}


def verify_source_provenance(repo, data):
    expected=data.get('provenance',{}).get('sha256',{})
    if not expected:
        raise RuntimeError('O JSON nao possui manifesto SHA-256 das fontes IEEE 8500; nao prosseguir sem rastreabilidade')
    directory=repo/'data/ieee8500/source'
    failed=[]
    for name, digest in expected.items():
        file=directory/name
        if not file.is_file():
            failed.append({'source':name,'reason':'ABSENT'})
            continue
        # A normalizacao original utiliza bytes com LF, sem alterar os arquivos.
        current=hashlib.sha256(file.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        if current!=digest:
            failed.append({'source':name,'reason':'HASH_MISMATCH','expected':digest,'got':current})
    if failed:
        raise RuntimeError('Fontes DSS nao coincidem com a proveniencia do JSON: '+str(failed[:8]))
    return {'files_checked':len(expected),'status':'PASS'}


def file_fingerprints(repo,driver):
    rel=['src/network/data/ieee8500.json','configs/ieee8500.yaml','data/ieee8500/source/Master.dss',
         'src/network/feeder_loader.py','src/network/pandapower_solver.py']
    paths=[repo/x for x in rel]+[driver,Path(__file__)]
    return {str(x.resolve()):sha256(x) for x in paths}


def preflight(repo,driver):
    required=[repo/'src/network/data/ieee8500.json',repo/'configs/ieee8500.yaml',
      repo/'data/ieee8500/source/Master.dss',repo/'src/network/feeder_loader.py',
      repo/'src/network/pandapower_solver.py',driver]
    missing=[str(x) for x in required if not x.is_file()]
    if missing:raise FileNotFoundError('Arquivos obrigatorios ausentes: '+str(missing))
    try:
        import yaml
        import opendssdirect
        import pandapower
    except ImportError as e:
        raise RuntimeError('Dependencias ausentes no ambiente Python. Instale opendssdirect.py e requisitos do projeto') from e
    return yaml


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path('.'),required=False,help='Caminho EVCS-BESS-V2G (pasta do JSON e dos DSS)')
    p.add_argument('--saida',type=Path,default=Path('results/validacao_fases_s0_ae'))
    p.add_argument('--horas',nargs='+',type=int,default=[3,18])
    p.add_argument('--capacitores',choices=('ligados','desligados'),default='ligados')
    p.add_argument('--taps',choices=('referencia','neutros'),default='referencia')
    p.add_argument('--completo',action='store_true',help='Tambem resolve o IEEE 8500 completo, com secundarios')
    p.add_argument('--rmse-alvo-pu',type=float,default=0.005)
    p.add_argument('--self-test',action='store_true',help='Testes unitarios sem OpenDSS/pandapower')
    args=p.parse_args(argv)
    if args.self_test:
        self_test();return 0
    if any(h not in range(24) for h in args.horas) or len(set(args.horas))!=len(args.horas):
        p.error('--horas deve conter horas 0..23 sem repeticoes')
    if not 0<args.rmse_alvo_pu<1:
        p.error('--rmse-alvo-pu invalido')
    repo=args.repo.expanduser().resolve()
    driver=Path(__file__).resolve().with_name('validar_s0_pareado_v7.py')
    out=output_path(repo,args.saida)
    yaml=preflight(repo,driver)
    # A execucao usa funções existentes; nenhum arquivo original e editado.
    sys.path.insert(0,str(driver.parent))
    import validar_s0_pareado_v7 as old
    from opendssdirect import dss
    dataset=repo/'src/network/data/ieee8500.json'
    data=json.loads(dataset.read_text(encoding='utf-8'))
    provenance_result=verify_source_provenance(repo,data)
    conf=yaml.safe_load((repo/'configs/ieee8500.yaml').read_text(encoding='utf-8'))
    multiples=conf['load_multipliers']
    if len(multiples)!=24 or not all(math.isfinite(float(x)) and float(x)>=0 for x in multiples):
        raise ValueError('configs/ieee8500.yaml deve ter 24 multiplicadores de demanda nao negativos')
    inventory=phase_count_inventory(data); clusters=pair_clusters(data)
    originals=file_fingerprints(repo,driver)
    out.mkdir(parents=True,exist_ok=False)
    csv_write(out/'clusters_paralelos.csv',clusters,['from_bus','to_bus','n_lines','phase_set','elements'])
    summary={'version':'AE-PHASE-AUDIT-1.0','created_utc':datetime.now(timezone.utc).isoformat(),
      'status':'RUNNING','original_files_sha256':originals,'repo':str(repo),'output':str(out),
      'network_inventory':inventory,'source_provenance_validation':provenance_result,'triple_single_phase_pairs':len(clusters),
      'study_conditions':{'hours':args.horas,'load_multipliers':{str(i):float(multiples[i]) for i in args.horas},
           'capacitors':args.capacitores,'tap_mode':args.taps,'line_matrices':'ORIGINAL DSS',
           'HV_transformer':'delta, original','controls':'OFF, taps fixed',
           'load_equivalent':'secondary loads off, equivalent primaries in DSS (secondary branches retained)'} ,
      'evaluation_targets_user_defined_not_IEEE':{'v_rmse_pu':args.rmse_alvo_pu,'source_diff_pct':1.0,'loss_diff_pct':5.0,
                         'overloaded_line_set':'identical'},'rows':[], 'errors':[],
      'important_limitations':['OpenDSS MT preserves native phases; pandapower LEGACY IS NOT MODIFIED.',
        'MT equivalence preserves unloaded secondary circuits; not an exact mathematical Kron reduction.',
        'Fixed taps and controls off; NOT the 24-h automatically regulated S0-S4 of the manuscript.',
        'One day at selected hours does not establish compliance or full benchmark equivalence.']}
    json_write(out/'relatorio_validacao_s0.json',summary)
    saved_cwd=Path.cwd()
    phases_all=[];reg_all=[];parallel_all=[];thermal_all=[]
    caps=(args.capacitores=='ligados')
    try:
        for hour in args.horas:
            mult=float(multiples[hour]);tag=f's0_h{hour:02d}'
            print(f'[AE] {tag}: pandapower legado, demanda {mult:.3f}, taps {args.taps}, capacitores {args.capacitores}',flush=True)
            pp_result=old.run_pandapower(repo,data,mult,args.taps,caps)
            old.save_model_results(pp_result,out,tag+'_pandapower')
            for mode in (['mt_equivalente','completo'] if args.completo else ['mt_equivalente']):
                print(f'[AE] {tag}: OpenDSS {mode} preservando fases fisicas',flush=True)
                try:
                    ds_result=old.run_opendss(repo,data,mult,mode,args.taps,caps,
                       0.50,1.50,'originais','delta')
                    phase_rows,topology=inspect_phase_topology(dss,data)
                    for r in phase_rows:r.update(hour=hour,mode=mode)
                    phases_all.extend(phase_rows)
                    if topology['status']!='PASS':
                        raise RuntimeError(f'As conexoes fisicas por fase nao coincidem com o arquivo normalizado: {topology["examples"][:3]}')
                    old.save_model_results(ds_result,out,tag+'_opendss_'+mode)
                    c=old.compare(pp_result,ds_result,out,tag+'_'+mode)
                    old.diagnostico_linhas(repo,data,out,hour,mode)
                    result=validate_scores(hour,mode,pp_result,ds_result,c,topology,args.rmse_alvo_pu)
                    result.update(load_mult=mult,taps=args.taps,capacitors=args.capacitores)
                    summary['rows'].append(result)
                    reg_all.extend(ratio_rows(data,pp_result,ds_result,hour,mode))
                    parallel_all.extend(parallel_rows(data,pp_result,ds_result,hour,mode))
                    overload_pp=overload_lines(pp_result);overload_ds=overload_lines(ds_result)
                    for name in sorted(overload_pp ^ overload_ds):
                        thermal_all.append({'hour':hour,'mode':mode,'line':name,
                           'pp_overloaded':name in overload_pp,'dss_overloaded':name in overload_ds})
                    print('[AE] MAE {:.6f} pu | RMSE {:.6f} pu | sobrecargas {}/{} | equivalencia={}'.format(
                        result['v_mae_pu'],result['v_rmse_pu'],len(overload_pp),len(overload_ds),
                        result['model_equivalent_for_s0']),flush=True)
                except Exception as ex:
                    error={'hour':hour,'mode':mode,'error_type':type(ex).__name__,'reason':str(ex),
                           'traceback':traceback.format_exc()}
                    summary['errors'].append(error)
                    json_write(out/f'{tag}_{mode}_ERRO.json',error)
                    print(f'[AE] ERRO {tag}/{mode}: {ex}',file=sys.stderr,flush=True)
                finally:
                    # O Compile do OpenDSS pode mudar o diretorio do processo Python.
                    os.chdir(saved_cwd)
                json_write(out/'relatorio_validacao_s0.json',summary)
    except Exception as ex:
        summary['errors'].append({'error_type':type(ex).__name__,'reason':str(ex),
                                   'traceback':traceback.format_exc()})
        print(f'[AE] ERRO: {ex}',file=sys.stderr)
    finally:
        os.chdir(saved_cwd)
        csv_write(out/'inventario_fases_opendss.csv',phases_all,[*PHASE_COLUMNS,'hour','mode'])
        csv_write(out/'comparacao_s0.csv',summary['rows'],COMPARE_COLUMNS)
        csv_write(out/'comparacao_reguladores.csv',reg_all,
            ['hour','mode','regulator','phase','nominal_tap','pp_vout_over_vin','dss_vout_over_vin','abs_diff'])
        csv_write(out/'correntes_trechos_paralelos.csv',parallel_all,
            ['hour','mode','cluster','element','phase','pp_current_a','dss_current_a','dss_over_pp_ratio'])
        csv_write(out/'linhas_sobrecarga_divergente.csv',thermal_all,
            ['hour','mode','line','pp_overloaded','dss_overloaded'])
        now=file_fingerprints(repo,driver)
        summary['input_hashes_preserved']=now==originals
        summary['status']='COMPLETE' if (not summary['errors'] and len(summary['rows'])==len(args.horas)*(2 if args.completo else 1) and now==originals) else 'INCOMPLETE'
        summary['equivalence_demonstrated']=summary['status']=='COMPLETE' and all(r['model_equivalent_for_s0'] for r in summary['rows'])
        json_write(out/'relatorio_validacao_s0.json',summary)
        print(f'[AE] Final: {summary["status"]}; equivalencia_demonstrada={summary["equivalence_demonstrated"]}; output={out}',flush=True)
    return 0 if summary['status']=='COMPLETE' else 2


def self_test():
    class Elem:
        def __init__(self): self.name=''
        def NumConductors(self):return 2 if self.name=='a' else 3
        def NumPhases(self):return 1 if self.name=='a' else 3
        def NodeOrder(self):return [1,0,1,0] if self.name=='a' else [1,2,3,1,2,3]
        def BusNames(self):return ['x.1','y.1'] if self.name=='a' else ['y.1.2.3','z.1.2.3']
    class Lines:
        def AllNames(self):return ['a','b']
    class Circuit:
        def __init__(self,elem):self.elem=elem
        def SetActiveElement(self,name):self.elem.name=name.split('.')[1];return 1
    class DSS:
        def __init__(self):self.CktElement=Elem();self.Lines=Lines();self.Circuit=Circuit(self.CktElement)
    sample={'lines':[{'id':'a','from_bus':'x','to_bus':'y','phases':'A'},
                     {'id':'b','from_bus':'y','to_bus':'z','phases':'ABC'}]}
    rows,status=inspect_phase_topology(DSS(),sample)
    assert status['status']=='PASS' and status['lines_checked']==2
    assert phase_count_inventory(sample)['nonphysical_slots_in_legacy_3ph_approximation']==2
    sample['lines'][0]['phases']='B'
    assert inspect_phase_topology(DSS(),sample)[1]['status']=='FAIL'
    pp={'lines':{('x','A'):{'loading_pct':50}},'metrics':{'vmin_pu':0.97,'p_subestacao_kw':100,'perdas_kw':10}}
    ds={'lines':{('x','A'):{'loading_pct':101}},'metrics':{'vmin_pu':0.96,'p_subestacao_kw':101,'perdas_kw':11}}
    comp={'tensao_erro_pu':{'rmse':0.006,'mae':0.005,'max_erro_absoluto':0.01},
          'fases_ausentes_dss':0,'fases_comparadas':2,'correntes_comparadas':1}
    x=validate_scores(18,'mt_equivalente',pp,ds,comp,{'status':'PASS'})
    assert x['model_equivalent_for_s0'] is False and x['new_dss_overloads_vs_pp']==1
    print('[AE] Self-test OK: phases, relatorio, metricas e fail-closed')


if __name__=='__main__':
    raise SystemExit(main())
