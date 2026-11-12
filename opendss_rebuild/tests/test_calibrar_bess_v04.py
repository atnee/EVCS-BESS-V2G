"""Pure-function tests: no OpenDSS needed."""
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from calibrar_bess_opendss import merge_delta,perturbation_rows


def test_perturbation_sign_and_phases():
    rows=perturbation_rows(18,'l3029502',200)
    assert set(rows.phase)==set('ABC')
    assert abs(rows.p_kw.sum()-200.)<1e-9
    assert (rows.q_kvar==0).all()
    assert set(rows.hour)=={18}


def test_line_derivative_sign():
    before=[{'hour':18,'line':'demo','phase':'A','current_a':140.,'loading_pct':110.,'ampacity_a':200.}]
    after=[{'hour':18,'line':'demo','phase':'A','current_a':132.,'loading_pct':105.,'ampacity_a':200.}]
    result=merge_delta(before,after,['hour','line','phase'],('current_a','loading_pct'),200.)
    assert result.iloc[0].delta_loading_pct==-5.
    assert result.iloc[0].d_loading_pct_per_kw==-0.025


def test_mismatch_is_rejected():
    before=[{'hour':18,'line':'demo','phase':'A','current_a':140.,'loading_pct':110.}]
    after=[{'hour':18,'line':'demo','phase':'B','current_a':132.,'loading_pct':105.}]
    try:
        merge_delta(before,after,['hour','line','phase'],('current_a','loading_pct'),200.)
    except ValueError:
        pass
    else:
        raise AssertionError('Missing phase must be rejected')
