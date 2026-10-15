"""Include the Node desktop adapter's failure/recovery scenarios in the normal suite."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DesktopBridgeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for the desktop adapter tests')
    def test_adapter_contracts(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which('node'),'--test','tests/wecom_desktop_bridge.test.mjs'],
                                cwd=root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
