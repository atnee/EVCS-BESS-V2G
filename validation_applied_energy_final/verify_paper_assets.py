"""Validate source hashes, figure files and non-ambiguous paper table semantics."""
from pathlib import Path
import hashlib
import json

import pandas as pd
import pymupdf
from PIL import Image


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parent / 'paper_insertions'
    m = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    assert len(m['source_files']) == 44
    assert len(m['tables']) == 10
    assert len(m['figures']) == 9
    for record in m['source_files'].values():
        p = Path(record['path'])
        if not p.is_file() or digest(p) != record['sha256']:
            raise AssertionError(f'Source changed or missing: {p}')
    for kind, subfolder in [('tables', 'tables'), ('figures', 'figures')]:
        for name, expected in m[kind].items():
            p = root / subfolder / name
            if not p.is_file() or digest(p) != expected:
                raise AssertionError(f'Output changed or missing: {p}')
    for pdf in (root / 'figures').glob('*.pdf'):
        with pymupdf.open(pdf) as document:
            if len(document) != 1 or document[0].rect.width < 450 or len(document[0].get_text()) < 20:
                raise AssertionError(f'PDF figure failed the text/page check: {pdf}')
            if document[0].get_images(full=True):
                raise AssertionError(f'PDF plot contains a rasterized image: {pdf}')
        with Image.open(pdf.with_suffix('.tif')) as tiff:
            if tiff.width < 2000 or tiff.height < 850:
                raise AssertionError(f'Low-resolution TIFF: {tiff.filename}')
    validation = pd.read_csv(root / 'tables/table_S1_paired_voltage_error.csv')
    assert set(validation.hour) == {3, 18}
    assert (validation[validation.phase == 'ALL'].pairs == 3820).all()
    op = pd.read_csv(root / 'tables/table_13_opendss_operational_comparison.csv')
    assert len(op) == 5 and op.hours.eq(24).all() and op.dt_h.eq(1).all()
    assert op.set_index('case').loc['S4 single BESS', 'new_overloaded_lines_vs_s0'] == 23
    assert op.set_index('case').loc['S4 distributed', 'new_overloaded_lines_vs_s0'] == 0
    q = pd.read_csv(root / 'tables/table_S3_distributed_bess_allocation.csv')
    assert abs(q.power_kw.sum() - 2200) < 1e-3 and abs(q.energy_kwh.sum() - 7200) < 1e-3
    a = pd.read_csv(root / 'tables/table_S4_distributed_v2g_allocation.csv')
    assert a.vehicles.sum() == 150
    for policy,number,s3_lines in [('native',15,32),('changed',16,33)]:
        full=pd.read_csv(root/f'tables/table_{number}_distributed_s0_s4_{policy}.csv')
        assert full.scenario.tolist()==['S0','S1','S2','S3','S4']
        assert int(full.loc[full.scenario=='S2','new_overloaded_lines_vs_S0'].iloc[0])==0
        assert int(full.loc[full.scenario=='S3','new_overloaded_lines_vs_S0'].iloc[0])==s3_lines
    costs=pd.read_csv(root/'tables/table_S5_connection_scope_and_missing_costs.csv')
    assert len(costs)==6 and costs.incremental_connection_cost_usd.isna().all()
    result = {'status':'PASS', 'source_files_checked':len(m['source_files']),
              'tables_checked':len(m['tables']), 'figures_checked':len(m['figures']),
              'pdf_figures_have_vector_plots':True, 'full_feeder_equivalence_claimed':False}
    (root / 'qa.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
