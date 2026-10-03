#pragma once

#include <map>
#include <memory>

// Homie 4 needs one MQTT connection (and therefore one Last Will) per device.
// Ingest runs on the BMW callback thread; all registry and client management runs
// on the main thread. Client callbacks only touch atomic flags and ACK state.
class HomiePublisher {
    struct Property {
        std::string name, datatype, unit, value;
    };
    struct Device {
        std::string id;
        std::map<std::string, Property> properties;
        mosquitto* client = nullptr;
        std::atomic<bool> connected{false}, resend{false};
        bool dirty = true;
        bool values_dirty = true;
        std::string state;
        std::chrono::steady_clock::time_point last_online{};
        std::mutex ack_mutex;
        std::condition_variable ack_condition;
        int shutdown_mid = 0;
        bool shutdown_ack = false;
    };
    std::map<std::string, std::unique_ptr<Device>> devices;
    std::mutex pending_mutex;
    std::vector<std::pair<std::string, json>> pending;
    std::string cache_path;
    bool cache_dirty = false;

    static std::string property_id(const std::string& key) {
        // Reversible encoding avoids collisions, including case and punctuation.
        static constexpr char hex[] = "0123456789abcdef";
        std::string result = "p-";
        for (unsigned char c : key) {
            result += hex[c >> 4];
            result += hex[c & 15];
        }
        return result;
    }
    static bool valid_vin(const std::string& vin) {
        return vin.size() == 17 && std::all_of(vin.begin(), vin.end(), [](unsigned char c) {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        });
    }
    static std::string device_id(std::string vin) {
        std::transform(vin.begin(), vin.end(), vin.begin(), [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });
        return "bmw-" + vin;
    }
    Device& device(const std::string& vin) {
        auto& entry = devices[vin];
        if (!entry) {
            entry = std::make_unique<Device>();
            entry->id = device_id(vin);
        }
        return *entry;
    }
    static void on_connect(mosquitto*, void* context, int rc) {
        auto& d = *static_cast<Device*>(context);
        std::cerr << "[homie] " << d.id << " CONNACK rc=" << rc
                  << " (" << mosquitto_connack_string(rc) << ")\n";
        d.connected = rc == 0;
        if (rc == 0) d.resend = true;
    }
    static void on_disconnect(mosquitto*, void* context, int rc) {
        auto& d = *static_cast<Device*>(context);
        d.connected = false;
        std::cerr << "[homie] " << d.id << " disconnected rc=" << rc
                  << " (" << mosquitto_strerror(rc) << ")\n";
    }
    static void on_log(mosquitto*, void* context, int level, const char* message) {
        if (!(level & (MOSQ_LOG_ERR | MOSQ_LOG_WARNING))) return;
        const auto& d = *static_cast<Device*>(context);
        std::cerr << "[homie/log] " << d.id << " level=" << level << " "
                  << (message ? message : "") << '\n';
    }
    static void on_publish(mosquitto*, void* context, int mid) {
        auto& d = *static_cast<Device*>(context);
        std::lock_guard<std::mutex> lock(d.ack_mutex);
        if (d.shutdown_mid != 0 && d.shutdown_mid == mid) {
            d.shutdown_ack = true;
            d.ack_condition.notify_all();
        }
    }
    static bool publish(Device& d, const std::string& suffix, const std::string& value) {
        const std::string topic = "homie/" + d.id + "/" + suffix;
        const int rc = mosquitto_publish(d.client, nullptr, topic.c_str(),
                                         static_cast<int>(value.size()), value.data(), 1, true);
        if (rc != MOSQ_ERR_SUCCESS) {
            std::cerr << "[homie] publish failed topic='" << topic
                      << "': " << mosquitto_strerror(rc) << '\n';
            return false;
        }
        return true;
    }
    static void stop_client(Device& d, bool clean = false) {
        if (!d.client) return;
        // Without a confirmed offline publication, let the broker deliver lost.
        if (clean) mosquitto_disconnect(d.client);
        mosquitto_loop_stop(d.client, true);
        mosquitto_destroy(d.client);
        d.client = nullptr;
        d.connected = false;
    }
    static bool start_client(Device& d) {
        d.last_online = std::chrono::steady_clock::now();
        // A generated client ID prevents collisions with the regular local client.
        d.client = mosquitto_new(nullptr, true, &d);
        if (!d.client) {
            std::cerr << "[homie] " << d.id << " client allocation failed\n";
            return false;
        }
        mosquitto_connect_callback_set(d.client, on_connect);
        mosquitto_disconnect_callback_set(d.client, on_disconnect);
        mosquitto_publish_callback_set(d.client, on_publish);
        mosquitto_log_callback_set(d.client, on_log);
        mosquitto_reconnect_delay_set(d.client, 1, 10, true);
        const std::string state_topic = "homie/" + d.id + "/$state";
        int rc = mosquitto_will_set(d.client, state_topic.c_str(), 4, "lost", 1, true);
        if (rc == MOSQ_ERR_SUCCESS && !BMB_MQTT_LOCAL_USER.empty()) {
            rc = mosquitto_username_pw_set(d.client, BMB_MQTT_LOCAL_USER.c_str(),
                                           BMB_MQTT_LOCAL_PASSWORD.c_str());
        }
        if (rc != MOSQ_ERR_SUCCESS || !configure_local_tls(d.client)) {
            std::cerr << "[homie] " << d.id << " client configuration failed rc=" << rc << '\n';
            stop_client(d);
            return false;
        }
        rc = mosquitto_connect_async(d.client, BMB_MQTT_LOCAL_HOST.c_str(), BMB_MQTT_LOCAL_PORT, 30);
        if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_loop_start(d.client);
        std::cerr << "[homie] " << d.id << " connect queued host='" << BMB_MQTT_LOCAL_HOST
                  << "' port=" << BMB_MQTT_LOCAL_PORT << " rc=" << rc
                  << " (" << mosquitto_strerror(rc) << ")\n";
        if (rc != MOSQ_ERR_SUCCESS) {
            std::cerr << "[homie] connection failed: " << mosquitto_strerror(rc) << '\n';
            stop_client(d);
            return false;
        }
        return true;
    }
    static bool describe(Device& d) {
        // Reannounce the complete schema before ready, including later additions.
        if (!publish(d, "$state", "init")) return false;
        bool ok = publish(d, "$homie", "4.0.0");
        ok &= publish(d, "$name", "BMW " + d.id.substr(4));
        ok &= publish(d, "$extensions", "");
        ok &= publish(d, "$nodes", "telemetry");
        ok &= publish(d, "telemetry/$name", "Vehicle telemetry");
        ok &= publish(d, "telemetry/$type", "bmw-cardata");
        std::string ids;
        for (const auto& [id, p] : d.properties) {
            if (!ids.empty()) ids += ',';
            ids += id;
            const std::string base = "telemetry/" + id;
            ok &= publish(d, base + "/$name", p.name);
            ok &= publish(d, base + "/$datatype", p.datatype);
            ok &= publish(d, base + "/$settable", "false");
            ok &= publish(d, base + "/$retained", "true");
            // An empty retained payload clears an obsolete optional unit.
            ok &= publish(d, base + "/$unit", p.unit);
        }
        ok &= publish(d, "telemetry/$properties", ids);
        return ok;
    }
    void load() {
        try {
            if (!std::filesystem::exists(cache_path)) return;
            std::ifstream input(cache_path);
            json cache;
            input >> cache;
            if (cache.at("version") != 1 || !cache.at("vehicles").is_object())
                throw std::runtime_error("unsupported cache format");
            // Validate the entire file before installing any cached devices.
            std::map<std::string, std::unique_ptr<Device>> restored;
            for (const auto& [vin, fields] : cache.at("vehicles").items()) {
                if (!valid_vin(vin) || !fields.is_object()) throw std::runtime_error("invalid cached vehicle");
                auto entry = std::make_unique<Device>();
                entry->id = device_id(vin);
                for (const auto& [name, field] : fields.items()) {
                    Property p{name, field.at("datatype").get<std::string>(),
                               field.at("unit").get<std::string>(), field.at("value").get<std::string>()};
                    if (name.empty() || (p.datatype != "float" && p.datatype != "boolean" && p.datatype != "string"))
                        throw std::runtime_error("invalid cached property");
                    entry->properties.emplace(property_id(name), std::move(p));
                }
                if (!entry->properties.empty()) restored.emplace(vin, std::move(entry));
            }
            devices = std::move(restored);
            std::cerr << "[homie] restored " << devices.size() << " vehicle(s) from field cache\n";
        } catch (const std::exception& e) {
            std::cerr << "[homie] cannot load field cache: " << e.what() << '\n';
        }
    }
    void save() {
        if (!cache_dirty) return;
        json vehicles = json::object();
        for (const auto& [vin, d] : devices) {
            for (const auto& [id, p] : d->properties) {
                vehicles[vin][p.name] = {{"datatype", p.datatype}, {"unit", p.unit}, {"value", p.value}};
            }
        }
        if (write_file_atomic(cache_path, json{{"version", 1}, {"vehicles", vehicles}}.dump())) {
            cache_dirty = false;
        } else {
            std::cerr << "[homie] cannot save field cache\n";
        }
    }

public:
    explicit HomiePublisher(const std::string& path) : cache_path(path) { load(); }
    ~HomiePublisher() { shutdown(); }
    void ingest(const std::string& vin, const json& fields) {
        if (!valid_vin(vin) || !fields.is_object()) return;
        std::lock_guard<std::mutex> lock(pending_mutex);
        pending.emplace_back(vin, fields);
    }
    void tick(bool bmw_connected) {
        std::vector<std::pair<std::string, json>> updates;
        {
            std::lock_guard<std::mutex> lock(pending_mutex);
            updates.swap(pending);
        }
        for (const auto& [vin, fields] : updates) {
            for (const auto& [name, field] : fields.items()) {
                if (name.empty() || !field.is_object() || !field.contains("value") || field["value"].is_null()) continue;
                const auto& value = field["value"];
                Property p{name, value.is_boolean() ? "boolean" : value.is_number() ? "float" : "string",
                           field.contains("unit") && field["unit"].is_string() ? field["unit"].get<std::string>() : "",
                           value.is_string() ? value.get<std::string>() : value.dump()};
                // Homie 4 does not allow empty property payloads.
                if (p.value.empty()) continue;
                auto& d = device(vin);
                const auto id = property_id(name);
                const auto previous = d.properties.find(id);
                if (previous == d.properties.end()) {
                    std::cerr << "[homie] " << d.id << " new property '" << name << "'\n";
                }
                // Never narrow numeric properties based on an initial integer 0.
                const bool schema_changed = previous == d.properties.end() ||
                    previous->second.datatype != p.datatype || previous->second.unit != p.unit;
                d.properties[id] = std::move(p);
                d.dirty |= schema_changed;
                cache_dirty = true;
                // Values are sent after the schema below, even for first sightings.
                d.values_dirty = true;
            }
        }
        save();
        const auto now = std::chrono::steady_clock::now();
        for (auto& [vin, entry] : devices) {
            auto& d = *entry;
            if (d.connected) d.last_online = now;
            if ((!d.client && d.last_online == std::chrono::steady_clock::time_point{}) ||
                (!d.connected && now - d.last_online >= std::chrono::seconds(30))) {
                if (d.last_online != std::chrono::steady_clock::time_point{})
                    std::cerr << "[homie] " << d.id << " offline for 30s; rebuilding client\n";
                stop_client(d);
                start_client(d);
            }
            if (!d.connected) continue;
            const bool resend = d.resend.exchange(false);
            if (d.dirty || resend || d.values_dirty) {
                // Full replay also recovers from a broker losing its retained store.
                const bool schema_update = d.dirty || resend;
                bool ok = !schema_update || describe(d);
                if (ok) {
                    for (const auto& [id, p] : d.properties)
                        ok &= publish(d, "telemetry/" + id, p.value);
                }
                const std::string state = bmw_connected ? "ready" : "alert";
                if (ok) ok = publish(d, "$state", state);
                if (ok) d.state = state;
                d.dirty = !ok;
                d.values_dirty = !ok;
                if (ok && schema_update) {
                    std::cerr << "[homie] description and " << d.properties.size()
                              << " value(s) queued under homie/" << d.id << "/\n";
                }
            } else {
                // Availability is separate from cached telemetry, which may be old.
                const std::string state = bmw_connected ? "ready" : "alert";
                if (state != d.state) {
                    if (publish(d, "$state", state)) d.state = state;
                    else d.dirty = true;
                }
            }
        }
    }
    void shutdown() {
        save();
        for (auto& [vin, entry] : devices) {
            auto& d = *entry;
            bool acknowledged = false;
            if (d.client && d.connected) {
                std::unique_lock<std::mutex> lock(d.ack_mutex);
                const std::string topic = "homie/" + d.id + "/$state";
                const int rc = mosquitto_publish(d.client, &d.shutdown_mid, topic.c_str(), 12,
                                                 "disconnected", 1, true);
                if (rc == MOSQ_ERR_SUCCESS) {
                    acknowledged = d.ack_condition.wait_for(lock, std::chrono::seconds(2), [&d] { return d.shutdown_ack; });
                }
            }
            stop_client(d, acknowledged);
        }
        devices.clear();
    }
};
