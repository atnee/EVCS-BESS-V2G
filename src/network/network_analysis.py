"""Topology diagnostics independent of the electrical backend."""
from core.schemas import Network

def radial_order(network: Network):
    network.validate()
    children = {b.id: [] for b in network.buses}
    incoming = {}
    for line in network.lines:
        if line.to_bus in incoming:
            raise ValueError("Multiple incoming branches; radial solver only")
        children[line.from_bus].append(line)
        incoming[line.to_bus] = line
    order, queue = [], [network.slack_bus]
    while queue:
        bus = queue.pop(0)
        if bus in order:
            raise ValueError("Cycle")
        order.append(bus)
        queue.extend(l.to_bus for l in children[bus])
    if len(order) != len(network.buses) or len(network.lines) != len(network.buses)-1:
        raise ValueError("Disconnected or meshed network")
    if network.slack_bus in incoming:
        raise ValueError("Slack has incoming branch")
    for bus in order[1:]:
        if not set(next(b.phases for b in network.buses if b.id==bus)) <= set(incoming[bus].phases):
            raise ValueError("Bus phase not supplied by parent line")
    return order, children, incoming
