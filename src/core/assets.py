"""Shared immutable equipment contracts; no module implementation imports."""
from dataclasses import dataclass
from core.schemas import finite

@dataclass(frozen=True)
class Battery:
    capacity_kwh: float = 100.0
    power_kw: float = 30.0
    soc_initial: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 0.9
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    capex: float = 0.0
    degradation_per_kwh: float = 0.0
    currency: str = "USD"

    def __post_init__(self):
        for n in ("capacity_kwh", "power_kw", "capex", "degradation_per_kwh"):
            finite(getattr(self, n), n)
        if self.capacity_kwh == 0 or self.power_kw == 0:
            raise ValueError("Positive battery ratings required")
        if not 0 <= self.soc_min <= self.soc_initial <= self.soc_max <= 1:
            raise ValueError("Invalid SOC limits")
        if not 0 < self.eta_charge <= 1 or not 0 < self.eta_discharge <= 1:
            raise ValueError("Invalid efficiency")


@dataclass(frozen=True)
class Station:
    id: str
    bus: str
    phases: str
    chargers: int
    charger_kw: float
    connection_kw: float
    capex: float = 0.0
    currency: str = "USD"
    bidirectional: bool = True

    def __post_init__(self):
        if not self.id or not isinstance(self.chargers, int) or self.chargers < 1:
            raise ValueError("Positive integer charger count required")
        if not self.phases or not set(self.phases) <= set("ABC") or len(set(self.phases)) != len(self.phases):
            raise ValueError("Invalid station phases")
        for n in ("charger_kw", "connection_kw", "capex"):
            finite(getattr(self, n), n)
        if self.charger_kw == 0 or self.connection_kw == 0:
            raise ValueError("Positive connection and charger power required")

    @property
    def capacity_kw(self) -> float:
        return min(self.chargers * self.charger_kw, self.connection_kw)


