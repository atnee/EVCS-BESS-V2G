"""Check identity and resource feasibility of the reused distributed dispatch."""
import sys
import numpy as np
import pandas as pd
from audit import A,B,OUT
from improve import save,read_team,read_existing
sys.path.insert(0,str(B))
from distributed_stochastic.coordenar_controles_v24 import validate_resources
from distributed_stochastic.otimizar_siting_v21 import build_injections


def main():
    root=B/'results/v301_continuacao_ac_segura/best'
    baseline=A/'results/replay_s0_s4_opendss'
    team=read_team(B,baseline);_,s1=read_existing(baseline)
    sizes=pd.read_csv(root/'bess_siting_sizing.csv')
    bd=pd.read_csv(root/'bess_dispatch.csv')
    vd=pd.read_csv(root/'vehicle_dispatch.csv')
    cap={r.site:dict(power_kw=r.power_kw,energy_kwh=r.energy_kwh) for r in sizes.itertuples()}
    bess={site:bd[bd.site==site].set_index('hour').p_kw.reindex(range(24)).to_numpy() for site in cap}
    ids=[v.id for v in team['vehicles']]
    if vd.groupby('vehicle_id').site.nunique().max()!=1:raise ValueError('Vehicle changes site during the horizon')
    places=vd.groupby('vehicle_id').site.first().reindex(ids).tolist()
    cars=vd.pivot(index='vehicle_id',columns='hour',values='p_kw').loc[ids,range(24)].to_numpy()
    validate_resources(team,bess,cars,places,cap)
    expected=build_injections(s1,team['old_fleet_profile'],bess,cars,places)
    saved=pd.read_csv(root/'injections_reoptimized.csv')
    keys=['hour','bus','phase']
    pair=expected.merge(saved,on=keys,how='outer',validate='one_to_one',suffixes=('_expected','_saved'),indicator=True)
    if not (pair['_merge']=='both').all():raise ValueError('Injection keys differ')
    error=float((pair.p_kw_expected-pair.p_kw_saved).abs().max())
    if error>1e-6:raise ValueError('Reused injections differ from component dispatch')
    save(OUT/'alternative_controls/resource_validation.json',dict(resource_validators_passed=True,vehicle_count=len(ids),allocation=pd.Series(places).value_counts().to_dict(),bess=sizes.to_dict(orient='records'),max_injection_error_kw=error,warning='Aggregate fleet/SOC/availability constraints verified; civil works, distributed charger infrastructure and connection ratings still require design approval.'))


if __name__=='__main__':main()
