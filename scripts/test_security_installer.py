"""Installer preflight/lifecycle unit tests. No system accounts or launchd writes."""
import contextlib
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('install-security-macos.py')


class InstallerTests(unittest.TestCase):
    def invoke(self, arguments):
        with patch.object(sys, 'argv', [str(SCRIPT), *arguments]), patch.object(sys, 'platform', 'darwin'), patch('os.geteuid', return_value=0), patch('subprocess.run') as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(SCRIPT), run_name='__main__')
            return stopped.exception.code, run.call_args_list

    def test_unload_disables_next_boot_before_stopping_current_instance(self):
        result, calls = self.invoke(['unload', '--tenant', '501'])
        self.assertEqual(result, 0)
        self.assertEqual([c.args[0] for c in calls], [
            ['launchctl', 'disable', 'system/com.orca.toolkit.config.501'],
            ['launchctl', 'bootout', 'system/com.orca.toolkit.config.501']])
        self.assertTrue(all(c.kwargs['check'] for c in calls))

    def test_install_without_original_caller_cannot_mutate_system(self):
        result, calls = self.invoke(['install', '--tenant', '501', '--service-uid', '490', '--bundle', '/synthetic'])
        self.assertIn('--caller-app is required', result)
        self.assertEqual(calls, [])

if __name__ == '__main__': unittest.main()
