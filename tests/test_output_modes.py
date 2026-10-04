"""Verify independent output switches using the production BMW message callback."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
class OutputModeTests(unittest.TestCase):
    def test_raw_split_homie_and_retain_combinations(self):
        source = (ROOT / 'resources/src/bmw_mqtt_bridge.cpp').read_text()
        callback = source[source.index('static void on_bmw_message('):]
        callback = callback.split('// Log MQTT failures')[0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / 'outputs.cpp'
            path.write_text(r'''
#include <cassert>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include "json.hpp"
using json = nlohmann::json;
struct mosquitto {};
struct mosquitto_message { char* topic; void* payload; int payloadlen; };
static int BMB_MQTT_RAW_TOPICS = 1, BMB_MQTT_SPLIT_TOPICS = 0, BMB_MQTT_HOMIE = 0, BMB_MQTT_RETAIN = 0;
static std::string BMB_MQTT_LOCAL_PREFIX = "bmw/520d/";
struct Publication { std::string topic, payload; bool retain; };
static std::vector<Publication> publications;
static int publish_local(const std::string& topic, const std::string& payload, bool retain) {
    publications.push_back({topic, payload, retain}); return 0;
}
static std::string sanitize_key(std::string key) { return key; }
struct HomiePublisher {
    int calls = 0;
    void ingest(const std::string& vin, const json& fields) {
        assert(vin == "WBA31AJ090CN29196");
        assert(fields.at("vehicle.body.hood.isOpen").at("value") == false);
        ++calls;
    }
};
static std::unique_ptr<HomiePublisher> g_homie;
''' + callback + r'''
int main() {
    std::string topic = "account/WBA31AJ090CN29196";
    std::string payload = R"({"vin":"WBA31AJ090CN29196","data":{"vehicle.body.hood.isOpen":{"value":false}}})";
    mosquitto_message message{topic.data(), payload.data(), static_cast<int>(payload.size())};
    for (int raw : {0, 1}) for (int split : {0, 1}) for (int homie : {0, 1}) for (int retain : {0, 1}) {
        BMB_MQTT_RAW_TOPICS = raw;
        BMB_MQTT_SPLIT_TOPICS = split;
        BMB_MQTT_HOMIE = homie;
        BMB_MQTT_RETAIN = retain;
        publications.clear();
        g_homie = homie ? std::make_unique<HomiePublisher>() : nullptr;
        on_bmw_message(nullptr, nullptr, &message);
        assert(publications.size() == static_cast<size_t>(raw * 2 + split));
        if (raw) {
            assert(publications[0].topic == "bmw/520d/raw/WBA31AJ090CN29196");
            assert(publications[1].topic == "bmw/520d/WBA31AJ090CN29196");
            assert(publications[0].payload == payload && publications[1].payload == payload);
        }
        if (split) {
            assert(publications.back().topic == "bmw/520d/vehicles/WBA31AJ090CN29196/vehicle.body.hood.isOpen");
            assert(json::parse(publications.back().payload).at("value") == false);
        }
        for (const auto& publication : publications) assert(publication.retain == (retain != 0));
        if (homie) assert(g_homie->calls == 1);
    }
    on_bmw_message(nullptr, nullptr, nullptr);
}
''')
            binary = base / 'outputs'
            compiled = subprocess.run(['c++', '-std=c++17', '-I', str(ROOT / 'resources/src'),
                                       str(path), '-o', str(binary)], text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
