"""IEEE123 shortcuts over network.feeder_loader (kept for existing imports)."""
from network.feeder_loader import DATA_DIR, FEEDERS, read_feeder_data, load_feeder, create_pandapower_network

DATA_PATH = DATA_DIR/FEEDERS["ieee123"]["file"]
FIDELITY = FEEDERS["ieee123"]["fidelity"]
LIMITATIONS = FEEDERS["ieee123"]["limitations"]


def read_ieee123_data(path=None):
    return read_feeder_data("ieee123",path)


def load_ieee123(path=None):
    return load_feeder("ieee123",path)
