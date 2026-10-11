import unittest
from core.schemas import Bus,Line,Network,TimeGrid,make_profile
from network.feeder_loader import load_feeder, create_pandapower_network


def small_network():
    """Four-bus test network (not a feeder): only for profile and contract checks, never solved."""
    return Network("TEST-4",[Bus("source"),Bus("n1"),Bus("n2"),Bus("n3","A")],
                   [Line("l1","source","n1","ABC",.25,.15,100),Line("l2","n1","n2","ABC",.3,.2,80),
                    Line("l3","n1","n3","A",.4,.25,50)],"source",synthetic=True)


class TestNetwork(unittest.TestCase):
    def test_bad_phase(self):
        p=make_profile(TimeGrid(steps=1),"x","n3","B",[-2])
        with self.assertRaises(ValueError): p.validate(small_network())

    def test_ieee_feeders_are_never_test_networks(self):
        self.assertFalse(load_feeder("ieee8500").synthetic)
        with self.assertRaises(ValueError): create_pandapower_network(small_network())
