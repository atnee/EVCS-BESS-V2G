"""Testes locais independentes de OpenDSS: chaves, guardas e CLI da V2.9."""
from __future__ import annotations
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from distributed_stochastic.redespachar_milp_v29 import (
    TARGET20, exact_key, additional_milp_guards, parser,
)


def make_context():
    branches=[{'hour':18,'line':'guard1','phase':'B','loading_pct':99.8},
              {'hour':15,'line':'ln6351510-1','phase':'C','loading_pct':109.3}]
    branches.extend({'hour':20,'line':name,'phase':'A','loading_pct':100.04+i/100}
                    for i,name in enumerate(TARGET20))
    voltages=[{'hour':18,'bus':f'bus{i}','phase':'A','v_pu':.937+i*.001}
              for i in range(10)]
    g=pd.DataFrame([{'hour':18,'line':'guard1','phase':'B','target_pct':99.9}])
    ctx={'guard_table':g,'focus_line':'ln6351510-1','focus_phase':'C'}
    ac={'branches':pd.DataFrame(branches),'voltages':pd.DataFrame(voltages)}
    args=SimpleNamespace(target_hour20_gain_pp=.003,target_hour15_gain_pp=.01,
              protect_n_buses_18h=8,protect_vmin_drop_pu=.00015)
    return ctx,ac,args


def test_guard_targets_are_independent_and_strict():
    ctx,ac,args=make_context()
    caps,floors,focus=additional_milp_guards(ctx,ac,args)
    assert caps[(18,'guard1','B')]==pytest.approx(99.92)
    assert len(caps)==4
    assert caps[(20,TARGET20[0],'A')]==pytest.approx(100.037)
    assert len(floors)==8
    assert floors[(18,'bus0','A')]==pytest.approx(.93685)
    assert focus[0]==(15,'ln6351510-1','C')
    assert focus[1]==pytest.approx(109.29)


def test_duplicate_ac_keys_never_silent():
    ctx,ac,args=make_context()
    ac['branches']=pd.concat([ac['branches'],ac['branches'].iloc[[0]]],ignore_index=True)
    with pytest.raises(ValueError,match='Duplicatas'):
        additional_milp_guards(ctx,ac,args)


def test_missing_critical_line_aborts():
    ctx,ac,args=make_context()
    ac['branches']=ac['branches'][ac['branches']['line']!=TARGET20[1]]
    with pytest.raises(ValueError,match='ausente'):
        additional_milp_guards(ctx,ac,args)


def test_missing_18h_buses_aborts():
    ctx,ac,args=make_context()
    ac['voltages']=ac['voltages'].head(5)
    with pytest.raises(ValueError,match='insuficiente'):
        additional_milp_guards(ctx,ac,args)


def test_nan_loading_aborts():
    ctx,ac,args=make_context()
    ac['branches'].loc[0,'loading_pct']=np.nan
    with pytest.raises(ValueError,match='nao finito'):
        exact_key(ac['branches'],['hour','line','phase'],(18,'guard1','B'),'loading_pct')


def test_v29_default_limits():
    cli=['--baseline','b','--v22-dir','v22','--v23-dir','v23',
         '--v24-dir','v24','--v25-dir','v25','--v26-dir','v26',
         '--v27-dir','v27','--v28-dir','v28','--v281-dir','v281',
         '--v281c-dir','v281c']
    args=parser().parse_args(cli)
    assert args.historical_voltage_budget_puh==pytest.approx(.15)
    assert args.voltage_budget_puh==pytest.approx(.03)
    assert args.max_iterations==1
    assert args.bess_trust_kw==100
    assert args.v2g_trust_kw==60


def test_not_allow_missing_arguments():
    with pytest.raises(SystemExit):
        parser().parse_args([])


def test_individual_milp_adds_joint_hard_line_and_voltage_rows(monkeypatch):
    """Stub apenas do solver; verifica se as restricoes eletricas sao adicionadas."""
    import sys
    import types
    from distributed_stochastic._milp_independente_v29 import make_milp_v29

    class FakeBuilder:
        instances=[]
        def __init__(self):
            self.n=0;self.rows=[];FakeBuilder.instances.append(self)
        def var(self, *args, **kwargs):
            j=self.n;self.n+=1;return j
        def row(self, cols, lo=None, hi=None):
            self.rows.append((cols,lo,hi))
        def solve(self, time_limit, mip_gap):
            return SimpleNamespace(status=0,message='synthetic',mip_gap=0.,fun=0.),np.zeros(self.n),{'mock':True}

    for module_name in ('rebuild','v2g'):
        monkeypatch.setitem(sys.modules,module_name,types.ModuleType(module_name))
    rmod=types.ModuleType('rebuild.joint_optimization')
    rmod.MatrixBuilder=FakeBuilder
    amod=types.ModuleType('v2g.availability')
    amod.session_steps=lambda T,a,d: [17]
    smod=types.ModuleType('v2g.strategies')
    smod.fleet_profile=lambda *a,**k: None
    for name,mod in (('rebuild.joint_optimization',rmod),
                     ('v2g.availability',amod),('v2g.strategies',smod)):
        monkeypatch.setitem(sys.modules,name,mod)
    bat=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,
                        soc_min=.1,soc_max=.9,eta_charge=.95,eta_discharge=.95)
    vehicle=SimpleNamespace(battery=bat,arrival=17,departure=18,
                            departure_target_kwh=15.,reserve_kwh=8.)
    team={'vehicles':[vehicle],'station':None,'grid':None,'ind':None}
    lines=pd.DataFrame([{'hour':15,'line':'focus','phase':'C','loading_pct':109.0},
                        {'hour':18,'line':'guard','phase':'A','loading_pct':99.70},
                        {'hour':20,'line':'h20','phase':'A','loading_pct':100.04}])
    volts=pd.DataFrame([{'hour':18,'bus':'bus0','phase':'A','v_pu':.937}])
    source=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':lines,'voltages':volts,'source':source}
    locs={('bess',sign):1e-4 for sign in (-1,1)}|{('station',sign):1e-4 for sign in (-1,1)}
    slopes={'lines':{(int(r.hour),str(r.line),str(r.phase)):locs for r in lines.itertuples()},
            'volts':{(18,'bus0','A'):locs},'source':{h:locs for h in range(24)}}
    result=make_milp_v29(team,['station'],{'bess':{'power_kw':100.,'energy_kwh':200.}},
        {'bess':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes,
        hard_line_caps={(18,'guard','A'):99.92,(20,'h20','A'):100.037},
        protected_voltage={(18,'bus0','A'):.93685},
        focus_target=((15,'focus','C'),108.99),peak_growth_kw=50.)
    assert result['hard_line_constraints']==2
    assert result['hard_voltage_constraints']==1
    rows=FakeBuilder.instances[-1].rows
    # Ha diversas linhas de penalizacao; estas tres diferencas sao os hard caps.
    assert any(hi==pytest.approx(.22) for _,lo,hi in rows if hi is not None)
    assert any(hi==pytest.approx(-.003) for _,lo,hi in rows if hi is not None)
    assert any(hi==pytest.approx(-.01) for _,lo,hi in rows if hi is not None)
    assert any(lo==pytest.approx(-.00015) for _,lo,hi in rows if lo is not None)
    assert len(rows)>100
