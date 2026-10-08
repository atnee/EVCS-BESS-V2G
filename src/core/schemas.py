"""Shared API v1. Changes require review by all three module owners."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import yaml


def finite(value: float, name: str, minimum: float = 0) -> None:
    if not np.isfinite(value) or value < minimum:
        raise ValueError(f"Invalid {name}: {value}")

@dataclass(frozen=True)
class TimeGrid:
    start: str = "2026-01-01T00:00:00-06:00"
    steps: int = 24
    dt_h: float = 1.0

    def __post_init__(self):
        finite(self.dt_h, "dt_h")
        if self.dt_h == 0 or not isinstance(self.steps, int) or self.steps < 1:
            raise ValueError("Positive step count and duration required")
        if pd.Timestamp(self.start).tzinfo is None:
            raise ValueError("Explicit timezone required")

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.date_range(self.start, periods=self.steps, freq=pd.Timedelta(hours=self.dt_h))

@dataclass(frozen=True)
class Bus:
    id: str
    phases: str = "ABC"
    vn_ln_v: float = 2400.0
    x: float | None = None
    y: float | None = None

    def __post_init__(self):
        if not self.id or not self.phases or len(set(self.phases)) != len(self.phases) or not set(self.phases) <= set("ABC"):
            raise ValueError("Invalid bus identifier/phases")
        finite(self.vn_ln_v, "vn_ln_v")
        if self.vn_ln_v == 0:
            raise ValueError("Voltage must be positive")

@dataclass(frozen=True)
class Line:
    id: str
    from_bus: str
    to_bus: str
    phases: str
    r_ohm: float
    x_ohm: float
    ampacity_a: float

    def __post_init__(self):
        for name in ("r_ohm", "x_ohm", "ampacity_a"):
            finite(getattr(self, name), name)
        if not self.id or self.ampacity_a == 0 or not self.phases or len(set(self.phases)) != len(self.phases) or not set(self.phases) <= set("ABC"):
            raise ValueError("Invalid line")

@dataclass
class Network:
    name: str
    buses: list[Bus]
    lines: list[Line]
    slack_bus: str
    synthetic: bool = False
    equipment: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def validate(self) -> None:
        ids = [b.id for b in self.buses]
        if len(set(ids)) != len(ids) or self.slack_bus not in ids:
            raise ValueError("Duplicate buses or missing slack")
        if len({l.id for l in self.lines}) != len(self.lines):
            raise ValueError("Duplicate line IDs")
        bus = {b.id: b for b in self.buses}
        for l in self.lines:
            if l.from_bus not in bus or l.to_bus not in bus or l.from_bus == l.to_bus:
                raise ValueError("Invalid line endpoints")
            if not set(l.phases) <= set(bus[l.from_bus].phases) & set(bus[l.to_bus].phases):
                raise ValueError("Line uses missing bus phase")

@dataclass
class Profile:
    """Signed injection per phase. Consumption is negative. Dense time coverage."""
    data: pd.DataFrame
    grid: TimeGrid
    asset_id: str
    vehicle_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, network: Network | None = None) -> None:
        required = {"time", "bus", "phase", "p_kw", "q_kvar"}
        if not required <= set(self.data.columns) or self.data.empty:
            raise ValueError("Missing profile fields or empty profile")
        d = self.data
        if d[list(required)].isna().any().any() or not np.isfinite(d[["p_kw", "q_kvar"]].to_numpy(float)).all():
            raise ValueError("Null/non-finite profile values")
        if d.duplicated(["time", "bus", "phase"]).any():
            raise ValueError("Duplicate time/bus/phase")
        for (bus_id, phase), g in d.groupby(["bus", "phase"]):
            if phase not in "ABC" or len(phase) != 1:
                raise ValueError("Invalid phase")
            times = pd.DatetimeIndex(g.time)
            if times.tz is None or not times.equals(self.grid.index):
                raise ValueError("Profile horizon mismatch or unsorted times")
            if network:
                buses = {b.id: b for b in network.buses}
                if bus_id not in buses or phase not in buses[bus_id].phases:
                    raise ValueError("Unknown bus/phase")

    def total_injection(self) -> np.ndarray:
        self.validate()
        return self.data.groupby("time").p_kw.sum().reindex(self.grid.index).to_numpy()

    def export(self, path: str | Path) -> None:
        self.validate()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.data.to_csv(path, index=False)


def make_profile(grid: TimeGrid, asset_id: str, bus: str, phases: str, p_kw, q_kvar=None, vehicle_ids=()) -> Profile:
    """Split total asset power equally across its connected phases, explicitly."""
    p = np.asarray(p_kw, dtype=float)
    q = np.zeros(grid.steps) if q_kvar is None else np.asarray(q_kvar, dtype=float)
    if p.shape != (grid.steps,) or q.shape != p.shape or not phases or len(set(phases)) != len(phases):
        raise ValueError("Invalid profile shape/phases")
    data = pd.concat([pd.DataFrame({"time": grid.index, "bus": bus, "phase": ph,
                                  "p_kw": p / len(phases), "q_kvar": q / len(phases)}) for ph in phases], ignore_index=True)
    result = Profile(data, grid, asset_id, tuple(vehicle_ids))
    result.validate()
    return result


def read_parameters(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        result = yaml.safe_load(f)
    if not isinstance(result, dict):
        raise ValueError("YAML must contain a mapping")
    return result
