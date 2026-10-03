"""Compile production TLS setup against API doubles; no network/credentials."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class LocalTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        env_helpers = source.split('// ---------------------- tiny helpers for env config ----------------------')[1]
        env_helpers = env_helpers.split('// ===================== Configuration =====================')[0]
        tls_setup = source.split('// MQTT_LOCAL_TLS_VERIFY controls both chain and hostname verification.')[1]
        tls_setup = tls_setup.split('static std::string token_dir() {')[0]
        path = Path(cls.temp.name) / 'tls.cpp'
        path.write_text(r'''
#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
struct mosquitto {};
constexpr int MOSQ_ERR_SUCCESS = 0;
static int calls = 0, fail_call = 0, verification = -1;
static bool insecure = false;
static std::string ca;
int result() { return ++calls == fail_call ? 1 : 0; }
int mosquitto_tls_set(mosquitto*, const char* file, const char*, const char*,
                      const char*, void*) { ca = file; return result(); }
int mosquitto_tls_opts_set(mosquitto*, int verify, const char*, const char*) {
    verification = verify; return result();
}
int mosquitto_tls_insecure_set(mosquitto*, bool value) {
    insecure = value; return result();
}
const char* mosquitto_strerror(int) { return "mock TLS failure"; }
''' + env_helpers + tls_setup + r'''
int main(int argc, char** argv) {
    mosquitto client;
    const std::string scenario = argv[1];
    unsetenv("MQTT_LOCAL_TLS");
    unsetenv("MQTT_LOCAL_TLS_VERIFY");
    unsetenv("MQTT_LOCAL_TLS_CA_FILE");
    if (scenario == "default") {
        assert(configure_local_tls(&client));
        assert(calls == 0);
    } else if (scenario == "secure") {
        setenv("MQTT_LOCAL_TLS", "true", 1);
        assert(configure_local_tls(&client));
        assert(calls == 3 && verification == 1 && !insecure);
        assert(ca == "/etc/ssl/certs/ca-certificates.crt");
    } else if (scenario == "self-signed") {
        setenv("MQTT_LOCAL_TLS", "true", 1);
        setenv("MQTT_LOCAL_TLS_VERIFY", "FaLsE", 1);
        assert(configure_local_tls(&client));
        assert(calls == 3 && verification == 0 && insecure);
    } else if (scenario == "custom-ca") {
        setenv("MQTT_LOCAL_TLS", "true", 1);
        setenv("MQTT_LOCAL_TLS_VERIFY", "true", 1);
        setenv("MQTT_LOCAL_TLS_CA_FILE", "/mounted/ca.crt", 1);
        assert(configure_local_tls(&client));
        assert(ca == "/mounted/ca.crt" && verification == 1 && !insecure);
    } else if (scenario == "invalid-switch") {
        setenv("MQTT_LOCAL_TLS", "true", 1);
        setenv("MQTT_LOCAL_TLS_VERIFY", "invalid", 1);
        assert(!configure_local_tls(&client));
        assert(calls == 0);
        setenv("MQTT_LOCAL_TLS", "invalid", 1);
        assert(!configure_local_tls(&client));
    } else if (scenario == "tls-failure") {
        setenv("MQTT_LOCAL_TLS", "true", 1);
        for (int i = 1; i <= 3; ++i) {
            calls = 0; fail_call = i;
            assert(!configure_local_tls(&client));
            assert(calls == i);
        }
    } else if (scenario == "off") {
        setenv("MQTT_LOCAL_TLS", "false", 1);
        assert(configure_local_tls(&client));
        assert(calls == 0);
        assert(!env_switch("MQTT_LOCAL_TLS", true));
        setenv("MQTT_LOCAL_TLS", "false", 1);
        assert(!env_switch("MQTT_LOCAL_TLS", true));
        setenv("MQTT_LOCAL_TLS", "FALSE", 1);
        assert(!env_switch("MQTT_LOCAL_TLS", true));
    } else { return 1; }
}
''')
        cls.binary = Path(cls.temp.name) / 'tls-test'
        result = subprocess.run(['c++', '-std=c++17', str(path), '-o', str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            cls.temp.cleanup()
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_tls_configuration_modes(self):
        for scenario in ('default', 'secure', 'self-signed', 'custom-ca',
                         'invalid-switch', 'tls-failure', 'off'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.binary), scenario],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
