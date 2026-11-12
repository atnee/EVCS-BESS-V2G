"""Audit DER connection scope and cost assumptions without inventing quotations."""
from pathlib import Path
import json
import math
import os

import numpy as np
import pandas as pd
import yaml

from audit import A, B, OUT, digest


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def main() -> None:
    folder = OUT / 'connection_costs'
    folder.mkdir(exist_ok=False)
    bess_yaml = B / 'configs/bess.yaml'
    evcs_yaml = B / 'configs/evcs.yaml'
    v2g_yaml = B / 'configs/v2g.yaml'
    bess = read_yaml(bess_yaml)
    evcs = read_yaml(evcs_yaml)
    v2g = read_yaml(v2g_yaml)
    best = B / 'results/v301_continuacao_ac_segura/best'
    allocation_file = best / 'bess_siting_sizing.csv'
    fleet_file = best / 'vehicle_dispatch.csv'
    allocation = pd.read_csv(allocation_file)
    fleet = pd.read_csv(fleet_file)
    feeder_file = B / 'src/network/data/ieee8500.json'
    feeder = json.loads(feeder_file.read_text(encoding='utf-8'))
    bus_phases = {b['id'].lower():b['phases'] for b in feeder['buses']}
    if len(allocation)!=3 or not math.isclose(allocation.power_kw.sum(),2200,abs_tol=1e-3) or not math.isclose(allocation.energy_kwh.sum(),7200,abs_tol=1e-3):
        raise ValueError('Distributed BESS budget differs from the article reference')
    if fleet.groupby('vehicle_id').site.nunique().max()!=1 or fleet.vehicle_id.nunique()!=150:
        raise ValueError('Unexpected V2G allocation')
    sites = fleet.groupby('vehicle_id').site.first().value_counts().to_dict()
    public = evcs['integration']['sites']
    if set(sites)!={'m1209790','m1069504','l3160107'}:
        raise ValueError('Unrecognized distributed V2G sites')
    catalog = evcs['planning']['station_types'] if 'station_types' in evcs['planning'] else evcs['planning'].get('types')
    if catalog is None:
        raise KeyError('Public station type catalog not found; do not guess its ratings')
    bess_sites = pd.read_csv(B/'results/v21_shortlist/candidatos_bess.csv').set_index('bus')
    v2g_sites = pd.read_csv(B/'results/v21_shortlist/candidatos_v2g.csv').set_index('bus')
    rows=[]
    unit_usd_kwh=float(bess['battery']['capex'])/float(bess['battery']['capacity_kwh'])
    for r in allocation.itertuples(index=False):
        site=r.site.lower()
        if bus_phases.get(site)!='ABC':raise ValueError('BESS site not three phase: '+site)
        rows.append(dict(asset='BESS',site=site,study_site_status=str(bess_sites.loc[site,'infrastructure_status']),
                         assigned_resources=1,power_kw=r.power_kw,energy_kwh=r.energy_kwh,
                         lower_bound_interface_kva_at_pf1=r.power_kw,existing_public_evcs_kw=0,
                         conservative_coincident_nameplate_kw=r.power_kw,
                         reproduced_model_capex_usd=r.energy_kwh*unit_usd_kwh,
                         incremental_connection_cost_usd='',cost_status='UNKNOWN: transformer, PCS/interface, switchgear, protection, civil work, land and dedicated line need design/quotation'))
    station = evcs['station'];base_ports=int(station['chargers']);cost_per_port=float(station['capex'])/base_ports
    if sum(sites.values())!=150 or int(station['chargers'])!=154 or abs(cost_per_port-5000)>1e-9:
        raise ValueError('Host station reference differs from the saved article assumptions')
    for site,n in sorted(sites.items()):
        if bus_phases.get(site)!='ABC':raise ValueError('V2G site not three phase: '+site)
        independent_ports=4 if site==station['bus'].lower() else 0
        ports=n+independent_ports
        existing_type=public.get(site)
        if existing_type is not None and existing_type not in catalog:
            raise ValueError('Unknown existing public site type: '+str(existing_type))
        existing_kw=0.
        if existing_type is not None:
            public_type=catalog[existing_type]
            existing_kw=float(public_type['chargers_per_site'])*float(public_type['charger_kw'])
        rows.append(dict(asset='V2G',site=site,study_site_status=str(v2g_sites.loc[site,'infrastructure_status']),
                         assigned_resources=n,power_kw=ports*11.,energy_kwh='',
                         lower_bound_interface_kva_at_pf1=ports*11.,existing_public_evcs_kw=existing_kw,
                         conservative_coincident_nameplate_kw=ports*11.+existing_kw,
                         reproduced_model_capex_usd=ports*cost_per_port,
                         incremental_connection_cost_usd='',cost_status='UNKNOWN: bidirectional-port installation or transfer, existing AC capacity, transformer, protection, metering and civil works require site design'))
    table=pd.DataFrame(rows)
    table.to_csv(folder/'site_requirements.csv',index=False,float_format='%.10g')
    old_public=float(evcs['planning']['types']['ac']['capex_usd']) if 'types' in evcs['planning'] else float(catalog['ac']['capex_usd'])
    current_public_2=2*old_public
    check=dict(reference_bess_capex_usd=float(bess['battery']['capex']),reference_v2g_host_capex_usd=float(station['capex']),
               reference_public_ac_site_capex_usd_each=old_public,existing_two_public_ac_sites_model_capex_usd=current_public_2,
               distributed_bess_linear_reference_usd=float(table.loc[table.asset=='BESS','reproduced_model_capex_usd'].sum()),
               distributed_ports_linear_reference_usd=float(table.loc[table.asset=='V2G','reproduced_model_capex_usd'].sum()),
               additional_bess_sites_without_physical_connection_model=2,
               v2g_existing_public_ac_sites_requiring_bidirectional_upgrade=2,
               incremental_connection_cost_usd=None,
               known_incremental_total_usd=None,
               reason='Equal modeled energy- and port-based totals are bookkeeping identities. Site-specific interconnection, converter/transformer, protection, metering, land, works and utility costs were not parameterized or quoted.',
               warning='Apparent power at PF=1 is a theoretical lower bound, not an engineered transformer rating. Simultaneous port + public AC nameplate is a conservative active-power envelope, not a verified peak or AC load flow.',
               data_source_sha256={str(p.resolve()):digest(p) for p in [bess_yaml,evcs_yaml,v2g_yaml,allocation_file,fleet_file,feeder_file]})
    if not math.isclose(check['distributed_bess_linear_reference_usd'],check['reference_bess_capex_usd'],abs_tol=.01) or not math.isclose(check['distributed_ports_linear_reference_usd'],check['reference_v2g_host_capex_usd'],abs_tol=.01):
        raise ValueError('Model bookkeeping cost did not reconcile')
    (folder/'cost_reconciliation.json').write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Reviewed',len(table),'asset sites. Modeled costs reconcile; actual connection cost remains unknown.')


if __name__=='__main__':
    main()
