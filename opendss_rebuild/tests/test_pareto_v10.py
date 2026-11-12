"""Scientific contract tests; no OpenDSS engine required for these tests."""
import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reotimizar_pareto_v10 import non_dominated, config_for, PROFILES
from rebuild.joint_optimization import OptSettings, solve_joint
from test_joint_optimization import fixture_case, install_v2g_stubs


def test_four_objective_nondominance():
    items=[
       dict(thermal_vs_s0_pph=250,under_vs_s0_puh=1.8,over_vs_s0_puh=1.76,peak_import_kw=13119),
       dict(thermal_vs_s0_pph=90,under_vs_s0_puh=2.74,over_vs_s0_puh=1.61,peak_import_kw=12819),
       dict(thermal_vs_s0_pph=150,under_vs_s0_puh=3.1,over_vs_s0_puh=1.72,peak_import_kw=13000),
    ]
    assert non_dominated(items)==[True,True,False]


def test_epsilon_not_negative(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['settings']=OptSettings(max_under_slack_puh=-0.1,solver_time_limit_s=10)
    with pytest.raises(ValueError):
        solve_joint(**case,mode='S4')


def test_epsilon_predicted_slacks_bounded(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['settings']=OptSettings(max_under_slack_puh=0.05,max_over_slack_puh=0.05,
                                 solver_time_limit_s=15,mip_rel_gap=.03)
    result=solve_joint(**case,mode='S4')
    assert result['report']['undervoltage_slack_sum_pu_h']<=0.05+1e-5
    assert result['report']['overvoltage_slack_sum_pu_h']<=0.05+1e-5
    assert result['report']['epsilon_caps_predicted_only']['under_puh']==0.05


def test_epsilon_infeasible_with_no_voltage_sensitivity(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    case['ds_volt'][['dv_bess','dv_v2g']]=0.
    case['settings']=OptSettings(max_under_slack_puh=0.005,
                                 solver_time_limit_s=10)
    with pytest.raises(RuntimeError):
        solve_joint(**case,mode='S4')


def test_presets_and_reference_budget():
    from types import SimpleNamespace
    team={'battery':SimpleNamespace(degradation_per_kwh=.03),
          'configs':{'v2g':{'cesar_v1':{'degradation_usd_kwh':0.}}}}
    ref={'under_vs_s0_puh':1.8,'over_vs_s0_puh':1.7}
    from reotimizar_pareto_v10 import config_for
    guard=config_for('under_guard',ref,250,200,30,.02,team)
    assert guard.max_under_slack_puh>1.8 and guard.max_under_slack_puh<2.0
    assert guard.max_over_slack_puh is None
    assert all(x in PROFILES for x in ['balanced','thermal','under_guard','over_guard','voltage_guard'])
