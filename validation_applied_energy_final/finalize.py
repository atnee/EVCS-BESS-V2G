"""Complete paired metrics and seal read-only source evidence."""
from pathlib import Path
import hashlib
import json
import zipfile
import pandas as pd
from audit import A,B,OUT,digest,dump


def main():
    folder=A/'results/validacao_fases_s0_ae'
    rows=[]
    for hour in [3,18]:
        pp=json.loads((folder/f's0_h{hour:02d}_pandapower_metricas.json').read_text(encoding='utf-8'))
        for mode in ['mt_equivalente','completo']:
            ds=json.loads((folder/f's0_h{hour:02d}_opendss_{mode}_metricas.json').read_text(encoding='utf-8'))
            row=dict(hour=hour,mode=mode,provenance='historical_metrics_recomputed_not_new_solve')
            for key in ['p_subestacao_kw','q_subestacao_kvar','perdas_kw']:
                x,y=pp[key],ds[key]
                row.update({f'pp_{key}':x,f'dss_{key}':y,f'delta_{key}':y-x,f'relative_pct_{key}':100*(y-x)/abs(x) if abs(x)>1e-12 else None})
            row['dss_effective_load_by_balance_kw']=ds.get('potencia_efetiva_por_balanco_kw')
            row['dss_nominal_load_kw']=ds.get('potencia_carga_nominal_kw')
            row['balance_note']='Psource-loss; not independent Kirchhoff residual (native_s0 has independent terminal-load sum)'
            rows.append(row)
    pd.DataFrame(rows).to_csv(OUT/'metricas_pareadas_complementares.csv',index=False)
    inv=pd.read_csv(OUT/'02_inventario_modelos.csv')
    changed=[]
    for r in inv.itertuples():
        path=Path(r.absolute_path)
        if not path.exists() or digest(path)!=r.sha256:
            changed.append(str(path))
    dump('preservacao_historico.json',dict(files_checked=len(inv),changed=changed,pass_unchanged=not changed,scope='all files in inventory; new unlisted files are not evaluated'))
    if changed:
        raise RuntimeError('Historical inputs changed: '+repr(changed[:10]))
    archive=OUT/'snapshot_fontes.zip'
    # Exclusive creation prevents replacing an existing source snapshot.
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for r in inv.itertuples():
            name=r.relative_path
            if name.startswith(('src/','configs/','scripts/','opendss_rebuild/','distributed_stochastic/')) or (name.startswith('data/ieee8500/source/') and '/results/' not in name) or ('/' not in name and name.endswith('.py')):
                z.write(r.absolute_path,r.project+'/'+name)
    dump('snapshot_sha256.json',dict(file=archive.name,sha256=digest(archive),note='Verifiable snapshot; hash detects modification, not filesystem write protection'))
    print('Original evidence unchanged; verifiable source snapshot written.')


if __name__=='__main__':
    main()
