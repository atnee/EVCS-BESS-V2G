"""One connection session per vehicle; departure index is exclusive."""
import numpy as np

def availability(steps: int, arrival: int, departure: int) -> np.ndarray:
    if not all(isinstance(x, int) for x in (steps, arrival, departure)) or not 0 <= arrival < departure <= steps:
        raise ValueError("Invalid arrival/departure indices")
    return (np.arange(steps) >= arrival) & (np.arange(steps) < departure)
