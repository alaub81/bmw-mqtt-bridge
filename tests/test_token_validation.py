"""Reject malformed JWT/refresh responses using the production refresh path."""
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return f'header.{payload}.signature'


class TokenValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        callback = source[source.index('static size_t curl_write_cb('):].split('// ===================== MQTT Callbacks')[0]
        refresh = source[source.rindex('static token_validation::Tokens fetch_and_store_tokens() {'):]
        options = sorted(set(re.findall(r'CURLOPT_[A-Z_]+', refresh)))
        harness = r'''
#include <atomic>
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <string>
#include <type_traits>
#include <vector>
#include "bridge_log.hpp"
#include "token_validation.hpp"
#include "async_job.hpp"
struct CURL {};
struct curl_slist {};
using CURLcode = int;
using curl_off_t = long long;
static std::atomic<bool> g_stop{false};
constexpr int CURLE_OK = 0, CURLINFO_RESPONSE_CODE = 0;
static CURL handle;
static curl_slist headers;
static std::string response, current_refresh = "current-refresh";
static std::string* response_target = nullptr;
static long status = 200;
static bool write_failure = false;
static std::map<std::string, std::string> writes;
static std::string ID_TOKEN_FILE = "/state/id_token.txt";
static std::string BMB_BMW_CLIENT_ID = "audit-client", g_id_token = "previous-id";
static std::atomic<long> g_id_token_exp{100};
CURL* curl_easy_init() { return &handle; }
void curl_easy_cleanup(CURL*) {}
curl_slist* curl_slist_append(curl_slist*, const char*) { return &headers; }
void curl_slist_free_all(curl_slist*) {}
template<typename T> void curl_easy_setopt(CURL*, int, T value) {
    if constexpr (std::is_same_v<T, std::string*>) response_target = value;
}
CURLcode curl_easy_perform(CURL*) { *response_target = response; return CURLE_OK; }
void curl_easy_getinfo(CURL*, int, long* code) { *code = status; }
const char* curl_easy_strerror(int) { return "injected error"; }
static std::string build_form_body(const std::vector<std::pair<std::string, std::string>>&) { return "test-form"; }
static bool write_file_atomic(const std::string& path, const std::string& value) {
    if (write_failure) return false;
    writes[path] = value; return true;
}
namespace token_store {
static bool load(const std::filesystem::path&, token_validation::Tokens& tokens) {
    tokens.refresh = current_refresh;
    return token_validation::valid_token(current_refresh);
}
template<typename Writer>
static bool save(const std::filesystem::path& directory, const token_validation::Tokens& tokens, Writer writer) {
    return writer((directory / "tokens.json").string(), nlohmann::json{
        {"version", 1}, {"id_token", tokens.id}, {"refresh_token", tokens.refresh}, {"access_token", tokens.access}}.dump());
}
}
'''
        harness += 'enum { ' + ', '.join(options) + ' };\n' + callback + refresh + r'''
int main(int argc, char** argv) {
    const std::string mode = argv[1];
    std::ifstream file(argv[2]);
    response = std::string(std::istreambuf_iterator<char>(file), {});
    if (mode == "worker-refresh") {
        AsyncJob<token_validation::Tokens> job;
        assert(job.start(fetch_and_store_tokens));
        const auto tokens = job.finish();
        assert(tokens && tokens->expires > time(nullptr) && writes.size() == 1);
        assert(g_id_token == "previous-id" && g_id_token_exp == 100); // Worker never changes active credentials.
        return 0;
    }
    if (mode == "cancel-callback") {
        assert(refresh_cancel_cb(nullptr, 0, 0, 0, 0) == 0);
        g_stop = true;
        assert(refresh_cancel_cb(nullptr, 0, 0, 0, 0) == 1);
        return 0;
    }
    if (mode == "expiry") {
        std::cout << token_validation::expiration(response) << '\n';
        return 0;
    }
    if (mode == "callback") {
        std::string output;
        char byte = 'x';
        assert(curl_write_cb(&byte, 1, 1, &output) == 1 && output == "x");
        assert(curl_write_cb(&byte, 1024 * 1024, 2, &output) == 0);
        assert(curl_write_cb(&byte, 1, static_cast<size_t>(-1), &output) == 0);
        output.assign(1024 * 1024, 'x');
        assert(curl_write_cb(&byte, 1, 1, &output) == 0);
        return 0;
    }
    if (mode == "write-error") write_failure = true;
    if (mode == "http-error") status = 500;
    if (mode == "bad-refresh-file") current_refresh = "embedded whitespace";
    const bool success = refresh_tokens();
    if (mode == "valid") {
        assert(success && writes.size() == 1 && g_id_token != "previous-id");
        assert(g_id_token_exp > time(nullptr));
        const auto bundle = nlohmann::json::parse(writes.at("/state/tokens.json"));
        const auto received = nlohmann::json::parse(response);
        for (const auto* key : {"id_token", "refresh_token", "access_token"})
            assert(bundle.at(key) == received.at(key));
    } else {
        assert(!success && writes.empty() && g_id_token == "previous-id" && g_id_token_exp == 100);
    }
}
'''
        path = cls.base / 'refresh.cpp'
        path.write_text(harness)
        cls.binary = cls.base / 'refresh'
        result = subprocess.run(['c++', '-std=c++17', '-pthread', '-Wall', '-Wextra',
                                 '-I', str(ROOT / 'resources/src'), str(path), '-o', str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            cls.temp.cleanup()
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, mode, response):
        path = self.base / 'response'
        path.write_text(response)
        result = subprocess.run([str(self.binary), mode, str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def valid_response(self):
        return {'id_token': jwt({'exp': int(time.time()) + 3600}),
                'refresh_token': 'sensitive-refresh', 'access_token': 'sensitive-access'}

    def test_invalid_expiry_types_and_encoding_return_zero_without_throwing(self):
        for claims in ({'exp': 'not-a-number'}, {'exp': None}, {'exp': True}, {'exp': []},
                       {'exp': 1.5}, {'exp': -1}, {'exp': 0}, {'exp': 2**64 - 1}, {}, [], 'value'):
            with self.subTest(claims=claims):
                self.assertEqual(self.run_case('expiry', jwt(claims)).stdout.strip(), '0')
        for invalid in ('no-jwt', 'header.!.signature', 'header.A.signature', 'header.e30.',
                        'header.e30.signature.extra', 'header.e3=.signature'):
            with self.subTest(jwt=invalid):
                self.assertEqual(self.run_case('expiry', invalid).stdout.strip(), '0')
        self.assertEqual(self.run_case('expiry', jwt({'exp': 12345})).stdout.strip(), '12345')

    def test_refresh_rejects_invalid_responses_without_writing_or_logging_tokens(self):
        responses = ['{broken', 'null', '[]', '"value"', '{}']
        for key in ('id_token', 'refresh_token', 'access_token'):
            for invalid in (None, 123, [], {}, '', 'bad token', 'bad\ntoken', '\0'):
                value = self.valid_response()
                value[key] = invalid
                responses.append(json.dumps(value))
        for claims in ({'exp': 'invalid'}, {'exp': int(time.time()) - 1}, {}, []):
            value = self.valid_response()
            value['id_token'] = jwt(claims)
            responses.append(json.dumps(value))
        value = self.valid_response()
        value['error'] = 'sensitive-error-details'
        responses.append(json.dumps(value))
        for response in responses:
            with self.subTest(response=response):
                result = self.run_case('invalid', response)
                self.assertNotIn('sensitive-', result.stdout + result.stderr)

    def test_valid_refresh_updates_memory_and_one_token_bundle(self):
        self.run_case('valid', json.dumps(self.valid_response()))

    def test_failed_bundle_commit_does_not_adopt_new_credentials(self):
        result = self.run_case('write-error', json.dumps(self.valid_response()))
        self.assertNotIn('sensitive-', result.stdout + result.stderr)

    def test_refresh_worker_returns_owned_tokens_without_changing_active_credentials(self):
        self.run_case('worker-refresh', json.dumps(self.valid_response()))

    def test_http_progress_callback_cancels_on_shutdown(self):
        self.run_case('cancel-callback', '')

    def test_http_error_and_invalid_current_refresh_do_not_expose_response_body(self):
        for mode in ('http-error', 'bad-refresh-file'):
            result = self.run_case(mode, json.dumps(self.valid_response()))
            self.assertNotIn('sensitive-', result.stdout + result.stderr)

    def test_refresh_response_callback_limits_size_and_prevents_overflow(self):
        self.run_case('callback', '')


if __name__ == '__main__':
    unittest.main()
