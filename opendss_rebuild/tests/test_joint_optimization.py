"""Tests independent of OpenDSS; AC feasibility still requires Windows run."""
import sys
from pathlib import Path
from types import SimpleNamespace,ModuleType
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rebuild.joint_optimization import MatrixBuilder,OptSettings,solve_joint


def install_v2g_stubs(monkeypatch):
    pkg=ModuleType('v2g');pkg.__path__=[]
    available=ModuleType('v2g.availability')
    def session_steps(steps,arrival,departure):
        if arrival<departure:return list(range(arrival,departure))
        return list(range(arrival,steps))+list(range(departure))
    def availability(steps,arrival,departure):
        a=np.zeros(steps,bool);a[session_steps(steps,arrival,departure)]=True;return a
    available.availability=availability;available.session_steps=session_steps
    strat=ModuleType('v2g.strategies')
    def fleet_profile(station,grid,vehicles,power_kw,independent_evcs_kw,independent_ids=(),discharge_mask=None,enable_v2g=True):
        assert power_kw.shape==(len(vehicles),24)
        for i,v in enumerate(vehicles):
            order=session_steps(24,v.arrival,v.departure)
            e=v.battery.soc_initial*v.battery.capacity_kwh
            for t in order:
                pw=power_kw[i,t]
                assert abs(pw)<=v.battery.power_kw+1e-4
                if pw>0:assert discharge_mask[t]
                assert np.abs(power_kw[i][[x for x in range(24) if x not in order]]).max(initial=0)<1e-5
                e+=(-pw*v.battery.eta_charge if pw<0 else -pw/v.battery.eta_discharge)
                assert v.battery.soc_min*v.battery.capacity_kwh-1e-5<=e<=v.battery.soc_max*v.battery.capacity_kwh+1e-5
            assert e>=v.departure_target_kwh-1e-5
        return True
    strat.fleet_profile=fleet_profile
    monkeypatch.setitem(sys.modules,'v2g',pkg)
    monkeypatch.setitem(sys.modules,'v2g.availability',available)
    monkeypatch.setitem(sys.modules,'v2g.strategies',strat)


def fixture_case():
    def make_vehicle(id,arr,dep,e0):
        bat=SimpleNamespace(capacity_kwh=50.,power_kw=11.,soc_initial=e0/50,
                            soc_min=.10,soc_max=.9,eta_charge=.95,eta_discharge=.95)
        return SimpleNamespace(id=id,battery=bat,arrival=arr,departure=dep,
                               reserve_kwh=12.,departure_target_kwh=e0+5.)
    vehicles=[make_vehicle('overnight',17,6,30.),make_vehicle('daytime',14,23,30.)]
    battery=SimpleNamespace(power_kw=100.,capacity_kwh=500.,soc_initial=.4,soc_min=.1,soc_max=.9,
                            eta_charge=.95,eta_discharge=.95)
    station=SimpleNamespace(bus='v2g_bus',charger_kw=11.,connection_kw=100.,chargers=4,bidirectional=True)
    grid=SimpleNamespace(steps=24,dt_h=1.)
    s1src=[];s0line=[];s1line=[];s0v=[];s1v=[];dl=[];dv=[];ds=[]
    for h in range(24):
        line1=120. if h==18 else 85.; volt1=.93 if h==18 else 1.04
        s1src.append({'hour':h,'p_kw':14000 if h==18 else 9000})
        s0line.append({'hour':h,'line':'l1','phase':'A','loading_pct':95. if h==18 else 75.})
        s1line.append({'hour':h,'line':'l1','phase':'A','loading_pct':line1})
        s0v.append({'hour':h,'bus':'b1','phase':'A','v_pu':1.0})
        s1v.append({'hour':h,'bus':'b1','phase':'A','v_pu':volt1})
        dl.append({'hour':h,'line':'l1','phase':'A','dl_bess':-.04,'dl_v2g':-.03})
        dv.append({'hour':h,'bus':'b1','phase':'A','dv_bess':.00008,'dv_v2g':.00005})
        ds.append({'hour':h,'dp_bess':-1.1,'dp_v2g':-1.1})
    return dict(vehicles=vehicles,grid=grid,station=station,battery=battery,bess_bus='bess_bus',v2g_bus='v2g_bus',
                source_s1=pd.DataFrame(s1src),lines_s1=pd.DataFrame(s1line),volt_s1=pd.DataFrame(s1v),
                lines_s0=pd.DataFrame(s0line),volt_s0=pd.DataFrame(s0v),
                ds_lines=pd.DataFrame(dl),ds_volt=pd.DataFrame(dv),ds_source=pd.DataFrame(ds),
                fleet_s1_kw=np.zeros(24),fleet_s1_per_vehicle_kw=np.zeros((2,24)),
                independent_evcs_kw=np.zeros(24),discharge_window=(17,20),no_charge_window=(17,22),
                tariffs=np.array([.2]*24),remuneration=np.array([.5]*24),
                settings=OptSettings(solver_time_limit_s=20,mip_rel_gap=.02))


def test_sparse_milp_and_binary():
    m=MatrixBuilder();x=m.var(-10,0,10);z=m.var(0,0,1,True)
    m.row([(x,1),(z,-10)],hi=0)
    r,v,stats=m.solve(15,.01)
    assert abs(v[x]-10)<1e-6 and abs(v[z]-1)<1e-6
    assert stats['binary_variables']==1


