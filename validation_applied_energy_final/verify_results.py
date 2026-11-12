"""Recompute saved campaign and pilot evidence without rerunning optimization."""
import json
import math
import pandas as pd
from audit import A,B,OUT,digest,dump,physical_branches


def main():
    campaign=pd.read_csv(A/'results/ieee8500/sensitivity/sensitivity.csv')
    rows=[]
    for case in campaign['case'].unique():
        root=A/'results/ieee8500'/('integration' if case=='base' else f'sensitivity/{case}')
        lp=root/'dados/line_peaks.csv'
        peaks=pd.read_csv(lp) if lp.exists() else None
        for s in ['S0','S1','S2','S3','S4']:
            expected=campaign[(campaign['case']==case)&(campaign.scenario==s)].iloc[0]
            src=pd.read_csv(root/f'dados/{s}_source.csv')
            if peaks is not None:
                n=int(((peaks[s]>100)&(peaks.S0<=100)).sum())
                origin='retained_line_maxima'
            else:
                df=physical_branches(pd.read_csv(root/f'dados/{s}_branches.csv'))
                base=physical_branches(pd.read_csv(root/'dados/S0_branches.csv'))
                n=len(set(df.loc[df.loading_pct>100,'line'])-set(base.loc[base.loading_pct>100,'line']))
                origin='full_branch_series'
            rows.append(dict(case=case,scenario=s,steps=len(src),new_lines=n,expected_new_lines=int(expected.new_overloads),peak_kw=float(src.p_kw.max()),peak_matches=math.isclose(float(src.p_kw.max()),float(expected.peak_kw),abs_tol=1e-7),lines_match=n==int(expected.new_overloads),thermal_evidence=origin))
    df=pd.DataFrame(rows);df.to_csv(OUT/'campanha_315_verificada.csv',index=False)
    dump('campanha_verificacao.json',dict(rows=len(df),all_peak_match=bool(df.peak_matches.all()),all_line_counts_match=bool(df.lines_match.all()),full_series_rows=int((df.thermal_evidence=='full_branch_series').sum())))
    history=json.loads((A/'results/validacao_fases_s0_ae/relatorio_validacao_s0.json').read_text(encoding='utf-8'))
    checks=[dict(path=p,recorded=h,current=digest(__import__('pathlib').Path(p)),matches=h==digest(__import__('pathlib').Path(p))) for p,h in history['original_files_sha256'].items()]
    pd.DataFrame(checks).to_csv(OUT/'validacao_hashes_historicos.csv',index=False)
    # The native pilot already exists. Recompute its statistics from saved phases.
    base=A/'results/replay_s0_s4_opendss'
    s0=pd.read_csv(base/'S0/branches.csv')
    basebad=set(s0.loc[s0.loading_pct>100,'line'])
    pilots=[]
    for s in ['S0','S1','S4']:
        folder=base/s
        v=pd.read_csv(folder/'voltages.csv');i=pd.read_csv(folder/'branches.csv');p=pd.read_csv(folder/'source.csv')
        pilots.append(dict(scenario=s,steps=len(p),voltage_rows=len(v),branch_rows=len(i),vmin=float(v.v_pu.min()),vmax=float(v.v_pu.max()),max_loading_pct=float(i.loading_pct.max()),new_lines=len(set(i.loc[i.loading_pct>100,'line'])-basebad),peak_kw=float(p.p_kw.max()),loss_kwh=float(p.loss_kw.sum()),dt_h=1,provenance='historical_native_pilot_recomputed_no_new_run'))
    pd.DataFrame(pilots).to_csv(OUT/'piloto_opendss_recalculado.csv',index=False)
    # Independently recompute the V3.0.1 absolute violations with dt_h = 1.
    root=B/'results/v301_continuacao_ac_segura/best/AC'
    v=pd.read_csv(root/'voltages.csv');i=pd.read_csv(root/'branches.csv')
    dump('v301_recalculado.json',dict(dt_h=1,hours=sorted(i.hour.unique().tolist()),thermal_pp_h=float((i.loading_pct-100).clip(lower=0).sum()),voltage_pu_h=float((.95-v.v_pu).clip(lower=0).sum()+(v.v_pu-1.05).clip(lower=0).sum()),vmin=float(v.v_pu.min()),vmax=float(v.v_pu.max())))
    out=OUT/'native_s0'
    rows=[]
    for hour in [3,18]:
        x=pd.read_csv(out/f'project_h{hour:02d}_branches.csv');y=pd.read_csv(out/f'installed_h{hour:02d}_branches.csv')
        pair=x.merge(y,on=['line','phase'],validate='one_to_one',suffixes=('_p','_i'))
        d=pair.current_a_p-pair.current_a_i
        xreg=pd.read_csv(out/f'project_h{hour:02d}_regulators.csv');yreg=pd.read_csv(out/f'installed_h{hour:02d}_regulators.csv')
        regs=xreg.merge(yreg,on='regulator',validate='one_to_one',suffixes=('_p','_i'))
        rows.append(dict(hour=hour,pairs=len(pair),current_mae_a=float(d.abs().mean()),current_max_error_a=float(d.abs().max()),overload_classifications_match=bool(((pair.loading_pct_p>100)==(pair.loading_pct_i>100)).all()),tap_pairs=len(regs),max_tap_difference=int((regs.tap_p-regs.tap_i).abs().max())))
    pd.DataFrame(rows).to_csv(out/'current_controls_comparison.csv',index=False)
    # Persist all other historical ablations with their conditions.
    frames=[]
    for p in (A/'results/validacao_applied_energy').glob('*/resumo_pareado.csv'):
        f=pd.read_csv(p);f['experiment']=p.parent.name;f['source']=str(p);frames.append(f)
    pd.concat(frames,ignore_index=True).to_csv(OUT/'ablacoes_historicas.csv',index=False)
    print('Campaign, native pilot, V3.0.1 and historical hashes checked.')


if __name__=='__main__':
    main()
