"""Check every bounded candidate with the native hourly S0 regulator taps."""
import os
import pandas as pd
from pathlib import Path
from audit import B,OUT
from improve import read_ac,metric_row
from check_alternatives import run
from rebuild.contracts import load_context


def main():
    ctx=load_context(B,B/'opendss_rebuild');cwd=Path.cwd()
    root=OUT/'improvement_experiment';out=OUT/'improvement_frozen';out.mkdir(exist_ok=False)
    s0=read_ac(OUT/'exact_profile_pilot/S0_auto');s1=read_ac(OUT/'exact_profile_pilot/S1_auto')
    rows=[]
    try:
        for r in pd.read_csv(root/'comparison.csv').itertuples():
            if r.case=='historical':continue
            paths=list((root/r.case).rglob('AC/injections_reoptimized.csv'))
            if len(paths)!=1:raise ValueError('Ambiguous candidate input: '+r.case)
            profile=pd.read_csv(paths[0])
            ac=run(ctx,profile,out/r.case,frozen=s0['regulators'])
            rows.append(metric_row(r.case,ac,s0,s1))
            pd.DataFrame(rows).to_csv(out/'comparison.csv',index=False)
    finally:os.chdir(cwd)


if __name__=='__main__':main()
