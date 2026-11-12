"""Unit tests for V1.1. No OpenDSS executable is used by these tests."""
import sys
from pathlib import Path
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reotimizar_guardas_v11 import (build_guards,check_guards_ac,introduced_overloads,
                                   new_line_ids,PROFILES)
from rebuild.joint_optimization import OptSettings,solve_joint
from test_joint_optimization import fixture_case,install_v2g_stubs


def test_solver_guard_integration(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['settings']=OptSettings(solver_time_limit_s=15,
                 guarded_line_max_pct=((18,'l1','A',118.0),),
                 guarded_voltage_min_pu=((18,'b1','A',0.9301),))
    out=solve_joint(**case,mode='S4')
    assert out['report']['ac_guided_linear_guard_counts']=={'line':1,'voltage_lower':1,'voltage_upper':0}
    h=18
    predicted_line=120-.04*out['bess_net_kw'][h]-.03*out['v2g_net_kw'][h]
    predicted_v=.93+.00008*out['bess_net_kw'][h]+.00005*out['v2g_net_kw'][h]
    assert predicted_line<=118.001
    assert predicted_v>=.9301-1e-6


def test_solver_rejects_impossible_guard(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['settings']=OptSettings(solver_time_limit_s=15,
                 guarded_line_max_pct=((18,'l1','A',20.0),))
    with pytest.raises(RuntimeError):solve_joint(**case,mode='S4')


def test_solver_rejects_unknown_and_duplicate(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['settings']=OptSettings(guarded_line_max_pct=((18,'l100','A',100.0),))
    with pytest.raises(ValueError,match='ausente'):
        solve_joint(**case,mode='S4')
    case['settings']=OptSettings(guarded_voltage_min_pu=((18,'b1','A',.90),(18,'b1','A',.92)))
    with pytest.raises(ValueError,match='duplicada'):
        solve_joint(**case,mode='S4')


def test_build_ac_guards_from_24_hour_phases():
    s0_lines=[];s1_lines=[];parent_lines=[];dl=[]
    s0_volt=[];s1_volt=[];parent_volt=[];dv=[]
    for h in range(24):
        for line in ('old','new'):
            old=102. if line=='old' and h==18 else 75.
            one=108. if h==18 else 78.
            last=(104. if line=='old' else 97.) if h==18 else 76.
            s0_lines.append(dict(hour=h,line=line,phase='A',loading_pct=old))
            s1_lines.append(dict(hour=h,line=line,phase='A',loading_pct=one))
            parent_lines.append(dict(hour=h,line=line,phase='A',loading_pct=last))
            dl.append(dict(hour=h,line=line,phase='A',dl_bess=-.03,dl_v2g=-.02))
        s0_volt.append(dict(hour=h,bus='low',phase='A',v_pu=.95))
        s1_volt.append(dict(hour=h,bus='low',phase='A',v_pu=.92))
        parent_volt.append(dict(hour=h,bus='low',phase='A',v_pu=(.935 if h==18 else .95)))
        dv.append(dict(hour=h,bus='low',phase='A',dv_bess=.0001,dv_v2g=.00008))
    s0={'branches':pd.DataFrame(s0_lines),'voltages':pd.DataFrame(s0_volt)}
    s1={'branches':pd.DataFrame(s1_lines),'voltages':pd.DataFrame(s1_volt)}
    parent={'branches':pd.DataFrame(parent_lines),'voltages':pd.DataFrame(parent_volt)}
    assert new_line_ids(s0['branches'],s1['branches'])=={'new'}
    assert introduced_overloads(parent['branches'],s0['branches'])==set()
    guards=build_guards(s0,s1,parent,(pd.DataFrame(dl),pd.DataFrame(dv)),
                        'thermal_guard',180,150)
    assert guards['stats']['recovered_line_ids']==1
    assert guards['stats']['full_recovered_line_phase_hours']==24
    assert (18,'new','A',100.) in guards['line']
    assert any(x[1]=='old' and x[0]==18 and x[3]<104 for x in guards['line'])
    assert any(x[1]=='low' and x[0]==18 and x[3]>.935 for x in guards['under'])
    assert check_guards_ac(parent,guards)['line']['checked']==25
    parentbad={'branches':parent['branches'].copy(),'voltages':parent['voltages']}
    parentbad['branches'].loc[(parentbad['branches'].hour==18)&(parentbad['branches'].line=='new'),'loading_pct']=100.01
    assert introduced_overloads(parentbad['branches'],s0['branches'])=={'new'}
    with pytest.raises(ValueError,match='reintroduziu'):
        build_guards(s0,s1,parentbad,(pd.DataFrame(dl),pd.DataFrame(dv)),'thermal_guard',180,150)


def test_guards_reject_missing_derivative():
    s0={'branches':pd.DataFrame([dict(hour=0,line='l',phase='A',loading_pct=90)]),
        'voltages':pd.DataFrame([dict(hour=0,bus='b',phase='A',v_pu=1.)])}
    s1={'branches':pd.DataFrame([dict(hour=0,line='l',phase='A',loading_pct=105)]),
        'voltages':pd.DataFrame([dict(hour=0,bus='b',phase='A',v_pu=1.)])}
    parent={'branches':pd.DataFrame([dict(hour=0,line='l',phase='A',loading_pct=90)]),
        'voltages':pd.DataFrame([dict(hour=0,bus='b',phase='A',v_pu=1.)])}
    dlines=pd.DataFrame(columns=['hour','line','phase','dl_bess','dl_v2g'])
    dv=pd.DataFrame([dict(hour=0,bus='b',phase='A',dv_bess=0.,dv_v2g=0.)])
    with pytest.raises(ValueError,match='derivadas'):
        build_guards(s0,s1,parent,(dlines,dv),'thermal_guard',100.,100.)
