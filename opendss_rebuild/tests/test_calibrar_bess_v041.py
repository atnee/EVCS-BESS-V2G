"""Unit tests for V0.4.1 incremental power balance."""
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from calibrar_bess_opendss import power_delta_audit


def test_large_loss_reduction_is_valid_not_false_error():
    before=dict(bess_terminal_kw=0.,original_loads_kw=10000.,net_pc_absorbed_kw=11000.)
    after=dict(bess_terminal_kw=-200.,original_loads_kw=10000.,net_pc_absorbed_kw=10800.)
    result=power_delta_audit(before,after,-244.45,-44.45,200.)
    assert abs(result['actual_bess_injection_kw']-200.)<1e-9
    assert abs(result['power_balance_residual_kw'])<1e-9
    assert abs(result['loss_reduction_kw']-44.45)<1e-9


def test_voltage_dependent_load_changes_are_accounted_for():
    before=dict(bess_terminal_kw=0.,original_loads_kw=10000.,net_pc_absorbed_kw=10000.)
    after=dict(bess_terminal_kw=-200.,original_loads_kw=9992.,net_pc_absorbed_kw=9792.)
    result=power_delta_audit(before,after,-250.,-42.,200.)
    assert abs(result['original_load_delta_kw']+8.)<1e-9
    assert abs(result['power_balance_residual_kw'])<1e-9


def test_unexplained_change_detected():
    before=dict(bess_terminal_kw=0.,original_loads_kw=10000.,net_pc_absorbed_kw=10000.)
    after=dict(bess_terminal_kw=-200.,original_loads_kw=10000.,net_pc_absorbed_kw=9800.)
    result=power_delta_audit(before,after,-244.45,-10.,200.)
    assert abs(result['power_balance_residual_kw'])>30.


def test_element_terminal_powers_and_disabled():
    from calibrar_bess_opendss import element_terminal_p_kw
    class Circuit:
        def SetActiveElement(self, name):
            self.current=name
            return 1
    class Element:
        def __init__(self, circuit): self.circuit=circuit
        def Enabled(self): return not self.circuit.current.endswith('.off')
        def NumConductors(self): return 2
        def Powers(self):
            return [-10., -2., -5., -1., 100., 50., 300., 70.]
    class DSS:
        def __init__(self):
            self.Circuit=Circuit()
            self.CktElement=Element(self.Circuit)
    d=DSS()
    assert element_terminal_p_kw(d, 'Generator', 'one')==-15.
    assert element_terminal_p_kw(d, 'Generator', 'off')==0.
