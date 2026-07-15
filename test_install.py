#!/usr/bin/env python3
"""Unit tests for the PyObjC bootstrap logic in install.py.

Run:  /usr/bin/python3 test_install.py
"""

import unittest
from unittest.mock import patch

import install


class EnsurePyobjcTests(unittest.TestCase):
    @patch.object(install, "_pip_install_pyobjc")
    @patch.object(install, "_pyobjc_available", return_value=True)
    def test_skips_install_when_already_available(self, _available, pip_install):
        self.assertTrue(install.ensure_pyobjc())
        pip_install.assert_not_called()

    @patch.object(install, "_pip_install_pyobjc")
    @patch.object(install, "_pyobjc_available", return_value=False)
    def test_installs_when_missing(self, _available, pip_install):
        pip_install.return_value.returncode = 0
        self.assertTrue(install.ensure_pyobjc())
        pip_install.assert_called_once()

    @patch.object(install, "_pip_install_pyobjc")
    @patch.object(install, "_pyobjc_available", return_value=False)
    def test_returns_false_when_pip_install_fails(self, _available, pip_install):
        pip_install.return_value.returncode = 1
        pip_install.return_value.stderr = "boom"
        self.assertFalse(install.ensure_pyobjc())


if __name__ == "__main__":
    unittest.main()
