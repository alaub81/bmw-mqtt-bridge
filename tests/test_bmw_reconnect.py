"""Exercise BMW callbacks, backoff and watchdog with simulated MQTT reconnects."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class BmwReconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helpers = source[source.index('static long long bmw_clock_ms('):]
        helpers = helpers.split('// Debounced status publisher')[0]
        # Drive the production monotonic clock without sleeping or changing system time.
        helpers = helpers.replace('std::chrono::steady_clock::now()', 'test_now()')
        callbacks = source.split('// v5 connect callback')[1]
        callbacks = callbacks[callbacks.index('static void on_bmw_connect_v5'):]
        callbacks = callbacks.split('static void on_bmw_message(')[0]
        logging = source[source.index('static void on_bmw_log('):]
        logging = logging.split('static void on_bmw_suback(')[0]
        path = Path(cls.temp.name) / 'bmw-reconnect.cpp'
        path.write_text(r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
struct mosquitto { bool disconnect_requested = false, socket_open = true; };
struct mosquitto_property {};
static mosquitto* g_bmw = nullptr;
static std::atomic<bool> g_connected{false}, g_stop{false}, g_bmw_reconnect_pending{false};
static std::atomic<long long> g_next_connect_after{0};
static std::string BMB_BMW_HOST = "bmw.test", BMB_BMW_GCID = "test-account";
static int BMB_BMW_PORT = 9000;
constexpr int MOSQ_ERR_SUCCESS = 0, MOSQ_ERR_NO_CONN = 4, MOSQ_LOG_ERR = 1, MOSQ_LOG_WARNING = 2;
static int created = 0, destroyed = 0, attempts = 0, disconnects = 0, subscriptions = 0;
static int connect_result = 0, loop_result = 0, join_reason = 0;
static bool allocation_failure = false, connect_precedes_loop = false;
static const auto start = std::chrono::steady_clock::time_point(std::chrono::seconds(1000));
static auto fake_now = start;
static auto test_now() { return fake_now; }
static void on_bmw_disconnect_v5(mosquitto*, void*, int, const mosquitto_property*);
void publish_status() {}
const char* mosquitto_strerror(int) { return "test result"; }
const char* mosquitto_reason_string(int) { return "test reason"; }
mosquitto* create_bmw_client() {
    ++created;
    return allocation_failure ? nullptr : new mosquitto;
}
int mosquitto_disconnect(mosquitto* client) {
    ++disconnects;
    client->disconnect_requested = true;
    const bool open = client->socket_open;
    client->socket_open = false;
    return open ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NO_CONN;
}
int mosquitto_loop_stop(mosquitto* client, bool force) {
    assert(force);
    if (join_reason) on_bmw_disconnect_v5(client, nullptr, join_reason, nullptr);
    // A callback may finish during the join; rebuilding must clear its state.
    g_connected = true;
    return 0;
}
void mosquitto_destroy(mosquitto* client) { ++destroyed; delete client; }
int mosquitto_connect_async(mosquitto* client, const char* host, int port, int keepalive) {
    assert(client == g_bmw && std::string(host) == BMB_BMW_HOST && port == BMB_BMW_PORT && keepalive == 30);
    ++attempts;
    connect_precedes_loop = true;
    client->disconnect_requested = false;
    return connect_result;
}
int mosquitto_loop_start(mosquitto*) { assert(connect_precedes_loop); return loop_result; }
int mosquitto_subscribe(mosquitto*, int* mid, const char* topic, int qos) {
    assert(std::string(topic) == "test-account/+" && qos == 1);
    *mid = ++subscriptions;
    return 0;
}
''' + helpers + callbacks + logging + r'''
static void tick(long seconds) {
    fake_now = start + std::chrono::seconds(seconds);
    check_bmw_connection(fake_now);
}
static void library_reconnect() {
    // Model loop_start's automatic retry unless disconnect was explicitly requested.
    if (g_bmw && !g_bmw->disconnect_requested && !g_connected) {
        mosquitto_connect_async(g_bmw, BMB_BMW_HOST.c_str(), BMB_BMW_PORT, 30);
    }
}
int main(int argc, char** argv) {
    const std::string scenario = argv[1];
    assert(bmw_full_reconnect());
    tick(0);
    if (scenario == "outage-after-success") {
        g_connected = true;
        tick(10);
        g_connected = false;
        tick(39);
        assert(created == 1);
        tick(40);
        assert(created == 2 && destroyed == 1 && !g_connected);
        tick(69);
        assert(created == 2);
        tick(70);
        assert(created == 3);
        g_connected = true;
        tick(100);
        assert(created == 3);
    } else if (scenario == "backoff") {
        extend_bmw_backoff(60);
        tick(59);
        assert(created == 1);
        tick(60);
        assert(created == 2);
    } else if (scenario == "allocation" || scenario == "dns" || scenario == "loop") {
        allocation_failure = scenario == "allocation";
        connect_result = scenario == "dns" ? 15 : 0;
        loop_result = scenario == "loop" ? 7 : 0;
        tick(30);
        assert(created == 2 && !g_bmw && !g_connected);
        tick(59);
        assert(created == 2);
        allocation_failure = false;
        connect_result = loop_result = 0;
        tick(60);
        assert(created == 3 && g_bmw);
    } else if (scenario == "shutdown") {
        on_bmw_connect_v5(g_bmw, nullptr, 151, 0, nullptr);
        g_stop = true;
        tick(60);
        assert(created == 1 && !bmw_full_reconnect());
    } else if (scenario.compare(0, 8, "connect-") == 0 || scenario.compare(0, 11, "disconnect-") == 0) {
        const bool server_disconnect = scenario.compare(0, 11, "disconnect-") == 0;
        const int reason = std::stoi(scenario.substr(server_disconnect ? 11 : 8));
        const long delay = reason == 151 ? 60 : reason == 135 ? 30 :
                           (reason == 128 || reason == 136 || reason == 137) ? 20 : 5;
        if (server_disconnect) {
            on_bmw_connect_v5(g_bmw, nullptr, 0, 0, nullptr);
            // A server DISCONNECT reaches the callback with an already-closed socket.
            g_bmw->socket_open = false;
            on_bmw_disconnect_v5(g_bmw, nullptr, reason, nullptr);
        } else {
            on_bmw_connect_v5(g_bmw, nullptr, reason, 0, nullptr);
        }
        assert(g_bmw_reconnect_pending && disconnects == 1 && !g_connected);
        assert(g_next_connect_after == bmw_clock_ms(start) + delay * 1000);
        for (long second = 1; second < delay; ++second) {
            tick(second);
            library_reconnect();
            assert(attempts == 1 && created == 1);
            assert(!bmw_full_reconnect()); // Token rotation cannot bypass the pause either.
        }
        tick(delay);
        assert(attempts == 2 && created == 2 && !g_bmw_reconnect_pending);
        on_bmw_connect_v5(g_bmw, nullptr, 0, 0, nullptr);
        assert(g_connected && g_next_connect_after == 0 && subscriptions > 0);
    } else if (scenario == "shorter-errors") {
        on_bmw_connect_v5(g_bmw, nullptr, 151, 0, nullptr);
        const auto deadline = g_next_connect_after.load();
        fake_now = start + std::chrono::seconds(10);
        on_bmw_log(g_bmw, nullptr, MOSQ_LOG_ERR, "SSL error");
        extend_bmw_backoff(1); // Successful token refresh.
        extend_bmw_backoff(15); // Failed token refresh.
        on_bmw_disconnect_v5(g_bmw, nullptr, 135, nullptr);
        assert(g_next_connect_after == deadline);
        tick(59);
        library_reconnect();
        assert(attempts == 1);
        tick(60);
        assert(attempts == 2);
    } else if (scenario == "longer-error") {
        on_bmw_connect_v5(g_bmw, nullptr, 132, 0, nullptr);
        fake_now = start + std::chrono::seconds(1);
        on_bmw_disconnect_v5(g_bmw, nullptr, 151, nullptr);
        tick(60);
        library_reconnect();
        assert(attempts == 1);
        tick(61);
        assert(attempts == 2);
    } else if (scenario == "join-callback") {
        join_reason = 151;
        tick(30);
        assert(created == 1 && destroyed == 1 && !g_bmw && !g_connected);
        assert(g_bmw_reconnect_pending);
        join_reason = 0;
        tick(89);
        assert(attempts == 1);
        tick(90);
        assert(attempts == 2 && created == 2);
    } else if (scenario == "ordinary-network-error") {
        on_bmw_connect_v5(g_bmw, nullptr, 0, 0, nullptr);
        on_bmw_disconnect_v5(g_bmw, nullptr, 7, nullptr); // MOSQ_ERR_CONN_LOST
        tick(1);
        library_reconnect();
        assert(disconnects == 0 && attempts == 2 && created == 1 && !g_bmw_reconnect_pending);
    } else if (scenario == "concurrent-errors") {
        std::thread short_error([] { for (int i = 0; i < 5000; ++i) extend_bmw_backoff(5); });
        std::thread long_error([] { for (int i = 0; i < 5000; ++i) extend_bmw_backoff(60); });
        short_error.join();
        long_error.join();
        assert(g_next_connect_after == bmw_clock_ms(start) + 60000);
    } else { return 1; }
    if (g_bmw) mosquitto_destroy(g_bmw);
}
''')
        cls.binary = Path(cls.temp.name) / 'bmw-reconnect'
        subprocess.run(['c++', '-std=c++17', '-pthread', str(path), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def run_scenarios(self, scenarios):
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_from_stopped_loop_and_startup_failures(self):
        self.run_scenarios(('outage-after-success', 'backoff', 'allocation', 'dns', 'loop', 'shutdown'))

    def test_server_backoff_suppresses_library_reconnects_until_deadline(self):
        self.run_scenarios(f'{event}-{reason}' for event in ('connect', 'disconnect')
                           for reason in (151, 135, 128, 136, 137, 132))

    def test_backoff_survives_other_errors_and_late_callbacks(self):
        self.run_scenarios(('shorter-errors', 'longer-error', 'join-callback', 'concurrent-errors'))

    def test_ordinary_network_errors_keep_library_reconnects(self):
        self.run_scenarios(('ordinary-network-error',))
