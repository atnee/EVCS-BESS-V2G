#!/usr/bin/env python3
"""Audita saídas OpenDSS S0/S1 já calculadas; NÃO executa novo fluxo de potência.

Uso na raiz EVCS-BESS-V2G:
 python opendss_rebuild/audit_s0_s1.py --results results/s0_s1_opendss_24h
"""
from __future__ import annotations
import argparse
from collections import deque
import json
from pathlib import Path
import pandas as pd
import numpy as np

REQUIRED={'source':['hour','p_kw','loss_kw'],
          'branches':['hour','line','phase','current_a','ampacity_a','loading_pct'],
          'voltages':['hour','bus','phase','v_pu'],
          'injections_replayed':['hour','bus','phase','p_kw']}


def read_tables(base:Path, scenario:str)->dict:
    out={}
    for name,cols in REQUIRED.items():
        file=base/scenario/(name+'.csv')
        if not file.is_file():raise FileNotFoundError(f'Arquivo obrigatório não encontrado: {file}')
        df=pd.read_csv(file)
        if not set(cols)<=set(df.columns):raise ValueError(f'Colunas faltantes em {file}: {set(cols)-set(df.columns)}')
        if name in ('branches','voltages','injections_replayed'):
            key=['hour','line','phase'] if name=='branches' else (['hour','bus','phase'])
            if df.duplicated(key).any():raise ValueError(f'Identidades duplicadas em {file}: {key}')
        out[name]=df
    return out


def read_network_paths(base:Path,repo:Path,new_lines:set[str])->dict:
    path=repo/'src/network/data/ieee8500.json'
    if not path.is_file():return {'status':'TOPOLOGY_NOT_CHECKED_NO_LOCAL_JSON'}
    d=json.loads(path.read_text(encoding='utf8'))
    adj={}
    def connect(a,b,name):
        adj.setdefault(str(a).lower(),[]).append((str(b).lower(),str(name).lower()))
        adj.setdefault(str(b).lower(),[]).append((str(a).lower(),str(name).lower()))
    for l in d['lines']: connect(l['from_bus'],l['to_bus'],l['id'])
    for s in d.get('switches',[]):
        if s.get('closed',False):connect(s['from_bus'],s['to_bus'],'switch:'+s['id'])
    for r in d.get('regulators',[]):connect(r['from_bus'],r['to_bus'],'regulator:'+r['id'])
    xf=d['transformer'];connect(xf['from_bus'],xf['to_bus'],'transformer:'+xf['id'])
    root=str(d['source_bus']).lower()
    def path_to(dest):
        dest=dest.lower();prev={root:None};q=deque([root]);
        while q:
            node=q.popleft()
            if node==dest:break
            for nxt,branch in adj.get(node,[]):
                if nxt not in prev:prev[nxt]=(node,branch);q.append(nxt)
        if dest not in prev:return None
        seq=[];node=dest
        while node!=root:
            node0,branch=prev[node];seq.append(branch);node=node0
        return list(reversed(seq))
    targets={'bess_l3029502':path_to('l3029502'),'v2g_m1069504':path_to('m1069504')}
    routes={key: {'reachable':val is not None,'n_hops':len(val) if val is not None else None,
                   'new_overloaded_lines_on_route':len(new_lines.intersection(val or []))} for key,val in targets.items()}
    both=set(targets['bess_l3029502'] or [])&set(targets['v2g_m1069504'] or [])
    return {'status':'GRAPH_ROUTE_ONLY_NOT_SENSITIVITY','graph_paths':routes,
            'new_overloaded_lines_on_both_paths':len(new_lines & both),
            'new_overloaded_lines_not_on_both_paths':sorted(new_lines-both),
            'preexisting_critical_line_ln6351510_1_on_paths':
                {'BESS':'ln6351510-1' in set(targets['bess_l3029502'] or []),
                 'V2G':'ln6351510-1' in set(targets['v2g_m1069504'] or [])},
            'caution':'Topologia de JSON com chaves fechadas: não substitui fluxo elétrico de fase nem comprova efeito do despacho.'}


