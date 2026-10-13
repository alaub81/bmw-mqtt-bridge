"""Exercise the production Homie publisher with deterministic MQTT clients."""
from pathlib import Path
import json
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
static int destroyed = 0, tls_calls = 0, failure = 0, cache_writes = 0;
static bool cache_failure = false;
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
    ++cache_writes;
    if (cache_failure) return false;
    std::ofstream out(path); out << content; return out.good();
}
#include "homie_publisher.hpp"
static std::string base = "homie/bmw-wby8p610007l21042/";
static std::string legacy_id(const std::string& key) {
    std::string s = "p-";
    for (unsigned char c : key) {
        s += "0123456789abcdef"[c >> 4]; s += "0123456789abcdef"[c & 15];
    }
    return s;
}
static std::string active_cache;
static std::string id(const std::string& key, const std::string& vin = "WBY8P610007L21042") {
    json cache;
    std::ifstream input(active_cache); input >> cache;
    return cache.at("vehicles").at(vin).at(key).at("id").get<std::string>();
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
    active_cache = cache;
    HomiePublisher publisher(cache, std::chrono::seconds(scenario == "cache-interval" ? 2 : 60));
    if (scenario == "indexed-collision") {
        const std::string first = "vehicle.collision.test", second = "vehicle.collision-test";
        const std::string other_vin = "WBA00000000000001";
        publisher.ingest(vin, {{first, {{"value", 1}}}}); publisher.tick(true);
        const auto first_id = id(first);
        publisher.ingest(vin, {{second, {{"value", 2}}}}); publisher.tick(true);
        const auto second_id = id(second);
        assert(first_id == "collisionTest" && first_id != second_id);
        publisher.ingest(other_vin, {{second, {{"value", 3}}}}); publisher.tick(true);
        publisher.ingest(other_vin, {{first, {{"value", 4}}}}); publisher.tick(true);
        assert(id(second, other_vin) == first_id && id(first, other_vin) != first_id);
        for (int i = 10; i < 20; ++i) {
            messages.clear();
            publisher.ingest(vin, {{first, {{"value", i}}}, {second, {{"value", i + 1}}}});
            publisher.tick(true);
            assert(latest(base + "telemetry/" + first_id) == std::to_string(i));
            assert(latest(base + "telemetry/" + second_id) == std::to_string(i + 1));
            assert(latest(base + "$homie") == "<missing>");
        }
        publisher.ingest(vin, {{first, {{"value", 20}, {"unit", "km"}}}}); publisher.tick(true);
        assert(id(first) == first_id && id(second) == second_id);
        assert(id(second, other_vin) == first_id);
        assert(latest(base + "telemetry/$properties") == first_id + "," + second_id + ",lastUpdate");
    } else if (scenario == "indexed-restore") {
        const std::string first = "vehicle.collision.test", second = "vehicle.collision-test";
        publisher.tick(true);
        messages.clear();
        publisher.ingest(vin, {{first, {{"value", 42}}}, {second, {{"value", 43}}}});
        publisher.tick(true);
        assert(latest(base + "telemetry/existingTest42") == "42");
        assert(latest(base + "telemetry/collisionTest") == "43");
        assert(latest(base + "$homie") == "<missing>");
        publisher.ingest(vin, {{"vehicle.collision_test", {{"value", 44}}}}); publisher.tick(true);
        assert(id(first) == "existingTest42" && id(second) == "collisionTest");
        const auto added = id("vehicle.collision_test");
        assert(added != "existingTest42" && added != "collisionTest");
        assert(latest(base + "telemetry/" + added) == "44");
    } else if (scenario == "performance") {
        const auto clock = std::chrono::steady_clock::now();
        const auto receipt = std::chrono::system_clock::time_point(std::chrono::seconds(1791115200));
        json fields = json::object();
        for (int i = 0; i < 100; ++i) fields["vehicle.audit.field" + std::to_string(i)] = {{"value", i}};
        publisher.ingest(vin, fields, receipt);
        publisher.tick(true, clock);
        assert(cache_writes == 1); // New registry is persisted immediately.
        messages.clear(); cache_writes = 0;
        for (int i = 1; i <= 60; ++i) {
            publisher.ingest(vin, {{"vehicle.audit.field0", {{"value", i}}}}, receipt + std::chrono::seconds(i));
            publisher.tick(true, clock + std::chrono::seconds(i));
        }
        assert(messages.size() == 120 && cache_writes == 1);
        std::cout << "60 changed updates / 100 fields: publications=" << messages.size() << " cache_writes=" << cache_writes << '\n';
        messages.clear(); cache_writes = 0;
        publisher.ingest(vin, {{"vehicle.audit.field0", {{"value", 60}}}}, receipt + std::chrono::seconds(61));
        publisher.tick(true, clock + std::chrono::seconds(61));
        assert(messages.size() == 1 && messages[0].first == base + "telemetry/lastUpdate");
        assert(cache_writes == 0);
        std::cout << "Identical value / fresh receipt: publications=" << messages.size() << " cache_writes=" << cache_writes << '\n';
        messages.clear();
        clients[0]->connect_cb(clients[0], clients[0]->context, 0);
        publisher.tick(true, clock + std::chrono::seconds(62));
        assert(latest(base + "$homie") == "4.0.0" && latest(base + "telemetry/auditField0") == "60");
        assert(latest(base + "telemetry/auditField99") == "99");
        publisher.shutdown();
        assert(cache_writes == 1); // Shutdown flushes uncheckpointed receipt time.
        json persisted;
        std::ifstream input(cache); input >> persisted;
        assert(persisted.at("last_updates").at(vin) == "2026-10-04T12:01:01.000Z");
    } else if (scenario == "cache-interval") {
        for (int interval : {-1, 0, 86401}) {
            bool rejected = false;
            try { HomiePublisher invalid(cache, std::chrono::seconds(interval)); }
            catch (const std::invalid_argument&) { rejected = true; }
            assert(rejected);
        }
        const auto clock = std::chrono::steady_clock::now();
        publisher.ingest(vin, field); publisher.tick(true, clock);
        assert(cache_writes == 1);
        publisher.ingest(vin, {{key, {{"value", 1}, {"unit", "kWh"}}}});
        publisher.tick(true, clock + std::chrono::seconds(1));
        assert(cache_writes == 1);
        publisher.tick(true, clock + std::chrono::seconds(2));
        assert(cache_writes == 2);
        publisher.tick(true, clock + std::chrono::seconds(4));
        assert(cache_writes == 2); // Clean/idle caches do not write periodically.
    } else if (scenario == "delta-retry") {
        publisher.ingest(vin, {{key, {{"value", 0}}}, {"other", {{"value", false}}}});
        publisher.tick(true);
        messages.clear(); failure = 4;
        publisher.ingest(vin, {{key, {{"value", 1}}}});
        publisher.tick(true);
        assert(messages.empty());
        failure = 0; publisher.tick(true);
        assert(latest(base + "telemetry/" + id(key)) == "1");
        assert(latest(base + "telemetry/other") == "false");
        assert(latest(base + "$homie") == "4.0.0" && latest(base + "$state") == "ready");
    } else if (scenario == "cache-retry") {
        const auto clock = std::chrono::steady_clock::now();
        cache_failure = true;
        publisher.ingest(vin, field); publisher.tick(true, clock);
        assert(cache_writes == 1 && latest(base + "$state") == "ready");
        for (int i = 1; i < 60; ++i) publisher.tick(true, clock + std::chrono::seconds(i));
        assert(cache_writes == 1); // A failed disk must not be retried each second.
        cache_failure = false;
        publisher.tick(true, clock + std::chrono::seconds(60));
        assert(cache_writes == 2 && std::filesystem::exists(cache));
        messages.clear();
        publisher.ingest(vin, {{key, {{"value", 1}, {"unit", "Wh"}}}});
        publisher.tick(true, clock + std::chrono::seconds(61));
        assert(cache_writes == 3); // Schema change bypasses the checkpoint interval.
        assert(latest(base + "telemetry/" + id(key) + "/$unit") == "Wh");
    } else if (scenario == "invalid") {
        publisher.ingest("invalid", field);
        publisher.ingest(vin, {{"null", {{"value", nullptr}}}, {"empty", {{"value", ""}}}, {"bad", 1}});
        publisher.tick(true);
        assert(clients.empty() && messages.empty());
    } else if (scenario == "last-update") {
        const auto first = std::chrono::system_clock::time_point(std::chrono::milliseconds(1791115200123LL));
        publisher.ingest(vin, field, first);
        publisher.tick(true);
        assert(latest(base + "telemetry/lastUpdate/$name") == "Last update");
        assert(latest(base + "telemetry/lastUpdate/$datatype") == "datetime");
        assert(latest(base + "telemetry/lastUpdate/$settable") == "false");
        assert(latest(base + "telemetry/lastUpdate/$retained") == "true");
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:00.123Z");
        assert(latest(base + "telemetry/$properties").find("lastUpdate") != std::string::npos);
        messages.clear();
        publisher.tick(true);
        assert(messages.empty()); // A timer tick is not a BMW update.
        publisher.ingest(vin, {{"bad", {{"value", nullptr}}}}, first + std::chrono::seconds(1));
        publisher.tick(true);
        assert(messages.empty());
        publisher.ingest(vin, field, first + std::chrono::seconds(2)); // Identical readings still count as receipts.
        publisher.tick(true);
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:02.123Z");
        assert(latest(base + "$homie") == "<missing>");
        publisher.ingest("WBA00000000000001", field, first + std::chrono::seconds(3));
        publisher.tick(true);
        assert(latest("homie/bmw-wba00000000000001/telemetry/lastUpdate") == "2026-10-04T12:00:03.123Z");
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:02.123Z");
        messages.clear();
        clients[0]->connect_cb(clients[0], clients[0]->context, 0);
        publisher.tick(true);
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:02.123Z");
        publisher.ingest(vin, {{"last-update", {{"value", "BMW field"}}}}, first + std::chrono::seconds(4));
        publisher.tick(true);
        assert(id("last-update") != "lastUpdate");
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:04.123Z");
        assert(latest(base + "telemetry/" + id("last-update")) == "BMW field");
    } else if (scenario == "restore-last-update") {
        publisher.tick(true);
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:04.123Z");
        assert(latest(base + "telemetry/lastUpdate/$datatype") == "datetime");
    } else if (scenario == "names") {
        const std::string hood = "vehicle.body.hood.isOpen";
        const std::string tire = "vehicle.chassis.axle.row1.wheel.left.tire.pressure";
        const std::string unknown = "vehicle.newSystem.HTTPStatus";
        publisher.ingest(vin, {{hood, {{"value", false}}}, {tire, {{"value", 290}}}, {unknown, {{"value", "OK"}}}});
        publisher.tick(true);
        assert(id(hood) == "bodyHoodIsOpen");
        assert(id(tire) == "chassisAxleRow1WheelLeftTirePressure");
        assert(id(unknown) == "newSystemHttpStatus");
        assert(latest(base + "telemetry/bodyHoodIsOpen/$name") == "Hood open");
        assert(latest(base + "telemetry/chassisAxleRow1WheelLeftTirePressure/$name") == "Tire pressure — front left");
        assert(latest(base + "telemetry/newSystemHttpStatus/$name") == "New system http status");
        assert(latest(base + "telemetry/$name") == "Vehicle data");
    } else if (scenario == "migration") {
        publisher.tick(true);
        const std::string hood = "vehicle.body.hood.isOpen";
        assert(id(hood) == "bodyHoodIsOpen");
        assert(latest(base + "telemetry/bodyHoodIsOpen") == "false");
        assert(latest(base + "telemetry/bodyHoodIsOpen/$name") == "Hood open");
        for (const auto* suffix : {"", "/$name", "/$datatype", "/$settable", "/$retained", "/$unit"})
            assert(latest(base + "telemetry/" + legacy_id(hood) + suffix).empty());
        assert(latest(base + "telemetry/$properties").find(legacy_id(hood)) == std::string::npos);
        const auto receipt = std::chrono::system_clock::time_point(std::chrono::milliseconds(1791115200123LL));
        publisher.ingest(vin, {{hood, {{"value", true}}}}, receipt); publisher.tick(true);
        assert(latest(base + "telemetry/bodyHoodIsOpen") == "true");
        assert(latest(base + "telemetry/$properties") == "bodyHoodIsOpen,lastUpdate");
        publisher.ingest(vin, {{hood, {{"value", false}}}}, receipt); publisher.tick(true);
    } else if (scenario == "migration-v2") {
        publisher.tick(true);
        assert(id("vehicle.body.hood.isOpen") == "bodyHoodIsOpen");
        assert(latest(base + "telemetry/bodyHoodIsOpen") == "false");
        assert(latest(base + "telemetry/lastUpdate") == "2026-10-04T12:00:00.123Z");
        for (const auto* suffix : {"", "/$name", "/$datatype", "/$settable", "/$retained", "/$unit"}) {
            assert(latest(base + "telemetry/body-hood-is-open" + suffix).empty());
            assert(latest(base + "telemetry/last-update" + suffix).empty());
        }
        assert(latest(base + "telemetry/$properties") == "bodyHoodIsOpen,lastUpdate");
        messages.clear();
        const auto receipt = std::chrono::system_clock::time_point(std::chrono::milliseconds(1791115200123LL));
        publisher.ingest(vin, {{"vehicle.body.hood.isOpen", {{"value", true}}}}, receipt); publisher.tick(true);
        assert(latest(base + "telemetry/bodyHoodIsOpen") == "true");
        assert(latest(base + "$homie") == "<missing>");
        publisher.ingest(vin, {{"vehicle.body.hood.isOpen", {{"value", false}}}}, receipt); publisher.tick(true);
    } else if (scenario == "restore") {
        publisher.tick(true);
        assert(latest(base + "telemetry/" + id(key)) == "0.75");
        assert(latest(base + "telemetry/$properties").find(id("new.field")) != std::string::npos);
        assert(clients.size() == 2);
    } else if (scenario == "publish") {
        publisher.ingest(vin, field);
        publisher.tick(true);
        assert(clients.size() == 1 && tls_calls == 1);
        assert(id(key) == "drivetrainElectricEngineChargingSmeEnergyDeltaFullyCharged");
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
        assert(id("new-field") == "newField");
        assert(id("new.field").rfind("newFieldH", 0) == 0);
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
    // openHAB UIDUtils leaves alphanumerics unchanged; these IDs need no escaping.
    const auto properties = latest(base + "telemetry/$properties");
    if (properties != "<missing>") for (unsigned char c : properties)
        assert(c == ',' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'));
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

    def test_index_preserves_collision_ids_and_isolates_vehicles(self):
        self.run_scenario('indexed-collision', self.base / 'indexed-collision.json')

    def test_index_restores_persisted_ids_before_updates_and_new_collisions(self):
        cache = self.base / 'indexed-restore.json'
        cache.write_text(json.dumps({
            'version': 3,
            'vehicles': {'WBY8P610007L21042': {
                'vehicle.collision.test': {'id': 'existingTest42', 'datatype': 'float', 'unit': '', 'value': '1'},
                'vehicle.collision-test': {'id': 'collisionTest', 'datatype': 'float', 'unit': '', 'value': '2'},
            }},
            'last_updates': {'WBY8P610007L21042': '2026-10-04T12:00:00.123Z'},
        }))
        self.run_scenario('indexed-restore', cache)
        fields = json.loads(cache.read_text())['vehicles']['WBY8P610007L21042']
        self.assertEqual(len(fields), 3)
        self.assertEqual(fields['vehicle.collision.test']['id'], 'existingTest42')
        self.assertEqual(fields['vehicle.collision-test']['id'], 'collisionTest')

    def test_delta_publications_checkpoint_interval_and_shutdown_flush(self):
        result = self.run_scenario('performance', self.base / 'performance.json')
        self.assertIn('publications=120 cache_writes=1', result.stdout)
        self.assertIn('publications=1 cache_writes=0', result.stdout)

    def test_failed_cache_writes_back_off_without_stopping_publication(self):
        self.run_scenario('cache-retry', self.base / 'cache-retry.json')

    def test_custom_cache_interval_validation_and_idle_writes(self):
        self.run_scenario('cache-interval', self.base / 'cache-interval.json')

    def test_failed_delta_publications_retry_current_values_and_full_schema(self):
        self.run_scenario('delta-retry', self.base / 'delta-retry.json')

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

    def test_readable_ids_known_labels_and_unknown_field_fallback(self):
        self.run_scenario('names', self.base / 'names.json')

    def test_last_update_tracks_receipt_per_vehicle_and_survives_restarts(self):
        cache = self.base / 'last-update.json'
        self.run_scenario('last-update', cache)
        self.run_scenario('restore-last-update', cache)

    def test_legacy_cache_migrates_values_and_clears_retained_topics(self):
        cache = self.base / 'migration.json'
        cache.write_text(json.dumps({
            'version': 1,
            'vehicles': {'WBY8P610007L21042': {
                'vehicle.body.hood.isOpen': {
                    'datatype': 'boolean', 'unit': '', 'value': 'false',
                },
            }},
        }))
        self.run_scenario('migration', cache)
        self.assertEqual(json.loads(cache.read_text())['version'], 3)
        # Retried cleanup must survive another restart after the cache was migrated.
        self.run_scenario('migration', cache)

    def test_hyphenated_cache_migrates_to_camelcase_including_last_update(self):
        cache = self.base / 'migration-v2.json'
        cache.write_text(json.dumps({
            'version': 2,
            'vehicles': {'WBY8P610007L21042': {
                'vehicle.body.hood.isOpen': {
                    'id': 'body-hood-is-open', 'datatype': 'boolean', 'unit': '', 'value': 'false',
                },
            }},
            'last_updates': {'WBY8P610007L21042': '2026-10-04T12:00:00.123Z'},
        }))
        self.run_scenario('migration-v2', cache)
        self.assertEqual(json.loads(cache.read_text())['version'], 3)
        self.run_scenario('migration-v2', cache)
