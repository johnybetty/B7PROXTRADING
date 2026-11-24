# uncompyle6 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)]
# Embedded file name: test_chaquopy_fix.py
# Compiled at: 2026-11-12 20:24:15
# Size of source mod 2**32: 760 bytes
import unittest, json, host_contract, pc_host_bridge
from android_pc_host import AndroidPCHost

class Tests(unittest.TestCase):

    def test_manifest_no_runtime_file_read(self):
        self.assertIn("_receive_open_price_pattern", host_contract.required_host_attributes())
        self.assertNotIn("pathlib", open((host_contract.__file__), encoding="utf8").read())

    def test_host_constructs(self):
        self.assertFalse(AndroidPCHost().status()["orders_enabled"])

    def test_bridge_status(self):
        self.assertFalse(json.loads(pc_host_bridge.status())["orders_enabled"])

    def test_start_stop(self):
        h = AndroidPCHost()
        h.start_signal_only("OPEN_PRICE_PATTERN")
        self.assertTrue(h.stop_engine())
        self.assertFalse(h.status()["running"])


if __name__ == "__main__":
    unittest.main()