def main():
    arg=argparse.ArgumentParser(description=__doc__)
    arg.add_argument('--results',required=True,type=Path)
    arg.add_argument('--output',type=Path,default=None)
    arg.add_argument('--repo',type=Path,default=Path(__file__).resolve().parent.parent)
    a=arg.parse_args();base=a.results.resolve()
    output=(a.output.resolve() if a.output else base/'auditoria_s0_s1')
    if not (base/'S0').is_dir() or not (base/'S1').is_dir():
        raise ValueError(f'S0 e S1 não encontrados sob {base}')
    d={s:read_tables(base,s) for s in ('S0','S1')}
    for s in ('S0','S1'):
        for table in ('source','branches','voltages'):
            hours=set(d[s][table].hour)
            if hours!=set(range(24)):
                raise ValueError(f'{s}/{table} não cobre as 24 horas: {sorted(hours)}')
    sb={s:d[s]['branches'] for s in d};sv={s:d[s]['voltages'] for s in d}
    if not np.allclose(sb['S0'].sort_values(['hour','line','phase']).ampacity_a,
                       sb['S1'].sort_values(['hour','line','phase']).ampacity_a):
        raise ValueError('Ampacidades divergentes entre S0/S1')
    amp0=sb['S0'];amp1=sb['S1']
    base=sb['S0'].loc[amp0.loading_pct>100].copy();target=sb['S1'].loc[amp1.loading_pct>100].copy()
    pairs0=set(zip(base.line,base.phase));pairs1=set(zip(target.line,target.phase))
    ids0=set(base.line);ids1=set(target.line)
    new_ids=ids1-ids0
    oldsample=set(zip(base.hour,base.line,base.phase))
    events=target.loc[[key not in oldsample for key in zip(target.hour,target.line,target.phase)]].copy()
    events=events.sort_values(['hour','line','phase'])
    # Numerically consistent source and load comparison by hour.
    src=d['S0']['source'].merge(d['S1']['source'],on='hour',suffixes=('_s0','_s1'),validate='one_to_one')
    src['evcs_and_fleet_kw']=-d['S1']['injections_replayed'].groupby('hour').p_kw.sum().reindex(src.hour).fillna(0).to_numpy()
    src['import_increment_kw']=src.p_kw_s1-src.p_kw_s0
    src['loss_increment_kw']=src.loss_kw_s1-src.loss_kw_s0
    src['energy_balance_residual_kw']=src.import_increment_kw-src.loss_increment_kw-src.evcs_and_fleet_kw
    def hour_count(which,col,threshold):
        return (which.loc[which[col]>threshold].groupby('hour').size().reindex(src.hour,fill_value=0).to_numpy())
    for label,table in (('s0',amp0),('s1',amp1)):
        src[f'overload_line_phase_{label}']=hour_count(table,'loading_pct',100)
    for label,table in (('s0',sv['S0']),('s1',sv['S1'])):
        src[f'v_min_pu_{label}']=table.groupby('hour').v_pu.min().reindex(src.hour).to_numpy()
        src[f'undervoltage_samples_{label}']=table.loc[table.v_pu<.95].groupby('hour').size().reindex(src.hour,fill_value=0).to_numpy()
        src[f'overvoltage_samples_{label}']=table.loc[table.v_pu>1.05].groupby('hour').size().reindex(src.hour,fill_value=0).to_numpy()
    src['new_overload_samples_at_hour']=events.groupby('hour').size().reindex(src.hour,fill_value=0).to_numpy()
    source0=d['S0']['source'];source1=d['S1']['source']
    meta={'source_peak_s0_kw':float(source0.p_kw.max()),'source_peak_s1_kw':float(source1.p_kw.max()),
          'source_energy_s0_kwh':float(source0.p_kw.sum()),'source_energy_s1_kwh':float(source1.p_kw.sum()),
          'loss_energy_s0_kwh':float(source0.loss_kw.sum()),'loss_energy_s1_kwh':float(source1.loss_kw.sum()),
          'vmin_s0':float(sv['S0'].v_pu.min()),'vmin_s1':float(sv['S1'].v_pu.min()),
          'overloaded_distinct_line_ids_s0':len(ids0),'overloaded_distinct_line_ids_s1':len(ids1),
          'new_distinct_line_ids_over_whole_day':len(new_ids),
          'new_distinct_line_phase_ids_over_whole_day':len(pairs1-pairs0),
          'line_ids_with_any_new_hour_phase_sample':events.line.nunique(),
          'new_hour_line_phase_overload_samples':len(events),
          'existing_line_ids_still_overloaded_in_s1':len(ids0&ids1),
          'overload_samples_s0':len(base),'overload_samples_s1':len(target),
          'evcs_plus_unidirectional_fleet_energy_kwh':float(src.evcs_and_fleet_kw.sum()),
          'incremental_source_energy_kwh':float(src.import_increment_kw.sum()),
          'incremental_loss_energy_kwh':float(src.loss_increment_kw.sum()),
          'absolute_max_hourly_energy_balance_residual_kw':float(src.energy_balance_residual_kw.abs().max())}
    # Source modules permit explicit accounting of V2G fleet charging in S1.
    m=basefile=a.results.resolve()/ 'modules/S1'
    paths={'evcs':m/'evcs/profile.csv','v2g':m/'v2g/profile.csv'}
    totals={}
    for mod,path in paths.items():
        if path.exists():
            profile=pd.read_csv(path)
            totals[mod]=float(-profile.p_kw.sum())
    meta['module_grid_demand_kwh']=totals
    if totals:
        meta['module_vs_grid_injection_residual_kwh']=float(sum(totals.values())-src.evcs_and_fleet_kw.sum())
    # Detailed new physical line IDs from the full-day S0/S1 overload sets.
    output.mkdir(parents=True,exist_ok=True)
    def line_overview(names:set[str]):
        rows=[]
        for name in sorted(names):
            pr=amp0[amp0.line==name];po=amp1[amp1.line==name];vi=po[po.loading_pct>100]
            peak=po.loc[po.loading_pct.idxmax()]
            rows.append({'line':name,'ampacity_a':float(peak.ampacity_a),
                'overloaded_phases_in_s1':''.join(sorted(vi.phase.unique())),
                'first_overloaded_hour_s1':int(vi.hour.min()),
                'peak_overload_hour_s1':int(peak.hour),
                'max_loading_s0_pct':float(pr.loading_pct.max()),
                'max_loading_s1_pct':float(peak.loading_pct),
                'worst_delta_max_pct_points':float(peak.loading_pct-pr.loc[(pr.hour==peak.hour)&(pr.phase==peak.phase),'loading_pct'].iloc[0])})
        return pd.DataFrame(rows)
    line_overview(new_ids).to_csv(output/'41_new_distinct_line_ids.csv',index=False)
    line_overview(ids0 & ids1).to_csv(output/'existing_s0_lines_in_s1.csv',index=False)
    events.to_csv(output/'new_overload_hour_phase_events.csv',index=False)
    src.to_csv(output/'hourly_comparison.csv',index=False)
    repo=a.repo.resolve()
    meta['topology']=read_network_paths(a.results.resolve(),repo,new_ids)
    (output/'metrics.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    report=f"""# Auditoria de S0 × S1 no OpenDSS — 24 horas

## Contagens sem ambiguidade

- **{len(ids0)}** IDs de linhas sobrecarregadas no S0 (ao menos uma vez durante o dia).
- **{len(ids1)}** IDs de linhas sobrecarregadas no S1.
- **{len(new_ids)}** novos IDs de linhas no S1 que *nunca* estiveram sobrecarregadas no S0.
- **{len(pairs1-pairs0)}** novos pares linha–fase no dia.
- **{events.line.nunique()}** IDs de linhas com alguma nova ocorrência por horário/fase (INCLUI linhas já sobrecarregadas em outras horas/fases no S0).
- **{len(events)}** novas ocorrências hora–linha–fase acima de 100%. A contagem global cresce de {len(base)} para {len(target)} amostras.

**AVISO DE MÉTRICA:** a versão antiga do `summary.csv` chamava o valor {events.line.nunique()} de `new_overloaded_unique_lines_vs_S0`. O rótulo mistura novas ocorrências com novas linhas físicas. A nova definição correta de linhas distintas acrescentadas é **{len(new_ids)}**.

## Energia

- Fonte S0: {meta['source_energy_s0_kwh']/1000:.3f} MWh; S1: {meta['source_energy_s1_kwh']/1000:.3f} MWh.
- Perdas S0: {meta['loss_energy_s0_kwh']/1000:.3f} MWh; S1: {meta['loss_energy_s1_kwh']/1000:.3f} MWh.
- Energia adicional absorvida pelos módulos no S1: {meta['evcs_plus_unidirectional_fleet_energy_kwh']/1000:.3f} MWh.
- Diferença líquida de importação: {meta['incremental_source_energy_kwh']/1000:.3f} MWh, das quais {meta['incremental_loss_energy_kwh']/1000:.3f} MWh são perdas adicionais.
- Fechamento de energia: maior desvio horário absoluto {meta['absolute_max_hourly_energy_balance_residual_kw']:.3f} kW (diferenças de precisão/estado de controle).
- EVCS e carregamento da frota V2G são módulos distintos: `v2g_delivered_kwh=0` no S1 NÃO significa ausência de consumo pela frota de 150 veículos.

## Limitações

- 24 snapshots a cada 1h, não perfil eletrodinâmico 15 minutos nem série medida de Honduras.
- Ampacidades da opção `study` são hipóteses de engenharia, não ratings originais do OpenDSS.
- Contadores por fase representam nós físicos MV exportados; não substituem auditoria dos secundários.
- Topologia dos caminhos (quando disponível) não demonstra, por si só, o efeito do despacho BESS–V2G.
- Otimização BESS de Kenia continua usando sensitividades calibradas no pandapower: deve ser recalibrada no OpenDSS antes de interpretar S2/S4 como soluções otimizadas.
"""
    (output/'RELATORIO.md').write_text(report,encoding='utf8')
    print(report)
    print('Arquivos:',output)

if __name__=='__main__':main()
