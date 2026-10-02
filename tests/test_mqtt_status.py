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
        callbacks = source.split('static void on_MQTT_LOCAL_connect')[1]
        callbacks = 'static void on_MQTT_LOCAL_connect' + callbacks.split('static void on_MQTT_LOCAL_log')[0]
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
static std::mutex g_MQTT_LOCAL_mutex;
static std::atomic<bool> g_MQTT_LOCAL_connected{false}, g_status_resend{false};
static std::mutex g_shutdown_mutex;
static std::condition_variable g_shutdown_condition;
static int g_shutdown_mid = 0;
static bool g_shutdown_acknowledged = false;
static std::string MQTT_LOCAL_STATUS_TOPIC = "bmw5/status";
static int BMW_STATUS_STABLE_DELAY = 0;
constexpr int MOSQ_ERR_SUCCESS = 0;
static int publish_count = 0, publish_result = 0;
static json payload;
static int last_qos = -1;
static bool send_matching_ack = true;
static std::thread acknowledgement;
static void on_MQTT_LOCAL_publish(struct mosquitto*, void*, int);
int mosquitto_publish(mosquitto*, int* mid, const char* topic, size_t length,
                      const void* data, int qos, bool retain) {
    ++publish_count;
    last_qos = qos;
    assert(std::string(topic) == "bmw5/status" && retain);
    payload = json::parse(std::string(static_cast<const char*>(data), length));
    if (mid) *mid = 42;
    if (qos == 1 && publish_result == 0) {
        acknowledgement = std::thread([] {
            on_MQTT_LOCAL_publish(&client, nullptr, send_matching_ack ? 42 : 41);
        });
    }
    return publish_result;
}
const char* mosquitto_strerror(int) { return "test result"; }
const char* mosquitto_connack_string(int) { return "test CONNACK"; }
''' + publisher + callbacks + r'''
int main(int argc, char** argv) {
    const std::string scenario = argv[1];
    if (scenario == "offline") {
        publish_status(true);
        assert(publish_count == 0);
    } else if (scenario == "rejected") {
        on_MQTT_LOCAL_connect(&client, nullptr, 5);
        publish_status(true);
        assert(!g_MQTT_LOCAL_connected && publish_count == 0);
    } else if (scenario == "retry") {
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_result = 4;
        publish_status(true);
        publish_result = 0;
        publish_status(true);
        assert(publish_count == 2 && payload["connected"] == true);
        assert(payload.contains("timestamp"));
        publish_status(true);
        assert(publish_count == 2);
    } else if (scenario == "reconnect") {
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_status(true);
        assert(publish_count == 1);
        on_MQTT_LOCAL_disconnect(&client, nullptr, 7);
        publish_status(false);
        assert(publish_count == 1);
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_status(true);
        assert(publish_count == 2 && payload["connected"] == true);
    } else if (scenario == "delayed-local") {
        publish_status(true); // BMW connected before the local CONNACK.
        assert(publish_count == 0);
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_status(true);
        assert(publish_count == 1 && payload["connected"] == true);
    } else if (scenario == "shutdown") {
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_status(true);
        BMW_STATUS_STABLE_DELAY = 3600;
        assert(publish_shutdown_status());
        acknowledgement.join();
        assert(publish_count == 2 && last_qos == 1);
        assert(payload["connected"] == false && payload.contains("timestamp"));
    } else if (scenario == "shutdown-offline") {
        assert(!publish_shutdown_status());
        assert(publish_count == 0);
    } else if (scenario == "shutdown-error") {
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
        publish_result = 4;
        assert(!publish_shutdown_status());
        assert(publish_count == 1);
    } else if (scenario == "shutdown-unrelated-ack") {
        on_MQTT_LOCAL_connect(&client, nullptr, 0);
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
                             'shutdown-unrelated-ack'):
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
                        cleanup.index('stop_MQTT_LOCAL_client(offline_acknowledged)'))
        self.assertIn('mosquitto_publish_callback_set(client, on_MQTT_LOCAL_publish);', source)


if __name__ == '__main__':
    unittest.main()
