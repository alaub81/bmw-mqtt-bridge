"""Offline regression tests: python3 -m unittest discover -s tests -v."""
import json
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
        self.env = dict(os.environ, BMW_TOKEN_DIR=str(self.state))
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
        (self.state / 'id_token.txt').write_text('current-id')
        (self.state / 'refresh_token.txt').write_text('current-refresh')

    def test_existing_tokens_are_used_with_consistent_permissions(self):
        self.create_tokens()
        (self.state / 'access_token.txt').write_text('current-access')
        (self.state / 'token_refresh_response.json').write_text('{"obsolete": "test-token"}')
        for filename in ('id_token.txt', 'refresh_token.txt', 'access_token.txt'):
            (self.state / filename).chmod(0o644)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bridge-started', result.stdout)
        self.assertEqual((self.state / 'id_token.txt').read_text(), 'current-id')
        self.assertFalse((self.state / 'token_refresh_response.json').exists())
        self.assertEqual((self.state / 'access_token.txt').read_text(), 'current-access')
        for filename in ('id_token.txt', 'refresh_token.txt', 'access_token.txt'):
            self.assertEqual(stat.S_IMODE((self.state / filename).stat().st_mode),
                             0o600)

    def test_restart_keeps_rotated_tokens(self):
        self.create_tokens()
        self.assertEqual(self.run_entrypoint().returncode, 0)
        (self.state / 'id_token.txt').write_text('rotated-id')
        (self.state / 'refresh_token.txt').write_text('rotated-refresh')
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertEqual((self.state / 'refresh_token.txt').read_text(),
                         'rotated-refresh')

    def test_legacy_pair_requires_explicit_migration(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        (legacy / 'id_token.txt').write_text('legacy-id')
        (legacy / 'refresh_token.txt').write_text('legacy-refresh')
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('bmw_flow.sh', result.stdout)
        self.assertFalse((self.state / 'id_token.txt').exists())
        self.assertEqual((legacy / 'refresh_token.txt').read_text(), 'legacy-refresh')

    def test_legacy_tokens_do_not_overwrite_a_partial_current_pair(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        (legacy / 'id_token.txt').write_text('legacy-id')
        (legacy / 'refresh_token.txt').write_text('legacy-refresh')
        (self.state / 'id_token.txt').write_text('current-id')
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.state / 'id_token.txt').read_text(), 'current-id')
        self.assertFalse((self.state / 'refresh_token.txt').exists())
        self.assertEqual((legacy / 'refresh_token.txt').read_text(), 'legacy-refresh')

    def test_incomplete_legacy_pair_is_not_migrated(self):
        legacy = self.state / 'bmw-mqtt-bridge'
        legacy.mkdir(parents=True)
        (legacy / 'id_token.txt').write_text('legacy-id')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.state / 'id_token.txt').exists())
        self.assertTrue((legacy / 'id_token.txt').exists())

    def test_partial_or_empty_state_is_not_overwritten(self):
        self.state.mkdir(parents=True)
        (self.state / 'id_token.txt').write_text('existing-id')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.state / 'refresh_token.txt').exists())
        (self.state / 'refresh_token.txt').write_text('')
        self.assertNotEqual(self.run_entrypoint().returncode, 0)
        self.assertEqual((self.state / 'id_token.txt').read_text(), 'existing-id')
        self.assertEqual((self.state / 'refresh_token.txt').read_text(), '')

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

    @unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
    def test_compose_forwards_all_options_and_keeps_token_mount_consistent(self):
        example_keys = {line.split('=', 1)[0] for line in
                        (ROOT / '.env.sample').read_text().splitlines()
                        if line and not line.startswith('#')}
        for compose_file in ('docker-compose.yml', 'docker-compose.dev.yml'):
            with self.subTest(compose_file=compose_file):
                service = self.compose_service(compose_file, BMW_TOKEN_DIR='/some/host/path')
                configured = service['environment']
                self.assertEqual(example_keys - {'BMB_VERSION'}, configured.keys())
                self.assertNotIn('BMB_VERSION', configured)
                self.assertNotIn('BMW_LOAD_ENV_FILE', configured)
                self.assertNotIn('BMW_TOKEN_DIR', example_keys)
                self.assertNotIn('BMW_HEARTBEAT_FILE', example_keys)
                volume = next(volume for volume in service['volumes'] if volume['source'] == 'data-token')
                self.assertEqual(volume['target'], '/app/token')
                self.assertNotIn('BMW_TOKEN_DIR', configured)
                self.assertNotIn('BMW_HEARTBEAT_FILE', configured)

    def compose_service(self, compose_file='docker-compose.yml', env_file='.env.sample', **overrides):
        env = dict(os.environ, BMW_CLIENT_ID='test-client', BMW_GCID='test-gcid')
        env.pop('BMB_VERSION', None)
        env.update(overrides)
        result = subprocess.run(['docker', 'compose', '--env-file', env_file,
                                 '-f', compose_file, 'config', '--format', 'json'],
                                cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)['services']['bmw-mqtt-bridge']

    @unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
    def test_host_compose_uses_published_image_and_defaults_to_latest(self):
        for overrides, expected in (({}, 'latest'), ({'BMB_VERSION': ''}, 'latest'),
                                    ({'BMB_VERSION': '1.2.3'}, '1.2.3')):
            with self.subTest(overrides=overrides):
                service = self.compose_service(env_file=os.devnull, **overrides)
                self.assertEqual(service['image'], f'ghcr.io/alaub81/bmw-mqtt-bridge:{expected}')
                self.assertNotIn('build', service)

    @unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
    def test_dev_compose_builds_locally_with_identical_runtime_configuration(self):
        host = self.compose_service()
        development = self.compose_service('docker-compose.dev.yml', BMB_VERSION='1.2.3')
        self.assertEqual(development.pop('image'), 'bmw-mqtt-bridge:dev')
        self.assertEqual(development.pop('build')['context'], str(ROOT))
        self.assertEqual(development.pop('pull_policy'), 'build')
        self.assertEqual(host.pop('image'), 'ghcr.io/alaub81/bmw-mqtt-bridge:latest')
        self.assertEqual(host, development)
        self.assertIn('host.docker.internal=host-gateway', host['extra_hosts'])

    def test_authentication_ignores_valid_credentials_in_old_env_file(self):
        script = (ROOT / 'resources/bmw_flow.sh').read_text()
        script = script.split('# ---------- Static OAuth config ----------')[0]
        self.state.mkdir(parents=True)
        config = self.state / '.env'
        config.write_text('BMW_CLIENT_ID=file-client\nBMW_GCID=file-gcid\n')
        env = dict(self.env)
        env.pop('BMW_CLIENT_ID', None)
        env.pop('BMW_GCID', None)
        result = subprocess.run(['bash'], input=script, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BMW_CLIENT_ID is missing', result.stderr)
        self.assertEqual(config.read_text(), 'BMW_CLIENT_ID=file-client\nBMW_GCID=file-gcid\n')

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_cpp_environment_helpers_validate_numbers_and_switches(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helpers = source.split('// ---------------------- tiny helpers for env config ----------------------')[1]
        helpers = helpers.split('// ===================== Configuration =====================')[0]
        directory_helper = source[source.index('static std::string token_dir() {'):]
        directory_helper = directory_helper.split('// Health telemetry:')[0]
        test_source = self.base / 'env_test.cpp'
        test_source.write_text('#include <string>\n#include <cstdlib>\n#include <cassert>\n'
                               '#include <algorithm>\n#include <cctype>\n#include <stdexcept>\n'
                               + helpers + directory_helper + r'''
int main() {
    unsetenv("BMW_TOKEN_DIR");
    assert(token_dir() == "/app/token");
    setenv("BMW_TOKEN_DIR", "/test/token", 1);
    assert(token_dir() == "/test/token");
    setenv("MQTT_LOCAL_HOST", "injected-host", 1);
    assert(env_str("MQTT_LOCAL_HOST", "fallback") == "injected-host");
    unsetenv("MQTT_LOCAL_PORT");
    assert(env_int("MQTT_LOCAL_PORT", 1883) == 1883);
    setenv("MQTT_LOCAL_PORT", "8883", 1);
    assert(env_int("MQTT_LOCAL_PORT", 0) == 8883);
    for (const char* invalid : {"1883abc", "1.5", "true", "999999999999999999999"}) {
        setenv("MQTT_LOCAL_PORT", invalid, 1);
        bool rejected = false;
        try { env_int("MQTT_LOCAL_PORT", 0); } catch (const std::invalid_argument&) { rejected = true; }
        assert(rejected);
    }
    setenv("MQTT_LOCAL_TLS", "TRUE", 1);
    assert(env_switch("MQTT_LOCAL_TLS", false));
    setenv("MQTT_LOCAL_TLS", "false", 1);
    assert(!env_switch("MQTT_LOCAL_TLS", true));
    for (const char* invalid : {"1", "ON", "yes"}) {
        setenv("MQTT_LOCAL_TLS", invalid, 1);
        bool rejected = false;
        try { env_switch("MQTT_LOCAL_TLS", false); } catch (const std::invalid_argument&) { rejected = true; }
        assert(rejected);
    }
}
''')
        binary = self.base / 'env-test'
        subprocess.run(['c++', '-std=c++17', str(test_source), '-o', str(binary)],
                       check=True, capture_output=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
