"""Exercise the production Homie publisher with deterministic MQTT clients."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class HomieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        source = cls.base / 'homie.cpp'
        source.write_text(r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "json.hpp"
using json = nlohmann::json;
struct mosquitto {
    void* context;
    void (*connect_cb)(mosquitto*, void*, int) = nullptr;
    void (*disconnect_cb)(mosquitto*, void*, int) = nullptr;
    void (*publish_cb)(mosquitto*, void*, int) = nullptr;
    std::thread ack;
};
static std::vector<mosquitto*> clients;
static std::vector<std::pair<std::string, std::string>> messages, wills;
static int destroyed = 0, tls_calls = 0, failure = 0;
static bool connect_now = true;
static std::string BMB_MQTT_LOCAL_HOST = "broker.test", BMB_MQTT_LOCAL_USER = "user", BMB_MQTT_LOCAL_PASSWORD = "secret";
static int BMB_MQTT_LOCAL_PORT = 8883;
constexpr int MOSQ_ERR_SUCCESS = 0;
constexpr int MOSQ_LOG_ERR = 1, MOSQ_LOG_WARNING = 2;
const char* mosquitto_strerror(int) { return "injected failure"; }
const char* mosquitto_connack_string(int rc) { return rc == 0 ? "Connection Accepted" : "Connection Refused"; }
mosquitto* mosquitto_new(const char* id, bool clean, void* context) {
    assert(id == nullptr && clean);
    auto* client = new mosquitto{context};
    clients.push_back(client);
    return client;
}
void mosquitto_connect_callback_set(mosquitto* c, decltype(c->connect_cb) cb) { c->connect_cb = cb; }
void mosquitto_disconnect_callback_set(mosquitto* c, decltype(c->disconnect_cb) cb) { c->disconnect_cb = cb; }
void mosquitto_publish_callback_set(mosquitto* c, decltype(c->publish_cb) cb) { c->publish_cb = cb; }
void mosquitto_log_callback_set(mosquitto*, void (*)(mosquitto*, void*, int, const char*)) {}
int mosquitto_reconnect_delay_set(mosquitto*, int a, int b, bool backoff) {
    assert(a == 1 && b == 10 && backoff); return 0;
}
int mosquitto_will_set(mosquitto*, const char* topic, int size, const void* payload, int qos, bool retain) {
    assert(qos == 1 && retain);
    wills.emplace_back(topic, std::string(static_cast<const char*>(payload), size)); return 0;
}
int mosquitto_username_pw_set(mosquitto*, const char* user, const char* password) {
    assert(std::string(user) == "user" && std::string(password) == "secret"); return 0;
}
bool configure_local_tls(mosquitto*) { ++tls_calls; return true; }
int mosquitto_connect_async(mosquitto*, const char* host, int port, int keepalive) {
    assert(std::string(host) == BMB_MQTT_LOCAL_HOST && port == 8883 && keepalive == 30); return 0;
}
int mosquitto_loop_start(mosquitto* c) {
    if (connect_now) c->connect_cb(c, c->context, 0); return 0;
}
int mosquitto_disconnect(mosquitto* c) { c->disconnect_cb(c, c->context, 0); return 0; }
int mosquitto_loop_stop(mosquitto* c, bool force) {
    assert(force);
    if (c->ack.joinable()) c->ack.join(); return 0;
}
void mosquitto_destroy(mosquitto* c) {
    clients.erase(std::remove(clients.begin(), clients.end(), c), clients.end());
    ++destroyed; delete c;
}
int mosquitto_publish(mosquitto* c, int* mid, const char* topic, int size, const void* payload, int qos, bool retain) {
    assert(qos == 1 && retain);
    if (failure) return failure;
    messages.emplace_back(topic, std::string(static_cast<const char*>(payload), size));
    if (mid) {
        *mid = 42;
        c->ack = std::thread([c] { c->publish_cb(c, c->context, 42); });
    }
    return 0;
}
static bool write_file_atomic(const std::string& path, const std::string& content) {
    std::ofstream out(path); out << content; return out.good();
}
#include "homie_publisher.hpp"
static std::string base = "homie/bmw-wby8p610007l21042/";
static std::string id(const std::string& key) {
    std::string s = "p-";
    for (unsigned char c : key) {
        s += "0123456789abcdef"[c >> 4]; s += "0123456789abcdef"[c & 15];
    }
    return s;
}
static std::string latest(const std::string& topic) {
    for (auto it = messages.rbegin(); it != messages.rend(); ++it)
        if (it->first == topic) return it->second;
    return "<missing>";
}
int main(int argc, char** argv) {
    const std::string scenario = argv[1], cache = argv[2];
    const std::string vin = "WBY8P610007L21042", key = "vehicle.drivetrain.electricEngine.charging.smeEnergyDeltaFullyCharged";
    const auto field = json{{key, {{"value", 0}, {"unit", "kWh"}}}};
    HomiePublisher publisher(cache);
    if (scenario == "invalid") {
        publisher.ingest("invalid", field);
        publisher.ingest(vin, {{"null", {{"value", nullptr}}}, {"empty", {{"value", ""}}}, {"bad", 1}});
        publisher.tick(true);
        assert(clients.empty() && messages.empty());
    } else if (scenario == "restore") {
        publisher.tick(true);
        assert(latest(base + "telemetry/" + id(key)) == "0.75");
        assert(latest(base + "telemetry/$properties").find(id("new.field")) != std::string::npos);
        assert(clients.size() == 2);
    } else if (scenario == "publish") {
        publisher.ingest(vin, field);
        publisher.tick(true);
        assert(clients.size() == 1 && tls_calls == 1);
        assert(wills[0] == std::make_pair(base + "$state", std::string("lost")));
        assert(latest(base + "$homie") == "4.0.0");
        assert(latest(base + "$state") == "ready");
        assert(latest(base + "telemetry/" + id(key) + "/$datatype") == "float");
        assert(latest(base + "telemetry/" + id(key) + "/$unit") == "kWh");
        assert(latest(base + "telemetry/" + id(key)) == "0");
        messages.clear();
        publisher.ingest(vin, {{key, {{"value", 0.75}, {"unit", "kWh"}}}});
        publisher.tick(true);
        assert(latest(base + "telemetry/" + id(key)) == "0.75");
        assert(latest(base + "$homie") == "<missing>"); // values do not rebuild channels
        messages.clear();
        publisher.ingest(vin, {{"new.field", {{"value", true}}}, {"new-field", {{"value", "OPEN"}}}});
        publisher.tick(true);
        assert(latest(base + "telemetry/$properties").find(id(key)) != std::string::npos);
        assert(latest(base + "telemetry/$properties").find(id("new.field")) != std::string::npos);
        assert(id("new.field") != id("new-field"));
        assert(latest(base + "telemetry/" + id("new.field") + "/$datatype") == "boolean");
        assert(latest(base + "telemetry/" + id("new-field") + "/$datatype") == "string");
        publisher.tick(false);
        assert(latest(base + "$state") == "alert");
        messages.clear();
        clients[0]->disconnect_cb(clients[0], clients[0]->context, 1);
        clients[0]->connect_cb(clients[0], clients[0]->context, 0);
        publisher.tick(true);
        assert(latest(base + "$homie") == "4.0.0");
        assert(latest(base + "telemetry/" + id(key)) == "0.75");
        publisher.ingest("WBA00000000000001", field);
        publisher.tick(true);
        assert(clients.size() == 2 && wills.size() == 2 && wills[0].first != wills[1].first);
    } else if (scenario == "async") {
        connect_now = false;
        publisher.ingest(vin, field);
        publisher.tick(true);
        assert(clients.size() == 1 && messages.empty());
        assert(std::filesystem::exists(cache));
        clients[0]->connect_cb(clients[0], clients[0]->context, 5);
        publisher.tick(true);
        assert(messages.empty());
        clients[0]->connect_cb(clients[0], clients[0]->context, 0);
        publisher.tick(true);
        assert(latest(base + "$state") == "ready");
        assert(latest(base + "telemetry/" + id(key)) == "0");
    } else if (scenario == "retry") {
        failure = 4;
        publisher.ingest(vin, field);
        publisher.tick(true);
        assert(messages.empty());
        failure = 0;
        publisher.tick(true);
        assert(latest(base + "$state") == "ready");
        assert(latest(base + "telemetry/" + id(key)) == "0");
    } else if (scenario == "corrupt") {
        publisher.tick(true);
        assert(clients.empty());
        publisher.ingest(vin, field);
        publisher.tick(true);
        assert(clients.size() == 1);
    } else { assert(false); }
    publisher.shutdown();
    assert(clients.empty());
    if (scenario != "invalid") assert(latest(base + "$state") == "disconnected");
}
''')
        cls.binary = cls.base / 'homie'
        subprocess.run(['c++', '-std=c++17', '-pthread', '-Wall', '-Wextra',
                        '-I', str(ROOT / 'resources/src'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_scenario(self, name, cache):
        result = subprocess.run([str(self.binary), name, str(cache)],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_dynamic_schema_values_reconnect_and_restart(self):
        cache = self.base / 'restore.json'
        self.run_scenario('publish', cache)
        self.run_scenario('restore', cache)

    def test_invalid_and_empty_values_do_not_create_devices(self):
        self.run_scenario('invalid', self.base / 'invalid.json')

    def test_failed_publications_retry_full_description(self):
        self.run_scenario('retry', self.base / 'retry.json')

    def test_async_connection_refusal_is_logged_and_recovery_publishes(self):
        result = self.run_scenario('async', self.base / 'async.json')
        self.assertIn('CONNACK rc=5 (Connection Refused)', result.stderr)
        self.assertIn('CONNACK rc=0 (Connection Accepted)', result.stderr)
        self.assertIn('queued under homie/bmw-wby8p610007l21042/', result.stderr)

    def test_corrupt_cache_does_not_stop_live_publishing(self):
        cache = self.base / 'corrupt.json'
        cache.write_text('{broken')
        self.run_scenario('corrupt', cache)
