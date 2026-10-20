"""Real libcurl against a loopback HTTP server; no BMW credentials or network."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs

BASE = Path(__file__).resolve().parent


def build():
    source = (BASE / 'src/bmw_mqtt_bridge.cpp').read_text()
    callbacks = source[source.index('static size_t curl_write_cb('):].split('// ===================== MQTT Callbacks')[0]
    refresh = source[source.index('static std::string urlencode_component('):]
    endpoint = '"https://customer.bmwgroup.com/gcdm/oauth/token"'
    if refresh.count(endpoint) != 1:
        raise ValueError('Cannot select the production refresh endpoint')
    refresh = refresh.replace(endpoint, 'test_endpoint')
    harness = r'''
#include <curl/curl.h>
#include <atomic>
#include <cassert>
#include <chrono>
#include <iostream>
#include <sstream>
#include <thread>
#include <vector>
#include "atomic_file.hpp"
#include "token_store.hpp"
#include "async_job.hpp"
static std::atomic<bool> g_stop{false};
static std::string ID_TOKEN_FILE, test_endpoint;
static std::string BMB_BMW_CLIENT_ID = "test-client", g_id_token = "old-id";
static std::atomic<long> g_id_token_exp{0};
'''
    harness += callbacks + refresh + r'''
int main(int argc, char** argv) {
    assert(argc == 4);
    test_endpoint = argv[1];
    ID_TOKEN_FILE = (std::filesystem::path(argv[2]) / "id_token.txt").string();
    const bool cancel = std::string(argv[3]) == "cancel";
    assert(curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK);
    long ticks = 0;
    const auto started = std::chrono::steady_clock::now();
    token_validation::Tokens result;
    {
        AsyncJob<token_validation::Tokens> job;
        assert(job.start(fetch_and_store_tokens));
        assert(!job.start([] { assert(false); return token_validation::Tokens{}; }));
        while (true) {
            ++ticks; // Represents work remaining responsive while HTTP is in flight.
            if (auto completed = job.poll()) { result = std::move(*completed); break; }
            const auto elapsed = std::chrono::steady_clock::now() - started;
            assert(elapsed < std::chrono::seconds(27));
            if (cancel && elapsed >= std::chrono::milliseconds(300)) {
                g_stop = true;
                // Production shutdown joins the worker before curl_global_cleanup.
                result = *job.finish();
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        assert(g_id_token == "old-id" && g_id_token_exp == 0);
    }
    curl_global_cleanup();
    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    std::cout << nlohmann::json{{"seconds", seconds}, {"ticks", ticks}, {"success", result.expires > time(nullptr)}} << '\n';
}
'''
    (BASE / 'http.cpp').write_text(harness)
    subprocess.run(['g++', '-std=c++17', '-O2', '-pthread', '-I', str(BASE / 'src'),
                    str(BASE / 'http.cpp'), '-lcurl', '-o', str(BASE / 'http')], check=True)


class SlowHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.old = {'version': 1, 'id_token': 'old-id', 'refresh_token': 'old-refresh', 'access_token': 'old-access'}
        self.bundle = self.directory / 'tokens.json'
        self.bundle.write_text(json.dumps(self.old))
        payload = base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 3600}).encode()).decode().rstrip('=')
        self.new = {'id_token': f'header.{payload}.signature', 'refresh_token': 'new-secret-refresh', 'access_token': 'new-secret-access'}
        self.requests = []
        self.release = threading.Event()
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                test.requests.append(parse_qs(body.decode()))
                if self.path == '/no-headers':
                    test.release.wait(30)
                    return
                content = json.dumps(test.new).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                if self.path == '/stalled-body':
                    self.wfile.write(content[:1])
                    self.wfile.flush()
                    test.release.wait(30)
                    return
                try:
                    for offset in range(0, len(content), 10):
                        self.wfile.write(content[offset:offset + 10])
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def run_refresh(self, path, cancel=False):
        url = f'http://127.0.0.1:{self.server.server_port}{path}'
        result = subprocess.run([str(BASE / 'http'), url, str(self.directory), 'cancel' if cancel else 'wait'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('new-secret-', result.stdout + result.stderr)
        self.assertEqual(self.requests, [{'grant_type': ['refresh_token'], 'refresh_token': ['old-refresh'], 'client_id': ['test-client']}])
        self.assertFalse(list(self.directory.glob('tokens.json.tmp.*')))
        report = json.loads(result.stdout)
        self.assertGreater(report['ticks'], 10)
        return report

    def test_slow_response_keeps_polling_and_commits_complete_bundle(self):
        report = self.run_refresh('/slow')
        self.assertTrue(report['success'])
        self.assertGreater(report['seconds'], 0.5)
        self.assertEqual(json.loads(self.bundle.read_text()), dict(self.new, version=1))
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o600)

    def test_production_timeout_preserves_tokens_while_body_stalls(self):
        before = self.bundle.read_bytes()
        report = self.run_refresh('/stalled-body')
        self.assertFalse(report['success'])
        self.assertGreaterEqual(report['seconds'], 19)
        self.assertLess(report['seconds'], 25)
        self.assertEqual(self.bundle.read_bytes(), before)

    def test_shutdown_cancels_before_headers_and_during_partial_body(self):
        for path in ['/no-headers', '/stalled-body']:
            with self.subTest(path=path):
                self.requests.clear()
                before = self.bundle.read_bytes()
                report = self.run_refresh(path, cancel=True)
                self.assertFalse(report['success'])
                self.assertLess(report['seconds'], 5)
                self.assertEqual(self.bundle.read_bytes(), before)


if __name__ == '__main__':
    if sys.argv[1:] == ['--build']:
        build()
    else:
        unittest.main(verbosity=2)