def test_joint_bess_v2g_energy_and_network(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    result=solve_joint(**case,mode='S4')
    assert result['report']['status'].startswith('MILP_PRIMAL')
    assert abs(result['bess_energy_kwh'][0]-200)<1e-4
    assert abs(result['bess_energy_kwh'][-1]-200)<1e-4
    assert np.all((result['bess_charge_kw']<1e-4)|(result['bess_discharge_kw']<1e-4))
    assert result['bess_net_kw'][18]>0 or result['v2g_net_kw'][18]>0
    assert np.all(result['vehicle_power_kw'][:,[0,8,10,11,12,13]]<=1e-6)


def test_s2_fleet_fixed_per_vehicle(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    # This fixture has no original charging and a positive departure target,
    # so a no-V2G + no-charge baseline is intentionally infeasible.
    with pytest.raises(RuntimeError):
        solve_joint(**case,mode='S2')


def test_configured_screen_excludes_far_safe_buses(monkeypatch):
    install_v2g_stubs(monkeypatch)
    case=fixture_case()
    extra=[{'hour':h,'bus':'far','phase':'A','v_pu':1.00} for h in range(24)]
    ds_extra=[{'hour':h,'bus':'far','phase':'A','dv_bess':0.,'dv_v2g':0.} for h in range(24)]
    for k in ('volt_s0','volt_s1'):case[k]=pd.concat([case[k],pd.DataFrame(extra)],ignore_index=True)
    case['ds_volt']=pd.concat([case['ds_volt'],pd.DataFrame(ds_extra)],ignore_index=True)
    out=solve_joint(**case,mode='S4')
    assert out['report']['screen']['voltage_bus_phase']==1


def test_remove_old_fleet_before_inserting_new():
    from reotimizar_bess_v2g import build_scenario_injections
    old=pd.DataFrame([{'hour':h,'bus':'v2g','phase':p,'p_kw':-3.,'q_kvar':0.}
                      for h in range(24) for p in 'ABC'])
    s1=pd.DataFrame([{'hour':h,'bus':bus,'phase':phase,'p_kw':(-5. if bus=='v2g' else -20.),'q_kvar':0.}
                     for h in range(24) for bus in ['v2g','other'] for phase in 'ABC'])
    same={'v2g_net_kw':np.full(24,-9.),'bess_net_kw':np.zeros(24)}
    out=build_scenario_injections(s1,old,{'v2g_bus':'v2g','bess_bus':'bess'},same)
    result=out.merge(s1,on=['hour','bus','phase'],suffixes=('_new','_old'),how='left')
    assert np.max(abs(result.loc[result.bus!='bess','p_kw_new']-result.loc[result.bus!='bess','p_kw_old']))<1e-10
    alt={'v2g_net_kw':np.full(24,15.),'bess_net_kw':np.full(24,60.)}
    after=build_scenario_injections(s1,old,{'v2g_bus':'v2g','bess_bus':'bess'},alt)
    assert abs(after.p_kw.sum()-(s1.p_kw.sum()+24*(15+9+60)))<1e-8


def test_reference_shift_is_exact_for_linear_milp(monkeypatch):
    """A shift of the AC linearization point must NOT change the physics."""
    install_v2g_stubs(monkeypatch)
    baseline=fixture_case()
    a=solve_joint(**baseline,mode='S4')
    k_b=80.;k_v=30.
    shifted=fixture_case()
    shifted['lines_s1']=shifted['lines_s1'].copy()
    shifted['volt_s1']=shifted['volt_s1'].copy()
    shifted['source_s1']=shifted['source_s1'].copy()
    shifted['lines_s1']['loading_pct'] += -.04*k_b-.03*k_v
    shifted['volt_s1']['v_pu'] += .00008*k_b+.00005*k_v
    shifted['source_s1']['p_kw'] += -1.1*(k_b+k_v)
    b=solve_joint(**shifted,mode='S4',network_reference_bess_kw=np.full(24,k_b),
                  network_reference_fleet_kw=np.full(24,k_v))
    assert np.allclose(a['bess_net_kw'],b['bess_net_kw'],atol=1e-3)
    assert np.allclose(a['v2g_net_kw'],b['v2g_net_kw'],atol=1e-3)
    assert abs(a['report']['objective']-b['report']['objective'])<1e-2


def test_trust_region_respects_reference(monkeypatch):
    install_v2g_stubs(monkeypatch)
    inp=fixture_case()
    old=solve_joint(**inp,mode='S4')
    inp['settings']=OptSettings(solver_time_limit_s=20,bess_trust_kw=40.,v2g_trust_kw=10.)
    candidate=solve_joint(**inp,mode='S4',network_reference_bess_kw=old['bess_net_kw'],
                          network_reference_fleet_kw=old['v2g_net_kw'])
    assert np.max(np.abs(candidate['bess_net_kw']-old['bess_net_kw']))<=40.+1e-5
    assert np.max(np.abs(candidate['v2g_net_kw']-old['v2g_net_kw']))<=10.+1e-5


def test_invalid_trust_is_rejected(monkeypatch):
    install_v2g_stubs(monkeypatch)
    inp=fixture_case()
    inp['settings']=OptSettings(bess_trust_kw=0.)
    with pytest.raises(ValueError):solve_joint(**inp,mode='S4')
