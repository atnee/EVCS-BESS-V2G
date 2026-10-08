"""Topological candidates only; electrical feasibility requires a solver."""
from core.schemas import Network

def candidate_buses(network: Network, phases: str = "ABC") -> list[str]:
    network.validate()
    return [b.id for b in network.buses if b.id != network.slack_bus and set(phases) <= set(b.phases)]
