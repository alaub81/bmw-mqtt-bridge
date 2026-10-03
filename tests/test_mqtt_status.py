"""Regression checks for retained status after local MQTT reconnects."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class MqttStatusTests(unittest.TestCase):
    def test_status_connection_and_retry_handling(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        publisher = source.split('// Debounced status publisher for MQTT_LOCAL_STATUS_TOPIC')[1]
        publisher = publisher.split('static std::string read_file')[0]
        callbacks = source.split('static void on_local_connect')[1]
        callbacks = 'static void on_local_connect' + callbacks.split('static void on_local_log')[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'status.cpp'
            path.write_text(r'''
#include <atomic>
#include <cassert>
#include <ctime>
#include <iostream>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <thread>
#include <string>
#include "json.hpp"
using json = nlohmann::json;
struct mosquitto {};
static mosquitto client;
static mosquitto* g_local = &client;
static std::mutex g_local_mutex;
static std::atomic<bool> g_local_connected{false}, g_status_resend{false}, g_connected{false};
static std::mutex g_shutdown_mutex;
static std::condition_variable g_shutdown_condition;
static int g_shutdown_mid = 0;
static bool g_shutdown_acknowledged = false;
static std::string MQTT_LOCAL_STATUS_TOPIC = "bmw5/status";
static int BMB_BMW_STATUS_STABLE_DELAY = 0;
constexpr int MOSQ_ERR_SUCCESS = 0;
static int publish_count = 0, publish_result = 0;
static json payload;
static int last_qos = -1;
static bool send_matching_ack = true;
static std::thread acknowledgement;
static void on_local_publish(struct mosquitto*, void*, int);
int mosquitto_publish(mosquitto*, int* mid, const char* topic, size_t length,
                      const void* data, int qos, bool retain) {
    ++publish_count;
    last_qos = qos;
    assert(std::string(topic) == "bmw5/status" && retain);
    payload = json::parse(std::string(static_cast<const char*>(data), length));
    if (mid) *mid = 42;
    if (mid && qos == 1 && publish_result == 0) {
        acknowledgement = std::thread([] {
            on_local_publish(&client, nullptr, send_matching_ack ? 42 : 41);
        });
    }
    return publish_result;
}
const char* mosquitto_strerror(int) { return "test result"; }
const char* mosquitto_connack_string(int) { return "test CONNACK"; }
''' + publisher + callbacks + r'''
int main(int argc, char** argv) {
    const std::string scenario = argv[1];
    g_connected = true;
    if (scenario == "offline") {
        publish_status();
        assert(publish_count == 0);
    } else if (scenario == "rejected") {
        on_local_connect(&client, nullptr, 5);
        publish_status();
        assert(!g_local_connected && publish_count == 0);
    } else if (scenario == "retry") {
        publish_result = 4;
        on_local_connect(&client, nullptr, 0);
        publish_result = 0;
        publish_status();
        assert(publish_count == 2 && payload["connected"] == true && last_qos == 1);
        assert(payload.contains("timestamp"));
        publish_status();
        assert(publish_count == 2);
    } else if (scenario == "reconnect") {
        on_local_connect(&client, nullptr, 0);
        assert(publish_count == 1);
        on_local_disconnect(&client, nullptr, 7);
        g_connected = false;
        publish_status();
        assert(publish_count == 1);
        g_connected = true;
        on_local_connect(&client, nullptr, 0);
        assert(publish_count == 2 && payload["connected"] == true);
    } else if (scenario == "reconnect-bmw-offline") {
        on_local_connect(&client, nullptr, 0);
        on_local_disconnect(&client, nullptr, 7);
        g_connected = false;
        on_local_connect(&client, nullptr, 0);
        assert(publish_count == 2 && payload["connected"] == false);
    } else if (scenario == "delayed-local") {
        publish_status(); // BMW connected before the local CONNACK.
        assert(publish_count == 0);
        on_local_connect(&client, nullptr, 0);
        assert(publish_count == 1 && payload["connected"] == true);
    } else if (scenario == "periodic-refresh") {
        on_local_connect(&client, nullptr, 0);
        const auto now = std::chrono::steady_clock::now();
        payload = {{"connected", false}}; // A late Will overwrites the broker's retained status.
        publish_status(now + std::chrono::seconds(29));
        assert(publish_count == 1);
        publish_status(now + std::chrono::seconds(31));
        assert(publish_count == 2 && payload["connected"] == true && last_qos == 1);
    } else if (scenario == "current-bmw-state") {
        on_local_connect(&client, nullptr, 0);
        g_connected = false;
        publish_status();
        assert(payload["connected"] == false);
        g_connected = true;
        publish_status();
        assert(publish_count == 3 && payload["connected"] == true);
    } else if (scenario == "stale-local-callback") {
        on_local_connect(&client, nullptr, 0);
        mosquitto old_client;
        on_local_disconnect(&old_client, nullptr, 7);
        assert(g_local_connected);
        on_local_connect(&old_client, nullptr, 0);
        assert(publish_count == 1);
    } else if (scenario == "shutdown") {
        on_local_connect(&client, nullptr, 0);
        BMB_BMW_STATUS_STABLE_DELAY = 3600;
        assert(publish_shutdown_status());
        acknowledgement.join();
        assert(publish_count == 2 && last_qos == 1);
        assert(payload["connected"] == false && payload.contains("timestamp"));
    } else if (scenario == "shutdown-offline") {
        assert(!publish_shutdown_status());
        assert(publish_count == 0);
    } else if (scenario == "shutdown-error") {
        on_local_connect(&client, nullptr, 0);
        publish_result = 4;
        assert(!publish_shutdown_status());
        assert(publish_count == 2);
    } else if (scenario == "shutdown-unrelated-ack") {
        on_local_connect(&client, nullptr, 0);
        send_matching_ack = false;
        assert(!publish_shutdown_status());
        acknowledgement.join();
        assert(!g_shutdown_acknowledged);
    } else { return 1; }
}

''')
            binary = Path(temporary) / 'status-test'
            result = subprocess.run(['c++', '-std=c++17', '-pthread', '-I',
                                     str(ROOT / 'resources/src'), str(path),
                                     '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in ('offline', 'rejected', 'retry', 'reconnect', 'delayed-local',
                             'shutdown', 'shutdown-offline', 'shutdown-error',
                             'shutdown-unrelated-ack', 'reconnect-bmw-offline', 'periodic-refresh',
                             'current-bmw-state', 'stale-local-callback'):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_shutdown_keeps_network_loop_alive_until_status_confirmation(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        cleanup = source.split('// Cleanup')[1].split('mosquitto_lib_cleanup();')[0]
        self.assertLess(cleanup.index('mosquitto_loop_stop(g_bmw'),
                        cleanup.index('publish_shutdown_status()'))
        self.assertLess(cleanup.index('publish_shutdown_status()'),
                        cleanup.index('stop_local_client(offline_acknowledged)'))
        self.assertIn('mosquitto_publish_callback_set(client, on_local_publish);', source)


if __name__ == '__main__':
    unittest.main()
