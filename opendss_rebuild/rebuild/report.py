"""Relatórios elétricos reproduzíveis: separar eventos horários de linhas distintas.

Definições de comparação:
- sample: (hora, linha, fase) > limite no alvo e não no cenário de referência
  na MESMA hora e fase.
- line IDs new over day: IDs de linha que violam em QUALQUER hora no alvo,
  mas NUNCA violaram na referência em todo o horizonte.
- line-phase IDs new over day: pares (linha, fase) ausentes entre violações da
  referência ao longo de todo o horizonte.
"""
from __future__ import annotations
import pandas as pd
from .contracts import energy_metrics,new_overloads_per_hour


def _identities(df:pd.DataFrame, limit:float):
    violated=df.loc[df.loading_pct>limit, ['line','phase']]
    lines=set(violated.line.astype(str))
    line_phases=set(zip(violated.line.astype(str),violated.phase.astype(str)))
    return lines,line_phases


def build_summary(results:dict,ctx:dict,profile_meta:dict,dt_h:float=1.0):
    summaries=[]
    extras={}
    for name,r in results.items():
        metrics=energy_metrics(r['source'],r['voltages'],r['branches'],dt_h,
             float(ctx['settings']['limits']['v_min_pu']),
             float(ctx['settings']['limits']['v_max_pu']),
             float(ctx['settings']['limits']['line_limit_pct']),
             float(ctx['settings']['paper']['tariff_usd_kwh']))
        metrics.update(profile_meta.get(name,{}).get('kpis',{}))
        taps=r.get('regulators',pd.DataFrame())
        if not taps.empty:
            taps=taps.sort_values(['regulator','hour'])
            metrics['tap_changes_between_snapshots_lower_bound']=float(
                taps.groupby('regulator').tap.diff().abs().fillna(0).sum())
        else:
            metrics['tap_changes_between_snapshots_lower_bound']=0.0
        metrics.update(scenario=name,backend='OpenDSS full physical IEEE 8500',
                       dispatch_provenance=profile_meta.get(name,{}).get('source','none'),
                       legacy_bess_surrogate=bool(profile_meta.get(name,{}).get('legacy_bess_surrogate',False)))
        summaries.append(metrics)
    limit=float(ctx['settings']['limits']['line_limit_pct'])
    for ref in ('S0','S1'):
        if ref not in results: continue
        ref_lines,ref_line_phases=_identities(results[ref]['branches'],limit)
        for name,r in results.items():
            if name==ref:continue
            extra=new_overloads_per_hour(r['branches'],results[ref]['branches'],limit)
            extras[f'{name}_new_vs_{ref}']=extra
            current_lines,current_line_phases=_identities(r['branches'],limit)
            for record in summaries:
                if record['scenario']!=name:continue
                record[f'new_overload_samples_vs_{ref}']=int(len(extra))
                # CORRIGIDO: esta coluna passa a significar linha física nova
                # em todo o dia, e não linhas presentes em alguma amostra nova.
                record[f'new_overloaded_unique_lines_vs_{ref}']=int(len(current_lines-ref_lines))
                record[f'new_overloaded_unique_line_phases_vs_{ref}']=int(len(current_line_phases-ref_line_phases))
                # A versão anterior reportava a coluna acima como esta métrica:
                record[f'lines_with_any_new_hour_phase_event_vs_{ref}']=int(extra.line.nunique())
                record[f'line_phases_with_any_new_hour_phase_event_vs_{ref}']=int(extra[['line','phase']].drop_duplicates().shape[0])
                record[f'persisting_overloaded_line_ids_vs_{ref}']=int(len(current_lines & ref_lines))
                record[f'no_longer_overloaded_line_ids_vs_{ref}']=int(len(ref_lines-current_lines))
    return pd.DataFrame(summaries),extras
