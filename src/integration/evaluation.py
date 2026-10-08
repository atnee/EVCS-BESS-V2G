"""Electrical and operational metrics with explicit boundaries."""
import numpy as np

def evaluate(flow: dict, dt_h: float, tariff: float = 0.15) -> dict:
    source = flow["source"].p_kw.to_numpy()
    v = flow["voltages"].v_pu
    b = flow["branches"]
    thermal = b[b.physical_phase] if "physical_phase" in b else b
    return {"peak_import_kw":float(np.maximum(source,0).max()),
            "import_kwh":float(np.maximum(source,0).sum()*dt_h),
            "export_kwh":float(np.maximum(-source,0).sum()*dt_h),
            "loss_kwh":float(b.loss_kw.sum()*dt_h),
            "v_min_pu":float(v.min()),"v_max_pu":float(v.max()),
            "loading_max_pct":float(thermal.loading_pct.max()),
            "voltage_violation_samples":int(((v<.95)|(v>1.05)).sum()),
            "overload_samples":int((thermal.loading_pct>100).sum()),
            "energy_cost_usd":float(np.maximum(source,0).sum()*dt_h*tariff)}
