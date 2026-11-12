import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from distributed_stochastic.validar_viabilidade_ac import evaluate
import pandas as pd

def test_feasible():
    b=pd.DataFrame([{'hour':0,'line':'l','phase':'A','loading_pct_s0':80.,'loading_pct_ac':80.}])
    v=pd.DataFrame([{'hour':0,'bus':'n','phase':'A','v_pu_s0':1.0,'v_pu_ac':1.0}])
    summary,_,_=evaluate(b,v)
    assert summary['strict_pass'] and summary['relative_s0_no_aggravation_pass']

def test_preexisting_violation_improves_without_strict_pass():
    b=pd.DataFrame([{'hour':0,'line':'l','phase':'A','loading_pct_s0':110.,'loading_pct_ac':105.}])
    v=pd.DataFrame([{'hour':0,'bus':'n','phase':'A','v_pu_s0':0.93,'v_pu_ac':0.94}])
    s,_,_=evaluate(b,v)
    assert not s['strict_pass'] and s['relative_s0_no_aggravation_pass']

def test_aggravation_during_existing_violation_fails():
    b=pd.DataFrame([{'hour':0,'line':'l','phase':'A','loading_pct_s0':110.,'loading_pct_ac':111.}])
    v=pd.DataFrame([{'hour':0,'bus':'n','phase':'A','v_pu_s0':0.93,'v_pu_ac':0.92}])
    s,_,_=evaluate(b,v)
    assert not s['relative_s0_no_aggravation_pass']
    assert s['thermal_aggravations_relative_s0']==1
    assert s['voltage_aggravations_relative_s0']==1

def test_new_overload_identified():
    b=pd.DataFrame([{'hour':0,'line':'l','phase':'A','loading_pct_s0':90.,'loading_pct_ac':101.}])
    v=pd.DataFrame([{'hour':0,'bus':'n','phase':'A','v_pu_s0':1.0,'v_pu_ac':1.0}])
    s,_,_=evaluate(b,v)
    assert s['new_distinct_overloaded_lines_relative_s0']==1
    assert not s['relative_s0_no_aggravation_pass']

def test_preexisting_on_different_hour_not_new_distinct_line():
    b=pd.DataFrame([
      {"hour":0,"line":"l","phase":"A","loading_pct_s0":110.,"loading_pct_ac":90.},
      {"hour":1,"line":"l","phase":"A","loading_pct_s0":90.,"loading_pct_ac":105.},
    ])
    v=pd.DataFrame([
      {"hour":0,"bus":"n","phase":"A","v_pu_s0":1.,"v_pu_ac":1.},
      {"hour":1,"bus":"n","phase":"A","v_pu_s0":1.,"v_pu_ac":1.},
    ])
    s,_,_=evaluate(b,v)
    assert s['new_distinct_overloaded_lines_relative_s0']==0
    assert s['distinct_lines_with_new_hourly_overload_vs_s0']==1
