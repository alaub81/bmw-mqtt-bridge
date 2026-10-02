"""Offline regression tests: python3 -m unittest discover -s tests -v."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.env = dict(os.environ, BMW_TOKEN_DIR=str(self.state),
                        BMW_LOAD_ENV_FILE='0')
        # Replace only the final binary execution with a harmless sentinel.
        # All bootstrap and validation logic is the production entrypoint.
        script = (ROOT / 'resources/docker-entrypoint.sh').read_text()
        self.entrypoint = self.base / 'entrypoint.sh'
        self.entrypoint.write_text(script.replace(
            'exec /app/bmw_mqtt_bridge', 'echo bridge-started'))

    def run_entrypoint(self, *args):
        return subprocess.run(['bash', str(self.entrypoint), *args],
                              env=self.env, text=True, capture_output=True)

    def create_tokens(self):
        self.state.mkdir(parents=True)
        (self.state / 'id_token').write_text('current-id')
        (self.state / 'refresh_token').write_text('current-refresh')

    def test_existing_tokens_are_used_and_permissions_restricted(self):
        self.create_tokens()
        for filename in ('id_token', 'refresh_token'):
            (self.state / filename).chmod(0o644)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bridge-started', result.stdout)
        self.assertEqual((self.state / 'id_token').read_text(), 'current-id')
        for filename in ('id_token', 'refresh_token'):
            self.assertEqual(stat.S_IMODE((self.state / filename).stat().st_mode),
                             0o600)

    def test_restart_keeps_rotated_tokens(self):
        self.create_tokens()
        self.assertEqual(self.run_entrypoint().returncode, 0)
        (self.state / 'id_token').write_text('rotated-id')
        (self.state / 'refresh_token').write_text('rotated-refresh')
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertEqual((self.state / 'refresh_token').read_text(),
                         'rotated-refresh')

    def test_complete_legacy_pair_is_migrated_without_reauthentication(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        for filename, content in (('id_token', 'legacy-id'),
                                  ('refresh_token', 'legacy-refresh'),
                                  ('access_token.txt', 'legacy-access')):
            (legacy / filename).write_text(content)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Migrated legacy tokens', result.stdout)
        self.assertEqual((self.state / 'id_token').read_text(), 'legacy-id')
        self.assertEqual((self.state / 'refresh_token').read_text(), 'legacy-refresh')
        self.assertFalse(legacy.exists())
        self.assertEqual(self.run_entrypoint().returncode, 0)

    def test_legacy_tokens_do_not_overwrite_a_partial_current_pair(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        (legacy / 'id_token').write_text('legacy-id')
        (legacy / 'refresh_token').write_text('legacy-refresh')
        (self.state / 'id_token').write_text('current-id')
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.state / 'id_token').read_text(), 'current-id')
        self.assertFalse((self.state / 'refresh_token').exists())
        self.assertEqual((legacy / 'refresh_token').read_text(), 'legacy-refresh')

    def test_incomplete_legacy_pair_is_not_migrated(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        (legacy / 'id_token').write_text('legacy-id')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.state / 'id_token').exists())
        self.assertTrue((legacy / 'id_token').exists())

    def test_partial_or_empty_state_is_not_overwritten(self):
        self.state.mkdir(parents=True)
        (self.state / 'id_token').write_text('existing-id')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.state / 'refresh_token').exists())
        (self.state / 'refresh_token').write_text('')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertEqual((self.state / 'id_token').read_text(), 'existing-id')
        self.assertEqual((self.state / 'refresh_token').read_text(), '')

    def test_empty_volume_requests_authentication(self):
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 1)
        self.assertIn('bmw_flow.sh', result.stdout)
        self.assertNotIn('bridge-started', result.stdout)
        self.assertFalse(self.state.exists())

    def test_custom_command_bypasses_token_checks(self):
        self.assertEqual(self.run_entrypoint(shutil.which('true')).returncode, 0)
        self.assertFalse(self.state.exists())

    def test_authentication_uses_environment_without_creating_env_file(self):
        # Stop before the network exchange; exercise configuration and ID validation.
        script = (ROOT / 'resources/bmw_flow.sh').read_text()
        script = script.split('# ---------- Static OAuth config ----------')[0]
        script += '\nprintf "%s:%s:%s\\n" "$BMW_CLIENT_ID" "$BMW_GCID" "$OUT_DIR"\n'
        self.state.mkdir(parents=True)
        (self.state / '.env').write_text('BMW_CLIENT_ID=old-client\nBMW_GCID=old-BMW_GCID\n')
        env = dict(self.env, BMW_CLIENT_ID='injected-client', BMW_GCID='injected-BMW_GCID')
        result = subprocess.run(['bash'], input=script, env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'injected-client:injected-BMW_GCID:{self.state}', result.stdout)
        (self.state / '.env').unlink()
        result = subprocess.run(['bash'], input=script, env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / '.env').exists())
        env.pop('BMW_CLIENT_ID')
        result = subprocess.run(['bash'], input=script, env=env,
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_cpp_env_file_is_only_a_fallback(self):
        # Compile the actual loader helpers without MQTT/network dependencies.
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helpers = source.split('// ---------------------- tiny helpers for env config ----------------------')[1]
        helpers = helpers.split('// ===================== Configuration =====================')[0]
        directory_helper = source[source.index('static std::string token_dir() {'):]
        directory_helper = directory_helper.split('// Health telemetry:')[0]
        test_source = self.base / 'env_test.cpp'
        test_source.write_text('#include <string>\n#include <fstream>\n'
                               '#include <cstdlib>\n#include <cassert>\n#include <algorithm>\n'
                               '#include <cctype>\n#include <stdexcept>\n' + helpers + directory_helper + r'''
int main(int argc, char** argv) {
    setenv("BMW_TOKEN_DIR", "/app/token", 1);
    assert(token_dir() == "/app/token");
    unsetenv("BMW_TOKEN_DIR");
    setenv("HOME", "/home/bmw-test", 1);
    assert(token_dir() == "/home/bmw-test/.local/state/bmw-mqtt-bridge");
    unsetenv("HOME");
    assert(token_dir() == "./.local/state/bmw-mqtt-bridge");
    setenv("MQTT_LOCAL_HOST", "injected-host", 1);
    unsetenv("MQTT_LOCAL_PORT");
    load_env_file(argv[1]);
    assert(env_str("MQTT_LOCAL_HOST", "") == "injected-host");
    assert(env_int("MQTT_LOCAL_PORT", 0) == 1884);
    assert(env_str("MQTT_LOCAL_USER", "") == "quoted-user");
    load_env_file("/nonexistent-env-file");
    assert(env_str("MQTT_LOCAL_HOST", "") == "injected-host");
}
''')
        config = self.base / 'fallback.env'
        config.write_text('MQTT_LOCAL_HOST=file-host\nMQTT_LOCAL_PORT=1884\n'
                          'MQTT_LOCAL_USER="quoted-user"\n')
        binary = self.base / 'env-test'
        subprocess.run(['c++', '-std=c++17', str(test_source), '-o', str(binary)],
                       check=True, capture_output=True)
        subprocess.run([str(binary), str(config)], check=True)


if __name__ == '__main__':
    unittest.main()
