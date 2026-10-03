"""Exercise the production BMW watchdog without a network or BMW account."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class BmwReconnectTests(unittest.TestCase):
    def test_recovery_from_stopped_loop_and_startup_failures(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        helpers = source[source.index('static bool bmw_full_reconnect()'):]
        helpers = helpers.split('// Debounced status publisher')[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bmw-reconnect.cpp'
            path.write_text(r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <ctime>
#include <iostream>
#include <string>
struct mosquitto {};
static mosquitto* g_bmw = nullptr;
static std::atomic<bool> g_connected{false}, g_stop{false};
static std::atomic<long> g_next_connect_after{0};
static std::string BMW_HOST = "bmw.test";
static int BMW_PORT = 9000;
constexpr int MOSQ_ERR_SUCCESS = 0;
static int created = 0, destroyed = 0, connect_result = 0, loop_result = 0;
static bool allocation_failure = false, connect_precedes_loop = false;
const char* mosquitto_strerror(int) { return "test result"; }
mosquitto* create_bmw_client() {
    ++created;
    return allocation_failure ? nullptr : new mosquitto;
}
int mosquitto_loop_stop(mosquitto*, bool force) {
    assert(force);
    // A callback may finish during the join; rebuilding must clear its state.
    g_connected = true;
    return 0;
}
void mosquitto_destroy(mosquitto* client) { ++destroyed; delete client; }
int mosquitto_connect_async(mosquitto* client, const char* host, int port, int keepalive) {
    assert(client == g_bmw && std::string(host) == BMW_HOST && port == BMW_PORT && keepalive == 30);
    connect_precedes_loop = true;
    return connect_result;
}
int mosquitto_loop_start(mosquitto*) { assert(connect_precedes_loop); return loop_result; }
''' + helpers + r'''
int main(int argc, char** argv) {
    const std::string scenario = argv[1];
    const auto start = std::chrono::steady_clock::time_point{};
    assert(bmw_full_reconnect());
    check_bmw_connection(start);
    if (scenario == "outage-after-success") {
        g_connected = true;
        check_bmw_connection(start + std::chrono::seconds(10));
        g_connected = false;
        check_bmw_connection(start + std::chrono::seconds(39));
        assert(created == 1);
        check_bmw_connection(start + std::chrono::seconds(40));
        assert(created == 2 && destroyed == 1 && !g_connected);
        check_bmw_connection(start + std::chrono::seconds(69));
        assert(created == 2);
        check_bmw_connection(start + std::chrono::seconds(70));
        assert(created == 3);
        g_connected = true;
        check_bmw_connection(start + std::chrono::seconds(100));
        assert(created == 3);
    } else if (scenario == "backoff") {
        g_next_connect_after = time(nullptr) + 3600;
        check_bmw_connection(start + std::chrono::seconds(60));
        assert(created == 1);
        g_next_connect_after = 0;
        check_bmw_connection(start + std::chrono::seconds(61));
        assert(created == 2);
    } else if (scenario == "allocation" || scenario == "dns" || scenario == "loop") {
        allocation_failure = scenario == "allocation";
        connect_result = scenario == "dns" ? 15 : 0;
        loop_result = scenario == "loop" ? 7 : 0;
        check_bmw_connection(start + std::chrono::seconds(30));
        assert(created == 2 && !g_bmw && !g_connected);
        check_bmw_connection(start + std::chrono::seconds(59));
        assert(created == 2);
        allocation_failure = false;
        connect_result = loop_result = 0;
        check_bmw_connection(start + std::chrono::seconds(60));
        assert(created == 3 && g_bmw);
    } else if (scenario == "shutdown") {
        g_stop = true;
        check_bmw_connection(start + std::chrono::seconds(60));
        assert(created == 1);
    } else { return 1; }
    if (g_bmw) mosquitto_destroy(g_bmw);
}
''')
            binary = Path(directory) / 'bmw-reconnect'
            subprocess.run(['c++', '-std=c++17', str(path), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            for scenario in ('outage-after-success', 'backoff', 'allocation', 'dns', 'loop', 'shutdown'):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario], capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
