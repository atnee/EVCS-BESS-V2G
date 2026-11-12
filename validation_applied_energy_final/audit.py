"""Read-only provenance and numerical audit; writes only beside this script."""
from pathlib import Path
import csv
import hashlib
import json
import subprocess
from collections import Counter
import pandas as pd

OUT = Path(__file__).resolve().parent
B = OUT.parent
A = B.parent / 'EVCS-BESS-V2G'
INSTALLED = Path('C:/Program Files/OpenDSS/IEEETestCases/8500-Node')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def inventory():
    rows = []
    for label, root in [('original', A), ('stochastic', B)]:
        for folder in ['src', 'configs', 'scripts', 'opendss_rebuild', 'distributed_stochastic', 'data/ieee8500/source', 'paper', 'results']:
            for p in (root / folder).rglob('*'):
                if not p.is_file() or '__pycache__' in p.parts or p.suffix.lower() not in {'.py', '.yaml', '.json', '.dss', '.csv', '.md'}:
                    continue
                rows.append(dict(project=label, relative_path=p.relative_to(root).as_posix(), absolute_path=str(p.resolve()), bytes=p.stat().st_size, sha256=digest(p)))
        for p in root.glob('*.py'):
            rows.append(dict(project=label, relative_path=p.name, absolute_path=str(p), bytes=p.stat().st_size, sha256=digest(p)))
    pd.DataFrame(rows).to_csv(OUT / '02_inventario_modelos.csv', index=False)
    left = {r['relative_path']:r for r in rows if r['project']=='original'}
    right = {r['relative_path']:r for r in rows if r['project']=='stochastic'}
    compared = []
    for name in sorted(left.keys() | right.keys()):
        x, y = left.get(name), right.get(name)
        state = 'only_original' if y is None else 'only_stochastic' if x is None else 'identical' if x['sha256']==y['sha256'] else 'different'
        compared.append(dict(path=name, state=state, original_sha256=x['sha256'] if x else '', stochastic_sha256=y['sha256'] if y else ''))
    pd.DataFrame(compared).to_csv(OUT / 'comparacao_arquivos.csv', index=False)
    dump('inventario_resumo.json', dict(counts=dict(Counter(r['state'] for r in compared)), files=len(rows)))
    # Installed files are an independent source, not assumed to be identical.
    data = json.loads((B/'src/network/data/ieee8500.json').read_text(encoding='utf-8'))
    checks = []
    for name, expected in data['provenance']['sha256'].items():
        for label, root in [('original', A/'data/ieee8500/source'), ('stochastic', B/'data/ieee8500/source'), ('installed', INSTALLED)]:
            p = root/name
            raw = p.read_bytes() if p.is_file() else None
            checks.append(dict(source=label, path=str(p), exists=raw is not None, sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None, normalized_lf_matches=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()==expected if raw is not None else False))
    pd.DataFrame(checks).to_csv(OUT/'fontes_ieee_sha256.csv', index=False)
    dump('estrutura.json', dict(lines=len(data['lines']), phases=dict(Counter(l['phases'] for l in data['lines'])), buses=len(data['buses']), physical_nodes=sum(len(x['phases']) for x in data['buses'])))
    git = {}
    for key, args in [('branch',['branch','--show-current']),('log',['log','-15','--oneline']),('status',['status','--short'])]:
        p = subprocess.run(['git','-C',str(A),*args],capture_output=True,text=True)
        git[key] = dict(exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
    dump('git_original.json',git)


def physical_branches(df):
    if 'physical_phase' in df:
        df = df[df.physical_phase.astype(str).str.lower()=='true']
    if 'element_type' in df:
        df = df[df.element_type=='line']
    return df


def historical_metrics():
    root=A/'results/ieee8500/integration/dados'
    frames={s:physical_branches(pd.read_csv(root/f'{s}_branches.csv')) for s in ['S0','S1','S2','S3','S4']}
    base=set(frames['S0'].loc[frames['S0'].loading_pct>100,'line'])
    rows=[]
    for s, df in frames.items():
        src=pd.read_csv(root/f'{s}_source.csv')
        times=pd.to_datetime(src.time)
        dt=(times.iloc[1]-times.iloc[0]).total_seconds()/3600
        bad=df[df.loading_pct>100]
        rows.append(dict(scenario=s, peak_kw=float(src.p_kw.max()), dt_h=dt, steps=len(src), unique_overloaded_lines=bad.line.nunique(), new_lines_vs_S0=len(set(bad.line)-base), max_loading_pct=float(df.loading_pct.max()), source_file=str(root/f'{s}_source.csv')))
    pd.DataFrame(rows).to_csv(OUT/'alegacoes_recalculadas.csv',index=False)
    sens=pd.read_csv(A/'results/ieee8500/sensitivity/sensitivity.csv')
    checks=[]
    for case in sens['case'].unique():
        folder=A/'results/ieee8500'/('integration' if case=='base' else f'sensitivity/{case}')
        checks.append(dict(case=case, rows=int((sens['case']==case).sum()), summary_exists=(folder/'summary.csv').exists(), sources_complete=all((folder/f'dados/{s}_source.csv').exists() for s in ['S0','S1','S2','S3','S4']), branches_complete=all((folder/f'dados/{s}_branches.csv').exists() for s in ['S0','S1','S2','S3','S4'])))
    pd.DataFrame(checks).to_csv(OUT/'campanha_completude.csv',index=False)
    dump('campanha_resumo.json',dict(rows=len(sens),cases=sens['case'].nunique(),scenario_counts=sens.scenario.value_counts().to_dict(),status=sens.status.value_counts().to_dict(),peak_reduction_pct=100*(rows[1]['peak_kw']-rows[4]['peak_kw'])/rows[1]['peak_kw']))
    # Recompute paired errors from raw data rather than accepting the old verdict.
    paired=A/'results/validacao_fases_s0_ae'
    results=[]
    for hour in [3,18]:
        for mode in ['mt_equivalente','completo']:
            tag=f's0_h{hour:02d}_{mode}'
            v=pd.read_csv(paired/f'{tag}_comparacao_tensoes.csv')
            c=pd.read_csv(paired/f'{tag}_comparacao_correntes.csv')
            for phase, block in [('ALL',v), *list(v.groupby('phase'))]:
                delta=block.delta_pu
                currents=c if phase=='ALL' else c[c.phase==phase]
                results.append(dict(hour=hour,mode=mode,phase=phase,pairs=len(block),mae_pu=float(delta.abs().mean()),rmse_pu=float((delta.pow(2).mean())**.5),max_error_pu=float(delta.abs().max()),current_pairs=len(currents),current_mae_a=float(currents.delta_current_a.abs().mean()),current_rmse_a=float(currents.delta_current_a.pow(2).mean()**.5),provenance='historical_raw_recomputed',source=str(paired/f'{tag}_comparacao_tensoes.csv')))
    pd.DataFrame(results).to_csv(OUT/'04_validacao_s0.csv',index=False)
    phase=pd.read_csv(paired/'inventario_fases_opendss.csv')
    phase['provenance']='historical_topology_audit_not_rerun'
    phase.to_csv(OUT/'05_validacao_fases_e_linhas.csv',index=False)
    regs=pd.read_csv(paired/'comparacao_reguladores.csv')
    regs['provenance']='historical_ratios_not_independent_tap_validation'
    regs.to_csv(OUT/'06_validacao_controles.csv',index=False)


if __name__=='__main__':
    inventory()
    historical_metrics()
    print('Audit outputs saved:',OUT)
