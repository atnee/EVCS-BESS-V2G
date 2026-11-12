"""Testes locais V2.9.3. Nao executa OpenDSS nem altera incumbente."""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from distributed_stochastic import redespachar_milp_v293 as m
from distributed_stochastic._milp_independente_v292 import make_milp_v292
from distributed_stochastic._milp_independente_v293 import make_milp_v293


def args():
    return SimpleNamespace(adaptive_recalibration=True, tap_change_threshold=12,
        max_recalibrations=1,max_diagnostic_solves=4,
        diagnostic_time_limit_s=25,progressive_cut_count=4)


def cuts():
    return {'lines':[(1.0,(23,'focus','C'),102.,.05)],
        'under':[(.003,(17,'low','A'),.950,.001)],
        'over':[(.004,(19,'high','C'),1.05,.001)],'summary':{}}


def test_infeasible_classifier_does_not_swallow_timeouts():
    assert m.is_reported_infeasible(RuntimeError('status=2; HiGHS model_status is Infeasible'))
    assert not m.is_reported_infeasible(RuntimeError('status=1; time limit no primal'))
    assert not m.is_reported_infeasible(RuntimeError('The problem is infeasible conceptually'))
    assert not m.is_reported_infeasible(TypeError('tuple indices must be integers'))


def test_immediate_recalibration_not_dependent_on_attempt_index():
    a=args()
    assert m.should_recalibrate(a,38,0,True)
    assert m.should_recalibrate(a,12,0,True)
    assert not m.should_recalibrate(a,11,0,True)
    assert not m.should_recalibrate(a,38,1,True)
    assert not m.should_recalibrate(a,38,0,False)
    a.adaptive_recalibration=False
    assert not m.should_recalibrate(a,38,0,True)


@pytest.mark.parametrize('group,want',[
    ('base',(0,0,0)),('thermal',(1,0,0)),('voltage',(0,1,1))])
def test_diagnostic_group_separation(group,want):
    z=m.group_cuts(cuts(),group)
    assert tuple(len(z[x]) for x in ('lines','under','over'))==want


def test_diagnostic_classification_not_iis():
    rows=[{'name':'BASE_SEM_APRENDIDOS','status':'MILP_PRIMAL_FOUND'},
          {'name':'APENAS_CORTES_TERMICOS','status':'MILP_INFEASIBLE'},
          {'name':'APENAS_CORTES_TENSAO','status':'MILP_PRIMAL_FOUND'}]
    assert m.classify_diagnostic(rows)=='THERMAL_LEARNED_GROUP_CONFLICT'
    rows[1]['status']='MILP_PRIMAL_FOUND'; rows[2]['status']='MILP_INFEASIBLE'
    assert m.classify_diagnostic(rows)=='VOLTAGE_LEARNED_GROUP_CONFLICT'
    rows[2]['status']='MILP_PRIMAL_FOUND'
    assert m.classify_diagnostic(rows)=='POSSIBLE_JOINT_GROUP_CONFLICT'
    rows[0]['status']='MILP_INFEASIBLE'
    assert m.classify_diagnostic(rows)=='BASE_OR_ENVELOPES_INFEASIBLE'


def test_diagnostic_calls_never_trigger_ac(monkeypatch,tmp_path):
    received=[]
    def fake_solve(*a,**kw):
        received.append(kw)
        if kw.get('diagnostic'):
            return {'solver_status':0,'mip_gap':0.,'diagnostic_slacks':{
                'learned_line':[{'key':[23,'focus','C'],'slack':.03}],
                'learned_under':[],'learned_over':[],
                'aggregate_thermal':[{'key':'abs','slack':.1}],
                'aggregate_voltage':[]}}
        return {'solver_status':0,'mip_gap':0.,'diagnostic_slacks':{}}
    monkeypatch.setattr(m,'solve_model',fake_solve)
    result=m.diagnose_infeasibility({},args(),{},None,None,cuts(),
        ({}, {}, {}),.5,tmp_path/'diag')
    assert result['diagnostic_only'] and result['never_run_AC']
    assert len(received)==4
    assert received[0]['diagnostic'] is True
    assert all(not kw['diagnostic'] for kw in received[1:])
    assert result['outcomes'][0]['elastic_nonzero']['learned_line']==1
    assert (tmp_path/'diag'/'diagnostic_summary_v293.json').is_file()


def test_voltage_guards_remain_anchored_to_incumbent(monkeypatch):
    calls=[]
    def fake_guards(ac,slopes,args,priority):
        calls.append(ac)
        return {(18,'x','A'):.939},{(19,'y','C'):1.057}, {'source':'inc'}
    monkeypatch.setattr(m.v292,'voltage_guards',fake_guards)
    inc={'ac':{'voltages':'REFERENCE'}}
    vf,vc,_=m.updated_voltage_guards(inc,None,args(),[],{(18,'x','A'):.94})
    assert calls==[inc['ac']]
    assert vf[(18,'x','A')]==.94 and (19,'y','C') in vc


