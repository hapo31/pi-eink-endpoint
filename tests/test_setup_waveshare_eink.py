import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / 'scripts/setup_waveshare_eink.sh'

# Replace all privileged commands so these tests never modify the host OS.
STUB = r'''
import json
import os
from pathlib import Path
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
if command == 'id':
    if args == ['-u']:
        print(os.environ.get('SETUP_TEST_UID', '1000'))
    elif args == ['-un']:
        print('root' if os.environ.get('SETUP_TEST_UID') == '0' else 'alice')
    elif args == ['-u', '--', 'missing']:
        sys.exit(1)
    else:
        print('0' if args[-1] == 'root' else '1000')
    sys.exit(0)
if command == 'sudo':
    os.execvp(args[0], args)
with open(os.environ['SETUP_TEST_LOG'], 'a') as log:
    log.write(json.dumps([command, *args]) + '\n')
if command == os.environ.get('SETUP_TEST_FAIL'):
    sys.exit(1)
'''


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.log = directory / 'commands.jsonl'
        for command in ('id', 'sudo', 'apt-get', 'raspi-config', 'usermod', 'runuser'):
            stub = directory / command
            stub.write_text(f'#!{sys.executable}\n' + STUB)
            stub.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{directory}:/usr/bin:/bin',
                        SETUP_TEST_LOG=str(self.log))
        self.env.pop('SUDO_USER', None)
        # Prevent the caller's shell setup from affecting the subprocess.
        self.env.pop('BASH_ENV', None)

    def run_setup(self, *args, **env):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            ['/bin/bash', str(SCRIPT), *args],
            env={**self.env, **env}, capture_output=True, text=True,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_selects_service_user_for_normal_sudo_and_root_invocations(self):
        cases = [
            ((), {}, 'alice'),
            ((), {'SETUP_TEST_UID': '0', 'SUDO_USER': 'alice'}, 'alice'),
            (('bob',), {'SETUP_TEST_UID': '0'}, 'bob'),
            (('bob',), {'SUDO_USER': 'alice'}, 'bob'),
        ]
        for args, env, expected in cases:
            with self.subTest(args=args, env=env):
                result, calls = self.run_setup(*args, **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(['usermod', '-aG', 'gpio,spi', '--', expected], calls)
                check = next(call for call in calls if call[0] == 'runuser')
                self.assertEqual(check[1:6], ['-u', expected, '--', '/usr/bin/python3', '-c'])
                self.assertIn(['raspi-config', 'nonint', 'do_spi', '0'], calls)
                self.assertIn('sudo reboot', result.stdout)

    def test_rejects_invalid_users_and_arguments_before_modifying_system(self):
        for args in [('root',), ('missing',), ('--unknown',), ('alice', 'bob')]:
            with self.subTest(args=args):
                result, calls = self.run_setup(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
        result, calls = self.run_setup(SETUP_TEST_UID='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failure_stops_later_setup_steps_and_never_reports_success(self):
        for failure in ('apt-get', 'raspi-config', 'usermod', 'runuser'):
            with self.subTest(failure=failure):
                result, calls = self.run_setup(SETUP_TEST_FAIL=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls[-1][0], failure)
                self.assertNotIn('setup complete', result.stdout)
                self.assertIn('setup failed', result.stderr)

    def test_help_does_not_modify_system(self):
        result, calls = self.run_setup('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
