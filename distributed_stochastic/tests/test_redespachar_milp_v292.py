"""Testes da V2.9.2: nenhuma execucao OpenDSS no pytest."""
from __future__ import annotations
import sys,types
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from distributed_stochastic import redespachar_milp_v292 as m
from distributed_stochastic._milp_independente_v292 import make_milp_v292


def args():
    return SimpleNamespace(max_line_growth_pp=.02,cut_safety_pp=.03,
        max_ac_line_bias_pp=2.,max_ac_voltage_bias_pu=.003,
        cut_voltage_safety_pu=.00005,max_ac_cuts=16,
        dynamic_voltage_drop_pu=.00015,max_ac_voltage_cuts=16,
        max_dynamic_voltage_keys=160,near_voltage_margin_pu=.002,
        progressive_cut_count=4,max_attempts=4)


def make_ac(values,load=100.,tap=0):
    return {'voltages':pd.DataFrame([{'hour':h,'bus':bus,'phase':phase,'v_pu':v}
       for h,bus,phase,v in values]),
       'branches':pd.DataFrame([{'hour':9,'line':'critical','phase':'C','loading_pct':load}]),
       'regulators':pd.DataFrame([{'hour':9,'regulator':'regA','tap':tap}])}


def test_dynamic_voltage_guards_keep_original_violations():
    a=make_ac([(9,'low','A',.938),(17,'near','A',.9502),
               (19,'high','C',1.057),(21,'safe','C',1.03)])
    keys=[(int(r.hour),r.bus,r.phase) for r in a['voltages'].itertuples()]
    slopes={'volts':{key:{} for key in keys}}
    floors,ceilings,diag=m.voltage_guards(a,slopes,args(),priority_keys=[(21,'safe','C')])
    assert floors[(9,'low','A')]==pytest.approx(.93785)
    assert floors[(17,'near','A')]==pytest.approx(.94985)
    assert ceilings[(19,'high','C')]==pytest.approx(1.05715)
    assert (21,'safe','C') in ceilings
    assert diag['forced']==1


def test_forced_missing_sensitivity_fails_closed():
    a=make_ac([(1,'x','A',.95)])
    with pytest.raises(ValueError,match='sem sensibilidade'):
        m.voltage_guards(a,{'volts':{}},args(),priority_keys=[(1,'x','A')])


def test_full_day_both_voltage_sides_and_new_thermal_cut():
    base=make_ac([(17,'u','A',.9502),(9,'o','A',1.0498)],load=99.9)
    trial=make_ac([(17,'u','A',.9495),(9,'o','A',1.0503)],load=100.4)
    ctx={'state':{'places':[],'cap':{'s':{}}}}
    inc={'bess':{'s':np.zeros(24)},'cars':np.zeros((0,24)),'ac':base}
    prop={'bess':{'s':np.ones(24)},'cars':np.zeros((0,24))}
    coeff={('s',1):0.,('s',-1):0.}
    slopes={'lines':{(9,'critical','C'):coeff},
            'volts':{(17,'u','A'):coeff,(9,'o','A'):coeff}}
    cuts=m.full_ac_cuts(ctx,inc,prop,trial,slopes,args())
    assert cuts['summary']=={'line_candidates':1,'under_candidates':1,
                              'over_candidates':1,'missing_sensitivities':0}
    assert cuts['under'][0][1]==(17,'u','A')
    assert cuts['over'][0][1]==(9,'o','A')
    assert cuts['lines'][0][1]==(9,'critical','C')
    caps,floors,ceilings,lb,vb=m.cut_settings(cuts,{}, {}, {},args())
    assert (17,'u','A') in floors and (9,'o','A') in ceilings
    assert caps[(9,'critical','C')]==pytest.approx(99.999)
    assert lb[(9,'critical','C')]>0 and len(vb)==2


def test_new_violations_first_in_topn():
    a=args();a.max_ac_voltage_cuts=1
    b=make_ac([(5,'new','A',.9501),(9,'old','A',.91)])
    z=make_ac([(5,'new','A',.9499),(9,'old','A',.909)])
    ctx={'state':{'places':[],'cap':{'s':{}}}}
    inc={'bess':{'s':np.zeros(24)},'cars':np.zeros((0,24)),'ac':b}
    prop={'bess':{'s':np.ones(24)},'cars':np.zeros((0,24))}
    coeff={('s',1):0.,('s',-1):0.}
    slopes={'lines':{(9,'critical','C'):coeff},
            'volts':{(5,'new','A'):coeff,(9,'old','A'):coeff}}
    got=m.full_ac_cuts(ctx,inc,prop,z,slopes,a)
    assert got['under'][0][1]==(5,'new','A')


