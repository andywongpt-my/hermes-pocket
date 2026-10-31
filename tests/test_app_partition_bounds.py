import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('app_bounds', ROOT / 'firmware/tools/check_app_bounds.py')
bounds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bounds)


class AppBoundsTests(unittest.TestCase):
    def test_source_exact_factory(self):
        bounds.check_partition((ROOT / 'firmware/partitions.csv').read_text())

    def test_sector_rounding_and_exact_limit(self):
        self.assertEqual(bounds.check_size(1), 0x11000)
        self.assertEqual(bounds.check_size(4097), 0x12000)
        self.assertEqual(bounds.check_size(0x300000), 0x310000)
        self.assertEqual(bounds.check_size(0x2fffff), 0x310000)

    def test_reject_empty_oversize_wrong_offset(self):
        for n in (0, -1, 0x300001, 0x7f0000, True):
            with self.subTest(n=n), self.assertRaises(ValueError):
                bounds.check_size(n)
        for offset in (0, 0x10001, 0x20000):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                bounds.check_size(1, offset)

    def test_reject_wrong_layout(self):
        for text in ('', 'factory,app,factory,0x10000,0x7f0000',
                     'factory,app,factory,0x20000,0x300000',
                     'ota_0,app,ota_0,0x10000,0x300000',
                     'factory,app,factory,0x10000,0x300000\nother,app,ota_0,0x400000,0x10000'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                bounds.check_partition(text)
