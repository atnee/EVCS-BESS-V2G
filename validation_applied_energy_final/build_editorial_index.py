"""Index existing manuscript assets and map source-specific editorial updates."""
from pathlib import Path
import hashlib
import csv

ROOT = Path(__file__).resolve().parent
B = ROOT.parent
A = B.parent / 'EVCS-BESS-V2G'
OUT = ROOT / 'paper_insertions'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if not (OUT / 'qa.json').is_file():
        raise FileNotFoundError('Verify the new scientific assets before indexing the manuscript')
    repo = A if (A / 'paper/04_figuras/fig1_framework.pdf').is_file() else B
    rows = []
    for name in [*[f'fig{i}_{suffix}' for i,suffix in (
        (1,'framework'), (2,'station_siting'), (3,'ev_penetration'),
        (4,'service_transformers'), (5,'substation_demand'),
        (6,'line_loading_maps'), (7,'regulators_voltage'),
        (8,'sensitivity_s4_seeds'))],
        'figS1_regulator_taps','figS2_sensitivity_s4_one_at_a_time']:
        for ext in ('.pdf','.tif'):
            path = (repo / 'paper/04_figuras' / (name+ext)).resolve(strict=True)
            rows.append(dict(figure=name,format=ext[1:],path=str(path),sha256=digest(path),
                             electrical_scope='historical pandapower MV approximation',
                             editorial_status='retained only with explicit historical-model label'))
    with (OUT / 'historical_figure_index.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    edits=[
        ('Abstract','49 to zero lines; 10.89%; 63 cases','Retain these as within-model historical pandapower findings; add the separate physical-replay limitation and avoid suggesting full-feeder adequacy.','Fig. 10; Table 13'),
        ('Section 2.1','Historical line/phase simplifications','Retain the original methodology; add an independent subsection describing the later full OpenDSS replay, its source/control settings, fixed-tap pair, and non-equivalence.','Fig. 9; Tables S1-S2'),
        ('Sections 2.3-2.4','One BESS and 150 cars at one host','Keep as historical experimental design; identify the later three-BESS and three-site V2G comparison as a separate design with the same aggregate BESS ratings and 150 participants.','Tables S3-S4'),
        ('Section 3.2 and Table 6','Historical S0-S4 reference numbers','Do not replace the existing table silently; add Table 13 with the physical replay as a distinct result.','Fig. 10; Table 13'),
        ('New results subsection after Section 3.8','S2/S3 attribution on the complete physical feeder','Add separate S0-S4 distributed replays under native and modified regulator controls, preserving the historical results as a different model.','Tables 15-16; Table S6'),
        ('Section 3.4','Regulator response','Add the native-control versus changed-control and hourly S0-frozen views; capacitor controls remain automatic.','Fig. 11; Table 14'),
        ('Section 3.7 and economic comparison','Equal modeled BESS and V2G component totals','State that incremental physical connection costs remain unknown; two new BESS points and two AC station upgrades lack project designs and quotations.','Table S5; connection_costs/cost_reconciliation.json'),
        ('Section 4.3','No OpenDSS comparison has been completed','Replace this now-obsolete statement with the performed paired and native replays; report their limitations and no numerical equivalence.','Fig. 9; Tables S1-S2'),
        ('Conclusions','Transferability and completeness','Distinguish verified within-model 63-case statements from the new distributed physical case; state that even the improved case retains absolute overload and voltage violations.','Tables 13-14'),
        ('Data and software availability','Public snapshot and unavailable full raw archive','Confirm the repository commit and public availability at the time of submission; describe the new input hashes, archived source datasets and raw replay location accurately.','manifest.json'),
    ]
    with (OUT/'manuscript_edit_map.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['manuscript_location','existing_claim_or_scope','required_editorial_action','supporting_assets']);w.writerows(edits)
    print(f'Indexed {len(rows)} historical figure formats and {len(edits)} editorial locations.')


if __name__=='__main__':
    main()