class FakeBuilder:
    instances=[]
    def __init__(self):
        self.rows=[];self.n=0;self.instances.append(self)
    def var(self,*args,**kwargs):
        i=self.n;self.n+=1;return i
    def row(self,terms,lo=None,hi=None):
        self.rows.append((terms,lo,hi))
    def solve(self,time_limit,mip_gap):
        return SimpleNamespace(status=0,message='mock',mip_gap=0.,fun=0.), \
            np.zeros(self.n), {'rows':len(self.rows),'variables':self.n}


def stub_deps(monkeypatch):
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    rebuild=types.ModuleType('rebuild.joint_optimization')
    rebuild.MatrixBuilder=FakeBuilder
    monkeypatch.setitem(sys.modules,'rebuild.joint_optimization',rebuild)
    monkeypatch.setitem(sys.modules,'v2g',types.ModuleType('v2g'))
    availability=types.ModuleType('v2g.availability')
    availability.session_steps=lambda *a:[17]
    monkeypatch.setitem(sys.modules,'v2g.availability',availability)
    strategies=types.ModuleType('v2g.strategies')
    strategies.fleet_profile=lambda *a:None
    monkeypatch.setitem(sys.modules,'v2g.strategies',strategies)


def miniature_case():
    batt=SimpleNamespace(power_kw=11.,soc_initial=.5,capacity_kwh=40.,
       soc_min=.1,soc_max=.9,eta_charge=.95,eta_discharge=.95)
    car=SimpleNamespace(battery=batt,arrival=17,departure=18,
       departure_target_kwh=15.,reserve_kwh=8.)
    team={'vehicles':[car],'station':None,'grid':None,'ind':None}
    loads=pd.DataFrame([{'hour':23,'line':'focus','phase':'C','loading_pct':104.}])
    volts=pd.DataFrame([{'hour':17,'bus':'low','phase':'A','v_pu':.94},
                        {'hour':19,'bus':'high','phase':'C','v_pu':1.056}])
    source=pd.DataFrame([{'hour':h,'p_kw':1000.} for h in range(24)])
    ac={'branches':loads,'voltages':volts,'source':source}
    coeff={('s',1):.001,('s',-1):.001,('m',1):.001,('m',-1):.001}
    slopes={'lines':{(23,'focus','C'):coeff},
           'volts':{(17,'low','A'):coeff,(19,'high','C'):coeff},
           'source':{h:coeff for h in range(24)}}
    base=(team,['m'],{'s':{'power_kw':100.,'energy_kwh':200.}},
          {'s':np.zeros(24)},np.zeros((1,24)),ac,ac,slopes)
    kwargs={'hard_line_caps':{(23,'focus','C'):103.9},
          'protected_voltage':{(17,'low','A'):.939},
          'voltage_ceilings':{(19,'high','C'):1.057},
          'absolute_thermal_target':4.,'strict_voltage_budget_puh':.02}
    return base,kwargs


def test_official_milp_is_unmodified_in_constraint_count(monkeypatch):
    stub_deps(monkeypatch)
    base,kw=miniature_case()
    b=make_milp_v292(*base,**kw)
    model_b=FakeBuilder.instances[-1]
    a=make_milp_v293(*base,**kw)
    model_a=FakeBuilder.instances[-1]
    assert model_b.n==model_a.n
    assert len(model_b.rows)==len(model_a.rows)
    assert a['diagnostic_mode'] is False
    assert a['diagnostic_slacks']=={}


def test_elastic_additions_are_diagnostic_only(monkeypatch):
    stub_deps(monkeypatch)
    base,kw=miniature_case()
    keyline=(23,'focus','C');keylow=(17,'low','A');keyhigh=(19,'high','C')
    with pytest.raises(ValueError,match='EXCLUSIVAS'):
        make_milp_v293(*base,**kw,elastic_line_keys=[keyline])
    z=make_milp_v293(*base,**kw,diagnostic_elastic=True,
        elastic_line_keys=[keyline],elastic_under_keys=[keylow],
        elastic_over_keys=[keyhigh],elastic_absolute_envelope=True,
        elastic_voltage_envelope=True)
    assert z['diagnostic_mode']
    assert {k:len(v) for k,v in z['diagnostic_slacks'].items()}=={
        'learned_line':1,'learned_under':1,'learned_over':1,
        'aggregate_thermal':1,'aggregate_voltage':1}


def test_parser_creates_unique_v293_controls():
    parser=m.parser()
    names={o.dest for o in parser._actions}
    assert {'v292_dir','max_diagnostic_solves','diagnostic_time_limit_s','tap_change_threshold'}<=names
    assert m.plans_v293(4)[1]['cut_mode']=='none'
