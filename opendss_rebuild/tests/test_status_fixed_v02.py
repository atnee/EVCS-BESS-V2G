"""Regression tests for original IEEE OpenDSS fixed vs variable load handling."""
from pathlib import Path
from rebuild.profiles import original_load_components


def test_status_fixed_loads_are_not_scaled(tmp_path):
    folder = tmp_path / 'source'
    folder.mkdir()
    (folder/'Master.dss').write_text('New Circuit.IEEE8500\n')
    rows = []
    for i in range(24):
        rows.append(f'New Load.F{i} phases=1 bus1=x.1 kW=10 status=fixed model=1')
    for i in range(1153):
        rows.append(f'New Load.V{i} phases=1 bus1=x.1 kW=1 status=variable model=1')
    (folder/'Loads.dss').write_text('\n'.join(rows))
    ctx={'files':{'master':folder/'Master.dss'},'network':{'loads':[{'p_kw':1393.0}]}}
    d=original_load_components(ctx)
    assert d['fixed_count']==24 and d['variable_count']==1153
    assert d['fixed_kw']==240.0 and d['variable_kw']==1153.0
    assert d['fixed_kw']+d['variable_kw']*.6==931.8
