import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pytest
from rebuild.contracts import canonical_profile,energy_metrics,new_overloads_per_hour


def sample_profile():
    dates=pd.date_range("2026-01-01",periods=24,freq="h",tz="America/Belem")
    return pd.DataFrame({"time":dates,"bus":["m1069504"]*24,
                         "phase":["A"]*24,"p_kw":np.ones(24),"q_kvar":np.zeros(24)})


def test_good_profile():
    r=canonical_profile(sample_profile(),[18],{"m1069504":set("ABC")})
    assert len(r)==1 and r.iloc[0].hour==18


def test_missing_physical_phase_rejected():
    data=sample_profile();data["phase"]="C"
    with pytest.raises(ValueError,match="inexistente"):
        canonical_profile(data,[18],{"m1069504":set("A")})


def test_multiply_asset_profile_not_allowed():
    data=sample_profile();data=pd.concat([data,data.iloc[[1]]])
    with pytest.raises(ValueError,match="duplicados"):
        canonical_profile(data,[18],{"m1069504":set("ABC")})


def test_metrics_and_new_overloads():
    src=pd.DataFrame({"hour":[0,1],"p_kw":[10,15],"loss_kw":[1,2]})
    volts=pd.DataFrame({"v_pu":[.94,1.0,1.06]})
    bran=pd.DataFrame({"hour":[0,0,1],"line":["L1","L2","L1"],
                       "phase":["A","B","A"],"loading_pct":[110,80,90]})
    m=energy_metrics(src,volts,bran,1)
    assert m["loss_kwh"]==3
    assert m["overloaded_unique_lines"]==1
    assert m["voltage_violation_samples"]==2
    base=bran.assign(loading_pct=[80,80,80])
    delta=new_overloads_per_hour(bran,base)
    assert delta[["hour","line","phase"]].values.tolist()==[[0,"L1","A"]]
