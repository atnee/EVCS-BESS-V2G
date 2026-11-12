"""Regression tests for conservative candidate acceptance."""
from improve import acceptable


def reference():
    return dict(max_loading_pct=120.,vmin=.93,vmax=1.06,thermal_pph=300.,under_puh=4.,over_puh=2.)


def test_thermal_gain_does_not_hide_voltage_deterioration():
    old=reference();new={**old,'thermal_pph':200.,'under_puh':4.1}
    assert not acceptable(new,old,dict(line_guard_tolerance_pp=.05,voltage_guard_tolerance_pu=.0005))


def test_average_gain_does_not_hide_worse_worst_line():
    old=reference();new={**old,'thermal_pph':200.,'max_loading_pct':121.}
    assert not acceptable(new,old,dict(line_guard_tolerance_pp=.05,voltage_guard_tolerance_pu=.0005))


def test_componentwise_gain_is_accepted():
    old=reference();new={**old,'thermal_pph':200.,'vmin':.935,'under_puh':3.}
    assert acceptable(new,old,dict(line_guard_tolerance_pp=.05,voltage_guard_tolerance_pu=.0005))
