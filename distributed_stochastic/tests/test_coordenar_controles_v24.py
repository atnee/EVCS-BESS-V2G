"""V2.4 contracts; no OpenDSS live engine required for these checks."""
import json
from pathlib import Path
import pandas as pd
import pytest
from distributed_stochastic.coordenar_controles_v24 import (
    build_policies, apply_policy, inventory, is_cap_voltage, reg_phase,
    guard_and_accept, control_changes, compare_replay_full, voltage_components)


class FakeReg:
    def __init__(self):
        self.selected=None
        self.values={'feeder_rega':120.,'vreg3_c':121.}
    def Name(self,name): self.selected=name.lower()
    def ForwardVreg(self,v=None):
        if v is not None:self.values[self.selected]=v
        return self.values[self.selected]
    def ForwardBand(self):return 2.
    def PTRatio(self):return 60.


class FakeCap:
    def __init__(self):
        self.selected=None
        self.vals={'cap3c':[1,116.,122.], 'cap2a':[0,200.,190.]}
    def AllNames(self):return list(self.vals)
    def Name(self,name):self.selected=name.lower()
    def Mode(self):return self.vals[self.selected][0]
    def ONSetting(self,v=None):
        if v is not None:self.vals[self.selected][1]=v
        return self.vals[self.selected][1]
    def OFFSetting(self,v=None):
        if v is not None:self.vals[self.selected][2]=v
        return self.vals[self.selected][2]


class FakeEngine:
    def __init__(self):
        self.regnames=['feeder_rega','vreg3_c'];self.capnames=['capbank3']
        self.dss=type('DSS',(),{'RegControls':FakeReg(),'CapControls':FakeCap()})()


def score(thermal=100,voltage=80,peak=12500,new=0,samples=44,strict=False,relative=False):
    return dict(thermal_aggravation_pp_h=thermal,voltage_aggravation_samples=voltage,
      source_peak_kw=peak, new_overloaded_lines_vs_s0=new, thermal_aggravation_samples=samples,
      strict_pass=strict,relative_s0_pass=relative)


def comp(under=.8,over=1.4):
    return dict(under_rel_puh=under,over_rel_puh=over,voltage_rel_puh=under+over)


def test_reg_phase_restrict_to_known_names():
    assert reg_phase('vreg3_a')=='A'
    assert reg_phase('feeder_regc')=='C'
    assert reg_phase('badc') is None


def test_cap_mode_must_be_voltage():
    assert is_cap_voltage(1) and is_cap_voltage('Voltage')
    assert not is_cap_voltage(0) and not is_cap_voltage('kvar')


def test_inventory_and_application():
    e=FakeEngine();iv=inventory(e)
    assert len(iv['regulators'])==2
    assert iv['capcontrols']['cap3c']['voltage_mode']
    applied=apply_policy(e,iv,{'name':'test','reg_vreg_offset_v':{'feeder_rega':.5},
                             'cap_onoff_offset_v':{'cap3c':-.5}})
    assert e.dss.RegControls.values['feeder_rega']==120.5
    assert e.dss.CapControls.vals['cap3c']==[1,115.5,121.5]
    assert applied['regulators']['feeder_rega']['delta_v']==.5
    with pytest.raises(ValueError):
        apply_policy(e,iv,{'name':'bad','reg_vreg_offset_v':{'vreg3_c':2.},'cap_onoff_offset_v':{}})
    with pytest.raises(ValueError):
        apply_policy(e,iv,{'name':'bad','reg_vreg_offset_v':{},'cap_onoff_offset_v':{'cap2a':.1}})


def test_policy_candidate_limits_and_uniqueness():
    inv=inventory(FakeEngine())
    pol=build_policies(inv,.5,12,True)
    assert pol[0]['name']=='original'
    assert len(pol)==len(set(json.dumps((x['reg_vreg_offset_v'],x['cap_onoff_offset_v']),sort_keys=True) for x in pol))
    assert any(x['cap_onoff_offset_v'] for x in pol)
    assert all('cap2a' not in x['cap_onoff_offset_v'] for x in pol)
    assert len(build_policies(inv,.5,2))==2


def test_acceptance_protects_lines_thermal_voltage_and_taps():
    prev=score();base=comp()
    op=dict(tap_absolute_steps_total=124,capacitor_switch_events=14)
    better=score(thermal=101)
    cc=comp(under=.78,over=1.39)
    assert guard_and_accept(prev,base,better,cc,ref_ops=op,cand_ops=op)[0]
    assert not guard_and_accept(prev,base,score(new=1),cc)[0]
    assert not guard_and_accept(prev,base,score(thermal=112),cc)[0]
    assert not guard_and_accept(prev,base,score(),comp(under=1.,over=1.1))[0]
    bad=dict(tap_absolute_steps_total=300,capacitor_switch_events=14)
    assert guard_and_accept(prev,base,better,cc,ref_ops=op,cand_ops=bad)[1]=='excessive_tap_steps_vs_reference'
    assert guard_and_accept(prev,base,score(),comp(under=.4,over=1.41))[0]


def test_operational_changes_count():
    ac={'regulators':pd.DataFrame({'hour':[0,1,2,0,1,2],
       'regulator':['a']*3+['b']*3,'tap':[0,1,1,0,-1,0]}),
       'capacitors':pd.DataFrame({'hour':[0,1,2],'capacitor':['x']*3,'states':['[0]','[1]','[1]']})}
    o=control_changes(ac)
    assert o['tap_absolute_steps_total']==3
    assert o['tap_change_events']==3
    assert o['capacitor_switch_events']==1


def test_complete_baseline_replay_comparison():
    ac={'branches':pd.DataFrame({'hour':[0],'line':['x'],'phase':['A'],'loading_pct':[99.]}),
        'voltages':pd.DataFrame({'hour':[0],'bus':['z'],'phase':['A'],'v_pu':[1.]}),
        'source':pd.DataFrame({'hour':[0],'p_kw':[1000.]}),
        'regulators':pd.DataFrame({'hour':[0],'regulator':['r'],'tap':[3]}),
        'capacitors':pd.DataFrame({'hour':[0],'capacitor':['c'],'states':['[1]']})}
    assert compare_replay_full(ac,ac)['source']==0.
    changed={k:v.copy() for k,v in ac.items()}
    changed['regulators'].loc[0,'tap']=2
    with pytest.raises(RuntimeError):compare_replay_full(ac,changed)


def test_real_v23_score_if_uploaded_dataset_exists():
    p=Path('/mnt/data/_v24_v23_inputs/v23_tensao/best/AC')
    if not p.is_dir():pytest.skip('Previous uploaded OpenDSS archive not mounted')
    reg=pd.read_csv(p/'regulators.csv');cap=pd.read_csv(p/'capacitors.csv')
    changes=control_changes({'regulators':reg,'capacitors':cap})
    assert changes['tap_absolute_steps_total']==124
    assert changes['capacitor_switch_events']==14
