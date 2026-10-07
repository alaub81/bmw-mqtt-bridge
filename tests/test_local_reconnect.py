"""Compile the production local watchdog against deterministic MQTT API doubles."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class LocalReconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helpers = source[source.index('static int publish_local('):]
        helpers = helpers.split('// v5 connect callback')[0]
        path = Path(cls.temp.name) / 'reconnect.cpp'
        path.write_text(r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
struct mosquitto { bool stopped = false; };
static mosquitto* g_local = nullptr;
static std::mutex g_local_mutex;
static std::atomic<bool> g_local_connected{false}, g_stop{false};
static std::string BMB_MQTT_LOCAL_CLIENT_ID = "bmw5-bridge", MQTT_LOCAL_STATUS_TOPIC = "bmw5/status";
static std::string BMB_MQTT_LOCAL_USER = "user", BMB_MQTT_LOCAL_PASSWORD = "password", BMB_MQTT_LOCAL_HOST = "mqtt.test";
static int BMB_MQTT_LOCAL_PORT = 8883;
static int BMB_MQTT_RAW_TOPICS = 1, BMB_MQTT_SPLIT_TOPICS = 0;
static int will_calls = 0;
constexpr int MOSQ_ERR_SUCCESS = 0, MOSQ_ERR_NO_CONN = 4;
static int created = 0, destroyed = 0, connect_calls = 0, tls_calls = 0, publish_calls = 0;
static int connect_result = 0, loop_result = 0;
static bool allocation_failure = false, tls_failure = false, probe_stop = false;
static int publish_local(const std::string&, const std::string&, bool);
static void on_local_connect(mosquitto*, void*, int) {}
static void on_local_disconnect(mosquitto*, void*, int) {}
static void on_local_publish(mosquitto*, void*, int) {}
static void on_local_log(mosquitto*, void*, int, const char*) {}
const char* mosquitto_strerror(int) { return "test result"; }
mosquitto* mosquitto_new(const char* id, bool clean, void*) {
    ++created;
    assert(clean);
    assert(BMB_MQTT_LOCAL_CLIENT_ID.empty() ? id == nullptr : std::string(id) == BMB_MQTT_LOCAL_CLIENT_ID);
    return allocation_failure ? nullptr : new mosquitto;
}
void mosquitto_connect_callback_set(mosquitto*, void (*callback)(mosquitto*, void*, int)) {
    assert(callback == on_local_connect);
}
void mosquitto_disconnect_callback_set(mosquitto*, void (*callback)(mosquitto*, void*, int)) {
    assert(callback == on_local_disconnect);
}
void mosquitto_publish_callback_set(mosquitto*, void (*callback)(mosquitto*, void*, int)) {
    assert(callback == on_local_publish);
}
void mosquitto_log_callback_set(mosquitto*, void (*callback)(mosquitto*, void*, int, const char*)) {
    assert(callback == on_local_log);
}
int mosquitto_reconnect_delay_set(mosquitto*, int minimum, int maximum, bool backoff) {
    assert(minimum == 1 && maximum == 10 && backoff);
    return 0;
}
int mosquitto_will_set(mosquitto*, const char* topic, size_t size,
                       const void* payload, int qos, bool retain) {
    ++will_calls;
    assert(std::string(topic) == MQTT_LOCAL_STATUS_TOPIC && qos == 0 && retain);
    assert(std::string(static_cast<const char*>(payload), size) == "{\"connected\":false}");
    return 0;
}
int mosquitto_username_pw_set(mosquitto*, const char* user, const char* password) {
    assert(std::string(user) == BMB_MQTT_LOCAL_USER && std::string(password) == BMB_MQTT_LOCAL_PASSWORD);
    return 0;
}
bool configure_local_tls(mosquitto*) { ++tls_calls; return !tls_failure; }
int mosquitto_connect_async(mosquitto* client, const char* host, int port, int keepalive) {
    assert(g_local == client);
    assert(std::string(host) == BMB_MQTT_LOCAL_HOST && port == BMB_MQTT_LOCAL_PORT && keepalive == 30);
    ++connect_calls;
    return connect_result;
}
int mosquitto_loop_start(mosquitto*) { return loop_result; }
int mosquitto_disconnect(mosquitto*) { return 0; }
int mosquitto_loop_stop(mosquitto* client, bool force) {
    assert(force);
    if (probe_stop) {
        // Simulate a callback publishing while the old network thread is joined.
        std::thread callback([] {
            assert(publish_local("bmw5/test", "payload", false) == MOSQ_ERR_NO_CONN);
        });
        callback.join();
    }
    client->stopped = true;
    return 0;
}
void mosquitto_destroy(mosquitto* client) {
    assert(client != g_local);
    ++destroyed;
    delete client;
}
int mosquitto_publish(mosquitto* client, int*, const char* topic, size_t size,
                      const void* payload, int qos, bool retain) {
    assert(client == g_local && !client->stopped);
    assert(std::string(topic) == "bmw5/test" && qos == 0 && retain);
    assert(std::string(static_cast<const char*>(payload), size) == "payload");
    ++publish_calls;
    return 0;
}
''' + helpers + r'''
int main(int argc, char** argv) {
    const std::string scenario = argv[1];
    const auto start = std::chrono::steady_clock::time_point{};
    if (scenario == "output-modes") {
        for (int raw : {0, 1}) for (int split : {0, 1}) {
            BMB_MQTT_RAW_TOPICS = raw;
            BMB_MQTT_SPLIT_TOPICS = split;
            will_calls = 0;
            assert(restart_local_client());
            assert(will_calls == (raw || split ? 1 : 0));
        }
    } else if (scenario == "stalled-loop") {
        assert(restart_local_client());
        check_local_connection(start);
        check_local_connection(start + std::chrono::seconds(29));
        assert(created == 1);
        check_local_connection(start + std::chrono::seconds(30));
        assert(created == 2 && destroyed == 1 && connect_calls == 2 && tls_calls == 2);
        check_local_connection(start + std::chrono::seconds(59));
        assert(created == 2);
        check_local_connection(start + std::chrono::seconds(60));
        assert(created == 3 && destroyed == 2);
    } else if (scenario == "automatic-reconnect") {
        assert(restart_local_client());
        check_local_connection(start);
        g_local_connected = true;
        check_local_connection(start + std::chrono::seconds(20));
        check_local_connection(start + std::chrono::seconds(100));
        assert(created == 1);
        g_local_connected = false;
        check_local_connection(start + std::chrono::seconds(129));
        assert(created == 1);
        check_local_connection(start + std::chrono::seconds(130));
        assert(created == 2);
    } else if (scenario == "allocation-failure") {
        check_local_connection(start);
        allocation_failure = true;
        check_local_connection(start + std::chrono::seconds(30));
        assert(created == 1 && !g_local);
        check_local_connection(start + std::chrono::seconds(59));
        assert(created == 1);
        allocation_failure = false;
        check_local_connection(start + std::chrono::seconds(60));
        assert(created == 2 && g_local);
    } else if (scenario == "connect-failure" || scenario == "loop-failure" || scenario == "tls-failure") {
        if (scenario == "connect-failure") connect_result = 4;
        if (scenario == "loop-failure") loop_result = 7;
        if (scenario == "tls-failure") tls_failure = true;
        assert(!restart_local_client());
        assert(!g_local && !g_local_connected && destroyed == 1);
        connect_result = loop_result = 0;
        tls_failure = false;
        assert(restart_local_client());
        assert(g_local && !g_local_connected);
    } else if (scenario == "concurrent-publishing") {
        assert(restart_local_client());
        assert(publish_local("bmw5/test", "payload", true) == MOSQ_ERR_NO_CONN);
        g_local_connected = true;
        assert(publish_local("bmw5/test", "payload", true) == 0);
        probe_stop = true;
        assert(restart_local_client());
        assert(publish_calls == 1 && destroyed == 1);
    } else if (scenario == "shutdown") {
        check_local_connection(start);
        g_stop = true;
        check_local_connection(start + std::chrono::seconds(60));
        assert(created == 0);
    } else if (scenario == "generated-id") {
        BMB_MQTT_LOCAL_CLIENT_ID.clear();
        assert(restart_local_client());
    } else { return 1; }
    stop_local_client(false);
    assert(!g_local && !g_local_connected);
}
''')
        cls.binary = Path(cls.temp.name) / 'reconnect-test'
        result = subprocess.run(['c++', '-std=c++17', '-pthread', str(path), '-o', str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            cls.temp.cleanup()
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_MQTT_LOCAL_recovery_and_client_replacement(self):
        for scenario in ('output-modes', 'stalled-loop', 'automatic-reconnect', 'allocation-failure',
                         'connect-failure', 'loop-failure', 'tls-failure',
                         'concurrent-publishing', 'shutdown', 'generated-id'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
