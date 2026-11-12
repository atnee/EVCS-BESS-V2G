import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rebuild.report import build_summary


def test_day_line_ids_distinct_from_hour_phase_events():
    # Line x was already overloaded at hour 0 in S0. A new overload at hour 1
    # and an additional phase at hour 1 are new events, NOT a new physical line.
    def frame(occs):
        rows=[]
        for h in (0,1):
            for line,ph in (('x','A'),('x','B'),('y','A')):
                rows.append(dict(hour=h,line=line,phase=ph,
                                 loading_pct=120. if (h,line,ph) in occs else 80.))
        return pd.DataFrame(rows)
    ref=frame({(0,'x','A')})
    target=frame({(0,'x','A'),(1,'x','A'),(1,'x','B'),(1,'y','A')})
    ctx={'settings':{'limits':{'v_min_pu':.95,'v_max_pu':1.05,'line_limit_pct':100},
                     'paper':{'tariff_usd_kwh':.15}}}
    def tables(branches):return {'source':pd.DataFrame({'p_kw':[100.], 'loss_kw':[1.]}),
        'voltages':pd.DataFrame({'v_pu':[1.0]}),'branches':branches,'regulators':pd.DataFrame()}
    summary,extras=build_summary({'S0':tables(ref),'S1':tables(target)},ctx,{},1.0)
    s=summary.set_index('scenario').loc['S1']
    assert s['new_overload_samples_vs_S0']==3
    assert s['new_overloaded_unique_lines_vs_S0']==1
    assert s['new_overloaded_unique_line_phases_vs_S0']==2
    assert s['lines_with_any_new_hour_phase_event_vs_S0']==2
    assert s['line_phases_with_any_new_hour_phase_event_vs_S0']==3
    assert s['persisting_overloaded_line_ids_vs_S0']==1
    assert len(extras['S1_new_vs_S0'])==3
