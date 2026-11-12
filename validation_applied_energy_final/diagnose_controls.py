"""Reproduce the 250-vehicle control failure with capacitor event logging."""
import json
import os
import traceback
import pandas as pd
from audit import B,OUT
from check_alternatives import ODSSEngine,error_checked,load_context
from improve import save


def main():
    folder=OUT/'improvement_frozen/participation_250_control_log';folder.mkdir(exist_ok=False)
    cwd=os.getcwd()
    engine=ODSSEngine(load_context(B,B/'opendss_rebuild'),mode='original',ampacity='study')
    result={}
    try:
        engine.compile()
        error_checked(engine.dss,f'Set DataPath="{folder.as_posix()}"')
        error_checked(engine.dss,'BatchEdit CapControl..* eventlog=yes')
        p=pd.read_csv(OUT/'improvement_experiment/participation_250/retry_01/AC/injections_reoptimized.csv')
        engine.define_assets(p)
        error_checked(engine.dss,'BatchEdit RegControl..* enabled=no')
        taps=pd.read_csv(OUT/'exact_profile_pilot/S0_auto/regulators.csv')
        for r in taps[taps.hour==0].itertuples():
            engine.dss.RegControls.Name(r.regulator);engine.dss.RegControls.TapNumber(int(r.tap))
        engine.solve_hour(0,float(engine.ctx['multipliers'][0]),p)
        result['status']='CONVERGED'
    except Exception:
        result.update(status='CONTROL_ITERATION_FAILURE',traceback=traceback.format_exc())
    finally:
        result['event_log']=list(engine.dss.Solution.EventLog())
        result['control_iterations']=int(engine.dss.Solution.ControlIterations())
        save(folder/'diagnostic.json',result)
        os.chdir(cwd)
    print(result['status'],result['control_iterations'],result['event_log'][-10:])


if __name__=='__main__':main()
