import unittest
from pocket.simulator import run
class SimulatorTests(unittest.TestCase):
    def test_complete_flow(self):
        result=run()
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['duplicate_dispatches'],0)
        self.assertFalse(result['real_hardware_tested'])
