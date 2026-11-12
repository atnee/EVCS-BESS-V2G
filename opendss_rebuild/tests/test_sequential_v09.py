"""Tests of AC acceptance, without assuming OpenDSS is available on this machine."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import numpy as np
import pandas as pd
from reotimizar_sequencial_v09 import decision,relative_score


def test_merit_acceptance_guards():
    before={'ac_merit':1000.,'thermal_vs_s0_pph':100.,'total_voltage_vs_s0_puh':5.}
    better={'ac_merit':900.,'thermal_vs_s0_pph':96.,'total_voltage_vs_s0_puh':5.01}
    assert decision(before,better,.05,.10,1.)[0]
    worse=dict(better);worse['thermal_vs_s0_pph']=107.
    assert not decision(before,worse,.05,.10,1.)[0]
    worse=dict(better);worse['total_voltage_vs_s0_puh']=6.
    assert not decision(before,worse,.05,.10,1.)[0]
    worse=dict(better);worse['ac_merit']=1001.
    assert not decision(before,worse,.05,.10,1.)[0]


def test_score_relative_baseline():
    s0={'source':pd.DataFrame({'hour':[0],'p_kw':[1000.]}),
        'branches':pd.DataFrame({'hour':[0],'line':['x'],'phase':['A'],'loading_pct':[110.]}),
        'voltages':pd.DataFrame({'hour':[0],'bus':['a'],'phase':['A'],'v_pu':[.93]})}
    s4={'source':pd.DataFrame({'hour':[0],'p_kw':[900.]}),
        'branches':pd.DataFrame({'hour':[0],'line':['x'],'phase':['A'],'loading_pct':[113.]}),
        'voltages':pd.DataFrame({'hour':[0],'bus':['a'],'phase':['A'],'v_pu':[.94]})}
    ans=relative_score(s0,s4,{'thermal':40.,'voltage':5000.,'peak':.5})
    assert abs(ans['thermal_vs_s0_pph']-3.)<1e-10
    assert abs(ans['under_vs_s0_puh'])<1e-10  # improved vs S0 even if still below .95
    assert ans['over_vs_s0_puh']==0
    assert ans['ac_merit']==3*40+.5*900
