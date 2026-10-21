"""One connection session per vehicle; departure index is exclusive.

Same-day session: arrival < departure. Overnight session: departure < arrival, connected from the
arrival to the end of the horizon and from its start to the departure. The day is cyclic: the morning
part is the end of last night's session, assumed equal to tonight's (steady state)."""
import numpy as np

def check_session(steps: int, arrival: int, departure: int):
    if not all(isinstance(x, int) for x in (steps, arrival, departure)):
        raise ValueError("Invalid arrival/departure indices")
    if not (0 <= arrival < departure <= steps or 0 < departure < arrival < steps):
        raise ValueError("Invalid arrival/departure indices")

def availability(steps: int, arrival: int, departure: int) -> np.ndarray:
    check_session(steps, arrival, departure)
    t = np.arange(steps)
    if arrival < departure:
        return (t >= arrival) & (t < departure)
    return (t >= arrival) | (t < departure)

def session_steps(steps: int, arrival: int, departure: int) -> list[int]:
    """Connected steps in chronological order of the session (overnight: evening, then morning)."""
    check_session(steps, arrival, departure)
    if arrival < departure:
        return list(range(arrival, departure))
    return list(range(arrival, steps))+list(range(departure))

def session_energy(power_kw, battery, arrival: int, departure: int, dt_h: float) -> np.ndarray:
    """T+1 battery energies [kWh] for a power row (positive = discharge).

    Same-day: starts at the arrival energy and stays constant while away. Overnight: the session starts
    at the arrival energy, the energy at the end of the horizon equals the one at its start (cyclic day),
    and the trip between departure and arrival appears as a step back to the arrival energy at `arrival`."""
    p = np.asarray(power_kw, float)
    check_session(len(p), arrival, departure)
    removed = np.where(p < 0, p*battery.eta_charge, p/battery.eta_discharge)*dt_h
    start = battery.soc_initial*battery.capacity_kwh
    if arrival < departure:
        return start-np.concatenate([[0.], np.cumsum(removed)])
    evening = start-np.concatenate([[0.], np.cumsum(removed[arrival:])])
    morning = evening[-1]-np.concatenate([[0.], np.cumsum(removed[:arrival-1])])
    return np.concatenate([morning, evening])
