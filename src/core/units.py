"""Canonical units: kW, kvar, kWh, hours, ohms, volts and declared currency."""
import numpy as np

def injection_to_consumption(p):
    """Signed net consumption = minus signed net injection."""
    return -np.asarray(p, dtype=float)

def energy_kwh(p_kw, dt_h: float) -> float:
    if not np.isfinite(dt_h) or dt_h <= 0:
        raise ValueError("dt_h must be finite and positive")
    p = np.asarray(p_kw, dtype=float)
    if not np.isfinite(p).all():
        raise ValueError("Non-finite power")
    return float(p.sum() * dt_h)
