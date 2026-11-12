"""Regressão para a V2.4.1: sensores limpos por hora e barreira de integridade.

Mock de engine: verifica lógica, não substitui OpenDSS trifásico real.
"""
import sys
import types
import numpy as np
import pandas as pd
import pytest
from distributed_stochastic import coordenar_controles_v24 as v24

class FakeDSS:
    class Solution:
        @staticmethod
        def Converged():
            return True

class FakeEngine:
    def __init__(self):
        self.dss = FakeDSS()
        self.current_kw = 0.0
        self.hour = None
    def update_assets(self, rows):
        self.current_kw = float(rows.p_kw.sum())
    def solve_hour(self, h, mult, profiles):
        self.hour = h
        self.update_assets(profiles.loc[profiles.hour == h])
        return self.read_results(h)
    def read_results(self, h):
        p = self.current_kw
        return {
            'branches':[{'hour':h,'line':'line_x','phase':'A','loading_pct':99.0-0.01*p}],
            'voltages':[{'hour':h,'bus':'bus_x','phase':'A','v_pu':0.951+0.0001*p}],
            'source':[{'hour':h,'p_kw':1000.0-p}],
            'regulators':[{'hour':h,'regulator':'reg_a','tap':3}],
            'capacitors':[{'hour':h,'capacitor':'cap_a','states':'[1]'}],
        }


def inputs():
    reference = {name:[] for name in ('branches','voltages','source','regulators','capacitors')}
    fake = FakeEngine()
    for h in range(24):
        for key,rows in fake.read_results(h).items():
            reference[key].extend(rows)
    reference = {k:pd.DataFrame(v) for k,v in reference.items()}
    profiles = pd.DataFrame([
        {'hour':h,'bus':'bess','phase':phase,'p_kw':0.,'q_kvar':0.}
        for h in range(24) for phase in 'ABC'])
    return profiles, reference


def patch_engine(monkeypatch):
    engines=[]
    def factory(repo, profiles, caps):
        eng=FakeEngine();engines.append(eng)
        return {'multipliers':[1.]*24},eng
    monkeypatch.setattr(v24.v22,'make_engine',factory)
    module=types.ModuleType('rebuild.engine')
    module.error_checked=lambda dss, cmd: None
    monkeypatch.setitem(sys.modules,'rebuild',types.ModuleType('rebuild'))
    monkeypatch.setitem(sys.modules,'rebuild.engine',module)
    return engines


def test_replay_each_hour_from_fresh_engine(monkeypatch,tmp_path):
    engines=patch_engine(monkeypatch)
    profiles,reference=inputs()
    result=v24.calibrate_isolated_v24(tmp_path,profiles,reference,reference,{'bess':500.},100.,tmp_path/'out')
    assert len(engines)==24
    assert result['errors']['voltages']==0.
    assert len(result['lines'])==24 and len(result['volts'])==24
    assert all(set(slopes)=={('bess',1),('bess',-1)} for slopes in result['lines'].values())
    assert np.isclose(result['lines'][(12,'line_x','A')][('bess',1)],-0.01)
    assert np.isclose(result['volts'][(12,'bus_x','A')][('bess',-1)],.0001)
    assert (tmp_path/'out'/'replay_diagnostico_por_hora.csv').is_file()
    assert (tmp_path/'out'/'derivadas_linhas_v22.csv.gz').is_file()
    assert (tmp_path/'out'/'calibration_manifest.json').is_file()


def test_reject_actual_baseline_divergence(monkeypatch,tmp_path):
    engines=patch_engine(monkeypatch)
    profiles,reference=inputs()
    reference['voltages'].loc[reference['voltages'].hour==7,'v_pu']+=.00007
    with pytest.raises(RuntimeError,match='h=07: replay LIMPO divergente'):
        v24.calibrate_isolated_v24(tmp_path,profiles,reference,reference,{'bess':500.},100.,tmp_path/'out')
    assert len(engines)==8  # abort at first invalid hour
    x=pd.read_csv(tmp_path/'out'/'replay_diagnostico_por_hora.csv')
    assert x.hour.max()==7
    assert x.loc[x.hour==7,'voltages_max_abs_error'].iloc[0] > 2e-5
    assert not (tmp_path/'out'/'calibration_manifest.json').exists()
