"""Testes V2.9.1 independentes de solver real e de OpenDSS."""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from distributed_stochastic.redespachar_milp_v291 import (
    TARGET23, absolute_thermal, strict_voltage, parse_slopes, gate_v291,
    predict_value, learn_cuts, diff_injections, parser,
)
from distributed_stochastic._milp_independente_v291 import make_milp_v291


def parse_cli():
    return parser().parse_args([
      '--baseline','b','--v22-dir','v22','--v23-dir','v23','--v24-dir','v24',
      '--v25-dir','v25','--v26-dir','v26','--v27-dir','v27','--v28-dir','v28',
      '--v281-dir','v281','--v281c-dir','v281c','--v29-dir','v29'])


def test_cli_and_default_safety():
    x=parse_cli()
    assert x.max_added_overload_samples==0
    assert x.target_hour23_gain_pp==pytest.approx(.02)
    assert x.predicted_absolute_gain_pph>0
    assert x.strict_voltage_budget_puh>0
    assert x.reuse_v29_calibration is False


def test_metrics_are_strict_absolute():
    ac={'branches':pd.DataFrame({'loading_pct':[80.,99.99,100.1,110.]}),
        'voltages':pd.DataFrame({'v_pu':[.937,.95,1.05,1.06]})}
    assert absolute_thermal(ac)==pytest.approx(10.1)
    assert strict_voltage(ac)==pytest.approx(.023)


def test_prediction_signed_direction_is_not_reversed():
    coeff={('s',1):.5,('s',-1):-.2}
    changes={'s':np.array([10.,-10.])}
    assert predict_value((0,'x','A'),coeff,100,changes)==pytest.approx(105.)
    assert predict_value((1,'x','A'),coeff,100,changes)==pytest.approx(102.)


def test_rejects_worsening_absolute_even_if_relative_improves(monkeypatch):
    from distributed_stochastic import redespachar_milp_v291 as mod
    monkeypatch.setattr(mod.v281,'dual_voltage_budget_gate',
                        lambda ctx,ac,metrics,current,args:(True,[],{}))
    base={'orig':{'strict_thermal_pp_h':150.,'strict_overload_samples':46,
                  'max_loading_pct':109.3,'strict_voltage_pu_h':2.1},
          'policy':{}}
    cand={'orig':{'strict_thermal_pp_h':153.,'strict_overload_samples':51,
                  'max_loading_pct':110.3,'strict_voltage_pu_h':2.09},
          'policy':{}}
    rows=[]
    for h,line,pct in [(23,'ln6351510-1',106.56),(20,'ln6350556-2',100.04),
                       (20,'ln6350556-3',100.04),(20,'ln5835160-1',100.04)]:
        rows.append({'hour':h,'line':line,'phase':'C' if h==23 else 'A',
                     'loading_pct':pct})
    acbase={'branches':pd.DataFrame(rows)}
    rows_new=[dict(r) for r in rows];rows_new[0]['loading_pct']=110.2
    acnew={'branches':pd.DataFrame(rows_new)}
    args=SimpleNamespace(min_actual_absolute_gain_pph=.05,max_line_growth_pp=.02,
        strict_voltage_budget_puh=.02,min_actual_hour23_gain_pp=.005,
        hour20_max_worsening_pp=.02)
    ok,reasons,audit=gate_v291({}, {'metrics':base,'ac':acbase},acnew,cand,args)
    assert not ok
    assert 'NO_ABSOLUTE_THERMAL_REDUCTION' in reasons
    assert 'OVERLOAD_SAMPLE_GROWTH_VS_INCUMBENT' in reasons
    assert 'HOUR23_FOCUS_NOT_IMPROVED' in reasons
    assert 'GLOBAL_LOADING_GROWTH' in reasons


def test_24h_changes_and_ac_cuts():
    ac0={'branches':pd.DataFrame([{'hour':23,'line':'ln6351510-1','phase':'C',
                                  'loading_pct':106.5}]),
         'voltages':pd.DataFrame([{'hour':18,'bus':'b','phase':'A','v_pu':.938}])}
    ac1={'branches':pd.DataFrame([{'hour':23,'line':'ln6351510-1','phase':'C',
                                  'loading_pct':109.5}]),
         'voltages':pd.DataFrame([{'hour':18,'bus':'b','phase':'A','v_pu':.937}])}
    ctx={'state':{'places':['m'],'cap':{'s':{'power_kw':100}}}}
    before={'bess':{'s':np.zeros(24)},'cars':np.zeros((1,24)),'ac':ac0}
    after={'bess':{'s':np.ones(24)},'cars':np.zeros((1,24))}
    changes=diff_injections(ctx,before,after)
    assert changes['s'][23]==1
    slopes={'lines':{TARGET23:{('s',1):.2,('s',-1):.1,('m',1):0.,('m',-1):0.}},
            'volts':{(18,'b','A'):{('s',1):.001,('s',-1):.001,('m',1):0.,('m',-1):0.}}}
    caps={TARGET23:106.48};floors={(18,'b','A'):.93785}
    args=SimpleNamespace(max_line_growth_pp=.02,target_hour23_gain_pp=.02,
           cut_safety_pp=.03,max_ac_line_bias_pp=2.0,
           cut_voltage_safety_pu=.00005,max_ac_voltage_bias_pu=.003,
           max_ac_cuts=8)
    lb,vb,diag=learn_cuts(ctx,before,after,ac1,slopes,caps,floors,args)
    assert TARGET23 in lb and lb[TARGET23]>0
    assert (18,'b','A') in vb and vb[(18,'b','A')]>0
    assert diag['critical_lines']==1 and diag['critical_voltages']==1


