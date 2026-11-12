"""Regression checks for physical masks, completeness and thermal reporting."""
import pandas as pd
import pytest
from native_check import physical_voltage_rows
from audit import physical_branches


def test_voltage_excludes_nonphysical_nodes_on_known_bus():
    rows=physical_voltage_rows(['x.1','x.2','x.4','y.3'],[1.,.02,.01,.98],{('x','A'),('y','C')})
    assert len(rows)==2
    assert min(r['v_pu'] for r in rows)==.98


@pytest.mark.parametrize('names,values', [(['x.1'],[]),(['x.1'],[float('nan')]),(['x.1','x.1'],[1.,1.])])
def test_bad_voltage_data_is_rejected(names,values):
    with pytest.raises(ValueError):
        physical_voltage_rows(names,values,{('x','A')})


def test_thermal_mask_excludes_transformer_and_phantom_phase():
    frame=pd.DataFrame({'line':['a','b','c'],'element_type':['line','line','transformer'],'physical_phase':[True,False,True],'loading_pct':[99,150,150]})
    assert physical_branches(frame).line.tolist()==['a']
