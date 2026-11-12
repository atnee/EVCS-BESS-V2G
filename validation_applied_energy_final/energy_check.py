"""Validate saved historical dispatch with existing module contracts, no optimizer."""
import json
import sys
import numpy as np
import pandas as pd
import yaml
from audit import A,B,OUT,dump


def main():
    sys.path.insert(0,str(B/'src'))
    from core.assets import Battery,Station
    from core.schemas import TimeGrid
    from bess.model import simulate
    from v2g.runner import vehicles_from_config,discharge_mask
    from v2g.strategies import fleet_profile
    b=yaml.safe_load((A/'configs/bess.yaml').read_text(encoding='utf-8'))
    v=yaml.safe_load((A/'configs/v2g.yaml').read_text(encoding='utf-8'))
    root=A/'results/ieee8500/integration'
    results={}
    for scenario in ['S2','S4']:
        command=pd.read_csv(root/f'modules/bess/{scenario}/dispatch.csv').injection_kw.to_numpy()
        dispatch=simulate(Battery(**b['battery']),command,1.,final_soc=b['battery']['soc_initial'])
        saved=pd.read_csv(root/f'dados/{scenario}_bess_soc.csv').soc.to_numpy()
        if not np.allclose(dispatch.soc,saved,atol=1e-9,rtol=0) or np.max(np.abs(dispatch.rejected_kw))>1e-7:
            raise ValueError('Historical BESS dispatch/state mismatch')
        results[scenario+'_bess']=dict(initial_soc=float(dispatch.soc[0]),final_soc=float(dispatch.soc[-1]),boundary_states=len(saved),steps=len(command),matches_saved=True)
    ev=json.loads((root/'modules/evcs/S4/manifest.json').read_text(encoding='utf-8'))
    saved=json.loads((root/'dados/S4_fleet.json').read_text(encoding='utf-8'))
    grid=TimeGrid(**v['time'])
    vehicles=vehicles_from_config(v)
    meta=ev['metadata']
    validated=fleet_profile(Station(**meta['station']),grid,vehicles,saved['vehicle_power_kw'],meta['station_demand_kw'],ev['vehicle_ids'],discharge_mask(v,grid),True)
    if not np.allclose(validated.metadata['energy_kwh'],saved['energy_kwh'],atol=1e-7,rtol=0):
        raise ValueError('Historical V2G state mismatch')
    results['S4_v2g']=dict(vehicles=len(vehicles),delivered_kwh=validated.metadata['v2g_delivered_kwh'],departure_mobility_soc_power_ports_validated=True,matches_saved_states=True)
    dump('energia_historica_verificada.json',results)
    print('Saved BESS and V2G dispatch passed existing validators.')


if __name__=='__main__':
    main()