def test_ablations_keep_base_guards():
    a=args()
    plans=m.experiment_plans(a)
    assert plans[0]['trust']==plans[2]['trust'] and plans[0]['cuts']!=plans[2]['cuts']
    assert plans[1]['trust']<plans[0]['trust'] and plans[1]['cuts']=='none'
    cuts={'lines':[(2.,(9,'l','C'),99.98,.1)],'under':[],'over':[], 'summary':{}}
    base={(20,'original','A'):99.9}
    caps,*_=m.cut_settings(cuts,base,{}, {},a,limit=0)
    assert caps==base
    caps,*_=m.cut_settings(cuts,base,{}, {},a,limit=None)
    assert caps[(20,'original','A')]==99.9 and (9,'l','C') in caps


def test_changed_taps_and_duplicate_guards():
    a=make_ac([(0,'x','A',.96)],tap=0)
    b=make_ac([(0,'x','A',.96)],tap=1)
    assert m.changed_taps(a,b)==1
    b['regulators']=pd.concat([b['regulators'],b['regulators']])
    with pytest.raises(ValueError,match='repetidas'):
        m.changed_taps(a,b)


def test_voltage_ceiling_in_milp(monkeypatch):
    class FakeBuilder:
        instances=[]
        def __init__(self):self.n=0;self.rows=[];self.instances.append(self)
        def var(self,*a,**kw):i=self.n;self.n+=1;return i
        def row(self,cols,lo=None,hi=None):self.rows.append((cols,lo,hi))
        def solve(self,time_limit,mip_gap):
            return SimpleNamespace(status=0,message='mock',mip_gap=0.,fun=0.),\
                np.zeros(self.n),{'rows':len(self.rows)}
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    mm=types.ModuleType('rebuild.joint_optimization');mm.MatrixBuilder=FakeBuilder
    monkeypatch.setitem(sys.modules,'rebuild.joint_optimization',mm)
    monkeypatch.setitem(sys.modules,'v2g',types.ModuleType('v2g'))
    av=types.ModuleType('v2g.availability');av.session_steps=lambda *a:[17]
    monkeypatch.setitem(sys.modules,'v2g.availability',av)
    st=types.ModuleType('v2g.strategies');st.fleet_profile=lambda *a:None
    monkeypatch.setitem(sys.modules,'v2g.strategies',st)
    battery=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,soc_min=.1,
                        soc_max=.9,eta_charge=.95,eta_discharge=.95)
    car=SimpleNamespace(battery=battery,arrival=17,departure=18,
        departure_target_kwh=15.,reserve_kwh=8.)
    team={'vehicles':[car],'station':None,'grid':None,'ind':None}
    lines=pd.DataFrame([{'hour':23,'line':'focus','phase':'C','loading_pct':104.}])
    volts=pd.DataFrame([{'hour':9,'bus':'high','phase':'A','v_pu':1.052}])
    source=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':lines,'voltages':volts,'source':source}
    coeff={('s',1):.001,('s',-1):.001,('m',1):.001,('m',-1):.001}
    slopes={'lines':{(23,'focus','C'):coeff},'volts':{(9,'high','A'):coeff},
            'source':{h:coeff for h in range(24)}}
    out=make_milp_v292(team,['m'],{'s':{'power_kw':100.,'energy_kwh':200.}},
        {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes,
        voltage_ceilings={(9,'high','A'):1.0521},
        absolute_thermal_target=4.1,strict_voltage_budget_puh=.004)
    assert out['hard_voltage_ceiling_constraints']==1
    assert any(hi==pytest.approx(.0001) for _,_,hi in FakeBuilder.instances[-1].rows if hi is not None)
    with pytest.raises(ValueError,match='Mesma chave'):
        make_milp_v292(team,['m'],{'s':{'power_kw':100.,'energy_kwh':200.}},
           {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes,
           protected_voltage={(9,'high','A'):1.},
           voltage_ceilings={(9,'high','A'):1.1})