def test_synthetic_milp_envelopes(monkeypatch):
    """Verifica explicitamente as novas linhas no MatrixBuilder."""
    import sys,types
    class FakeBuilder:
        instances=[]
        def __init__(self):self.n=0;self.rows=[];self.cost=[];self.instances.append(self)
        def var(self,*args,**kw):
            i=self.n;self.n+=1;self.cost.append(args[0]);return i
        def row(self,cols,lo=None,hi=None):self.rows.append((cols,lo,hi))
        def solve(self,time_limit,mip_gap):
            return SimpleNamespace(status=0,message='mock',mip_gap=0.,fun=0.),\
                np.zeros(self.n),{'rows':len(self.rows)}
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    m=types.ModuleType('rebuild.joint_optimization');m.MatrixBuilder=FakeBuilder
    monkeypatch.setitem(sys.modules,'rebuild.joint_optimization',m)
    monkeypatch.setitem(sys.modules,'v2g',types.ModuleType('v2g'))
    am=types.ModuleType('v2g.availability');am.session_steps=lambda *a:[17]
    monkeypatch.setitem(sys.modules,'v2g.availability',am)
    sm=types.ModuleType('v2g.strategies');sm.fleet_profile=lambda *a:None
    monkeypatch.setitem(sys.modules,'v2g.strategies',sm)
    b=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,soc_min=.1,
                      soc_max=.9,eta_charge=.95,eta_discharge=.95)
    v=SimpleNamespace(battery=b,arrival=17,departure=18,
                      departure_target_kwh=15.,reserve_kwh=8.)
    team={'vehicles':[v],'station':None,'grid':None,'ind':None}
    branches=pd.DataFrame([{'hour':23,'line':'focus23','phase':'C','loading_pct':104.},
                           {'hour':20,'line':'safe','phase':'A','loading_pct':99.98}])
    volts=pd.DataFrame([{'hour':18,'bus':'bad','phase':'A','v_pu':.937}])
    source=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':branches,'voltages':volts,'source':source}
    coef={('s',1):.001,('s',-1):.001,('m',1):.001,('m',-1):.001}
    slopes={'lines':{(int(r.hour),r.line,r.phase):coef for r in branches.itertuples()},
            'volts':{(18,'bad','A'):coef},'source':{h:coef for h in range(24)}}
    out=make_milp_v291(team,['m'],{'s':{'power_kw':100.,'energy_kwh':200.}},
           {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes,
           hard_line_caps={(23,'focus23','C'):103.98},
           protected_voltage={(18,'bad','A'):.93685},
           absolute_thermal_target=3.95,strict_voltage_budget_puh=.02)
    assert out['absolute_thermal_envelope_rows']==2
    assert out['absolute_voltage_envelope_rows']==2
    rows=FakeBuilder.instances[-1].rows
    # Meta absoluta exige soma de folgas <= 3.95, nao apenas agravamento.
    assert any(hi==pytest.approx(3.95) for _,_,hi in rows if hi is not None)
    assert any(hi==pytest.approx(.02) for _,_,hi in rows if hi is not None)
    assert any(hi==pytest.approx(.015) for _,_,hi in rows if hi is not None)


def test_missing_overload_sensitivity_aborts(monkeypatch):
    # A seguranca falha fechada se a calibracao omitir qualquer amostra > 100%.
    import sys,types
    class Noop:
        def var(self,*a,**k):return 0
        def row(self,*a,**k):pass
        def solve(self,*a,**k):raise AssertionError('Nao deveria chegar ao solver')
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    r=types.ModuleType('rebuild.joint_optimization');r.MatrixBuilder=Noop
    monkeypatch.setitem(sys.modules,'rebuild.joint_optimization',r)
    monkeypatch.setitem(sys.modules,'v2g',types.ModuleType('v2g'))
    a=types.ModuleType('v2g.availability');a.session_steps=lambda *a:[17]
    monkeypatch.setitem(sys.modules,'v2g.availability',a)
    s=types.ModuleType('v2g.strategies');s.fleet_profile=lambda *a:None
    monkeypatch.setitem(sys.modules,'v2g.strategies',s)
    b=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,soc_min=.1,
                      soc_max=.9,eta_charge=.95,eta_discharge=.95)
    v=SimpleNamespace(battery=b,arrival=17,departure=18,departure_target_kwh=15.,reserve_kwh=8.)
    lines=pd.DataFrame([{'hour':23,'line':'miss','phase':'C','loading_pct':105.}]);
    vols=pd.DataFrame([{'hour':18,'bus':'fine','phase':'A','v_pu':1.}]);
    src=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':lines,'voltages':vols,'source':src}
    with pytest.raises(RuntimeError,match='omitiu'):
        make_milp_v291({'vehicles':[v],'station':None,'grid':None,'ind':None},['m'],
            {'s':{'power_kw':100.,'energy_kwh':200.}},
            {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,
            {'lines':{},'volts':{},'source':{h:{('s',1):0.,('s',-1):0.,('m',1):0.,('m',-1):0.} for h in range(24)}},
            absolute_thermal_target=4.)
