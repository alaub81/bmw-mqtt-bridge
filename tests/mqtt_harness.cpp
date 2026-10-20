// Exercise the production Homie publisher against an actual loopback broker.
#include <mosquitto.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <thread>
#include "json.hpp"
using json = nlohmann::json;
static std::string BMB_MQTT_LOCAL_HOST = "127.0.0.1", BMB_MQTT_LOCAL_USER, BMB_MQTT_LOCAL_PASSWORD;
static int BMB_MQTT_LOCAL_PORT = 18883;
static bool configure_local_tls(mosquitto*) { return true; }
static bool write_file_atomic(const std::string& path, const std::string& content) {
    const auto temporary = path + ".tmp";
    { std::ofstream out(temporary); out << content; if (!out.good()) return false; }
    std::filesystem::rename(temporary, path);
    return true;
}
#include "homie_publisher.hpp"
int main() {
    mosquitto_lib_init();
    {
        HomiePublisher publisher("/tmp/cache.json", std::chrono::seconds(1));
        std::string command;
        while (std::getline(std::cin, command)) {
            if (command == "stop") break;
            if (command != "tick") publisher.ingest("WBA00000000000001", {{"vehicle.test.value", {{"value", std::stoi(command)}}}});
            // Allow asynchronous connection/cache completion without blocking callbacks.
            for (int i = 0; i < 100; ++i) {
                publisher.tick(true);
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            std::cout << "done" << std::endl;
        }
        publisher.shutdown();
    }
    mosquitto_lib_cleanup();
}
