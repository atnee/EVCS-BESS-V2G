"""Protocols isolate module operations from electrical solver implementation."""
from typing import Protocol
from core.schemas import Network, Profile, TimeGrid

class OperationalModel(Protocol):
    def run(self, grid: TimeGrid) -> Profile: ...

class ElectricalSolver(Protocol):
    def solve(self, network: Network, profile: Profile) -> dict: ...
