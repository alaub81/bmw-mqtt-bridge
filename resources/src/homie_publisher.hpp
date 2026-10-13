#pragma once

#include <map>
#include <deque>
#include <unordered_map>
#include <memory>
#include <set>
#include <ctime>
#include <iomanip>
#include <sstream>
#include "homie_names.hpp"
#include "bridge_log.hpp"

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
        // Rebuilt from the existing registry on load; never persisted or used
        // to order discovery, so cached IDs and collision assignment stay stable.
        std::unordered_map<std::string, std::string> property_ids;
        // Keep migration tombstones to retry retained cleanup on every reconnect.
        std::set<std::string> legacy_ids;
        mosquitto* client = nullptr;
        std::atomic<bool> connected{false}, resend{false};
        bool dirty = true;
        std::set<std::string> changed_values;
        bool timestamp_dirty = false;
        std::string state;
        std::string last_update;
        std::chrono::steady_clock::time_point last_online{};
        std::mutex ack_mutex;
        std::condition_variable ack_condition;
        int shutdown_mid = 0;
        bool shutdown_ack = false;
    };
    std::map<std::string, std::unique_ptr<Device>> devices;
    std::mutex pending_mutex;
    struct Update {
        std::string vin;
        json fields;
        std::string received_at;
    };
    std::deque<Update> pending;
    const size_t queue_limit;
    size_t dropped_updates = 0; // Protected by pending_mutex.
    std::string cache_path;
    bool cache_dirty = false;
    bool cache_schema_dirty = false;
    bool cache_retry_pending = false;
    std::chrono::seconds cache_interval;
    std::chrono::steady_clock::time_point last_cache_attempt = std::chrono::steady_clock::now();

    static std::string timestamp(std::chrono::system_clock::time_point received_at) {
        const auto seconds = std::chrono::time_point_cast<std::chrono::seconds>(received_at);
        const auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(received_at - seconds).count();
        const std::time_t time = std::chrono::system_clock::to_time_t(seconds);
        std::tm utc{};
        if (!gmtime_r(&time, &utc)) throw std::runtime_error("cannot format Homie receipt timestamp");
        std::ostringstream out;
        out << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S") << '.'
            << std::setfill('0') << std::setw(3) << milliseconds << 'Z';
        return out.str();
    }

    static std::string legacy_property_id(const std::string& key) {
        // Reversible encoding avoids collisions, including case and punctuation.
        static constexpr char hex[] = "0123456789abcdef";
        std::string result = "p-";
        for (unsigned char c : key) {
            result += hex[c >> 4];
            result += hex[c & 15];
        }
        return result;
    }
    static std::string property_id(const Device& d, const std::string& key) {
        const auto known = d.property_ids.find(key);
        if (known != d.property_ids.end()) return known->second;
        const std::string base = homie_names::field_id(key);
        if (base != "lastUpdate" && d.properties.count(base) == 0) return base;
        std::string id = base + "H" + homie_names::suffix(key);
        // Even an unlikely hash collision must not overwrite another property.
        while (d.properties.count(id) != 0) id += "X";
        return id;
    }
    static bool valid_property_id(const std::string& id) {
        return !id.empty() && id.front() != '-' && id.back() != '-' &&
            std::all_of(id.begin(), id.end(), [](unsigned char c) {
                return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-';
            });
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
    static bool valid_field(const std::string& name, const json& field) {
        if (name.empty() || !field.is_object() || !field.contains("value")) return false;
        const auto& value = field.at("value");
        return !value.is_null() && (!value.is_string() || !value.get_ref<const std::string&>().empty());
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
        BMB_LOG(info) << "[homie] " << d.id << " CONNACK rc=" << rc
                  << " (" << mosquitto_connack_string(rc) << ")\n";
        d.connected = rc == 0;
        if (rc == 0) d.resend = true;
    }
    static void on_disconnect(mosquitto*, void* context, int rc) {
        auto& d = *static_cast<Device*>(context);
        d.connected = false;
        BMB_LOG(warning) << "[homie] " << d.id << " disconnected rc=" << rc
                  << " (" << mosquitto_strerror(rc) << ")\n";
    }
    static void on_log(mosquitto*, void* context, int level, const char* message) {
        if (!(level & (MOSQ_LOG_ERR | MOSQ_LOG_WARNING))) return;
        const auto& d = *static_cast<Device*>(context);
        BMB_LOG_THROTTLED_AT((level & MOSQ_LOG_ERR) ? bridge_log::Level::error : bridge_log::Level::warning)
            << "[homie/log] " << d.id << " level=" << level << " "
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
            BMB_LOG_THROTTLED(error) << "[homie] publish failed topic='" << topic
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
            BMB_LOG(error) << "[homie] " << d.id << " client allocation failed\n";
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
            BMB_LOG(error) << "[homie] " << d.id << " client configuration failed rc=" << rc << '\n';
            stop_client(d);
            return false;
        }
        rc = mosquitto_connect_async(d.client, BMB_MQTT_LOCAL_HOST.c_str(), BMB_MQTT_LOCAL_PORT, 30);
        if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_loop_start(d.client);
        BMB_LOG(info) << "[homie] " << d.id << " connect queued host='" << BMB_MQTT_LOCAL_HOST
                  << "' port=" << BMB_MQTT_LOCAL_PORT << " rc=" << rc
                  << " (" << mosquitto_strerror(rc) << ")\n";
        if (rc != MOSQ_ERR_SUCCESS) {
            BMB_LOG(error) << "[homie] connection failed: " << mosquitto_strerror(rc) << '\n';
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
        ok &= publish(d, "telemetry/$name", "Vehicle data");
        ok &= publish(d, "telemetry/$type", "bmw-cardata");
        std::string ids;
        for (const auto& [id, p] : d.properties) {
            if (!ids.empty()) ids += ',';
            ids += id;
            const std::string base = "telemetry/" + id;
            ok &= publish(d, base + "/$name", homie_names::label(p.name));
            ok &= publish(d, base + "/$datatype", p.datatype);
            ok &= publish(d, base + "/$settable", "false");
            ok &= publish(d, base + "/$retained", "true");
            // An empty retained payload clears an obsolete optional unit.
            ok &= publish(d, base + "/$unit", p.unit);
        }
        if (!d.last_update.empty()) {
            if (!ids.empty()) ids += ',';
            ids += "lastUpdate";
            ok &= publish(d, "telemetry/lastUpdate/$name", "Last update");
            // openHAB's Homie binding supports datetime directly, without a transform.
            ok &= publish(d, "telemetry/lastUpdate/$datatype", "datetime");
            ok &= publish(d, "telemetry/lastUpdate/$settable", "false");
            ok &= publish(d, "telemetry/lastUpdate/$retained", "true");
            ok &= publish(d, "telemetry/lastUpdate/$unit", "");
        }
        ok &= publish(d, "telemetry/$properties", ids);
        if (ok) {
            for (const auto& id : d.legacy_ids) {
                if (id == "lastUpdate" || d.properties.count(id) != 0) continue;
                const std::string base = "telemetry/" + id;
                for (const auto* suffix : {"", "/$name", "/$datatype", "/$settable", "/$retained", "/$unit"})
                    ok &= publish(d, base + suffix, "");
            }
        }
        return ok;
    }
    void load() {
        try {
            if (!std::filesystem::exists(cache_path)) return;
            std::ifstream input(cache_path);
            json cache;
            input >> cache;
            const int version = cache.at("version").get<int>();
            if ((version != 1 && version != 2 && version != 3) || !cache.at("vehicles").is_object())
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
                    const std::string id = version < 3 ? property_id(*entry, name) : field.at("id").get<std::string>();
                    if (version == 3 && (id == "lastUpdate" || id.find('-') != std::string::npos))
                        throw std::runtime_error("invalid cached alphanumeric property ID");
                    if (!valid_property_id(id) || !entry->properties.emplace(id, std::move(p)).second)
                        throw std::runtime_error("invalid or duplicate cached property ID");
                    entry->property_ids.emplace(name, id);
                    if (version == 1) entry->legacy_ids.insert(legacy_property_id(name));
                    if (version == 2) {
                        const std::string previous_id = field.at("id").get<std::string>();
                        if (!valid_property_id(previous_id)) throw std::runtime_error("invalid cached previous ID");
                        entry->legacy_ids.insert(previous_id);
                    }
                }
                if (cache.contains("last_updates") && cache.at("last_updates").contains(vin))
                    entry->last_update = cache.at("last_updates").at(vin).get<std::string>();
                if (version < 3 && !entry->last_update.empty()) entry->legacy_ids.insert("last-update");
                if (version >= 2 && cache.contains("legacy_ids") && cache.at("legacy_ids").contains(vin)) {
                    for (const auto& id : cache.at("legacy_ids").at(vin)) {
                        const auto legacy = id.get<std::string>();
                        if (!valid_property_id(legacy)) throw std::runtime_error("invalid cached legacy ID");
                        entry->legacy_ids.insert(legacy);
                    }
                }
                if (!entry->properties.empty()) restored.emplace(vin, std::move(entry));
            }
            devices = std::move(restored);
            cache_dirty = version < 3;
            cache_schema_dirty = cache_dirty;
            BMB_LOG(info) << "[homie] restored " << devices.size() << " vehicle(s) from field cache\n";
        } catch (const std::exception& e) {
            BMB_LOG(warning) << "[homie] cannot load field cache: " << e.what() << '\n';
        }
    }
    void save(std::chrono::steady_clock::time_point now, bool force = false) {
        if (!cache_dirty) return;
        // Schema additions/migrations are durable immediately. Value/receipt
        // updates are checkpointed; failed saves retry at the same interval.
        if (!force && now - last_cache_attempt < cache_interval && (!cache_schema_dirty || cache_retry_pending)) return;
        last_cache_attempt = now;
        json vehicles = json::object();
        json legacy_ids = json::object();
        json last_updates = json::object();
        for (const auto& [vin, d] : devices) {
            for (const auto& [id, p] : d->properties) {
                vehicles[vin][p.name] = {{"id", id}, {"datatype", p.datatype}, {"unit", p.unit}, {"value", p.value}};
            }
            if (!d->legacy_ids.empty()) legacy_ids[vin] = d->legacy_ids;
            if (!d->last_update.empty()) last_updates[vin] = d->last_update;
        }
        if (write_file_atomic(cache_path, json{{"version", 3}, {"vehicles", vehicles},
                                              {"legacy_ids", legacy_ids}, {"last_updates", last_updates}}.dump())) {
            cache_dirty = false;
            cache_schema_dirty = false;
            cache_retry_pending = false;
        } else {
            cache_retry_pending = true;
            BMB_LOG_THROTTLED(error) << "[homie] cannot save field cache\n";
        }
    }

public:
    explicit HomiePublisher(const std::string& path, std::chrono::seconds interval = std::chrono::seconds(60),
                            size_t pending_limit = 1024)
        : queue_limit(pending_limit), cache_path(path), cache_interval(interval) {
        if (interval.count() < 1 || interval.count() > 86400)
            throw std::invalid_argument("BMB_HOMIE_CACHE_INTERVAL must be between 1 and 86400 seconds");
        if (pending_limit < 1 || pending_limit > 65536)
            throw std::invalid_argument("BMB_HOMIE_QUEUE_LIMIT must be between 1 and 65536 updates");
        load();
    }
    ~HomiePublisher() { shutdown(); }
    void ingest(const std::string& vin, json fields,
                std::chrono::system_clock::time_point received_at = std::chrono::system_clock::now()) {
        if (!valid_vin(vin) || !fields.is_object()) return;
        bool accepted = false;
        for (const auto& [name, field] : fields.items()) {
            if (valid_field(name, field)) { accepted = true; break; }
        }
        if (!accepted) return; // Invalid/empty messages must not evict valid work.
        // Format receipt time before taking the queue lock. Keep FIFO order so
        // first-seen collision IDs and intermediate schema changes stay stable.
        Update update{vin, std::move(fields), timestamp(received_at)};
        size_t dropped = 0;
        {
            std::lock_guard<std::mutex> lock(pending_mutex);
            if (pending.size() >= queue_limit) {
                pending.pop_front();
                dropped = ++dropped_updates;
            }
            pending.push_back(std::move(update));
        }
        if (dropped) {
            BMB_LOG_THROTTLED(warning) << "[homie] pending queue full; dropped oldest update; total_dropped="
                                       << dropped << " limit=" << queue_limit << '\n';
        }
    }
    void tick(bool bmw_connected, std::chrono::steady_clock::time_point now = std::chrono::steady_clock::now()) {
        std::deque<Update> updates;
        {
            std::lock_guard<std::mutex> lock(pending_mutex);
            updates.swap(pending);
        }
        for (const auto& [vin, fields, received_at] : updates) {
            bool accepted = false;
            for (const auto& [name, field] : fields.items()) {
                if (!valid_field(name, field)) continue;
                const auto& value = field["value"];
                Property p{name, value.is_boolean() ? "boolean" : value.is_number() ? "float" : "string",
                           field.contains("unit") && field["unit"].is_string() ? field["unit"].get<std::string>() : "",
                           value.is_string() ? value.get<std::string>() : value.dump()};
                // Homie 4 does not allow empty property payloads.
                if (p.value.empty()) continue;
                auto& d = device(vin);
                const auto id = property_id(d, name);
                const auto previous = d.properties.find(id);
                if (previous == d.properties.end()) {
                    BMB_LOG(debug) << "[homie] " << d.id << " new property '" << name << "'\n";
                }
                // Never narrow numeric properties based on an initial integer 0.
                const bool schema_changed = previous == d.properties.end() ||
                    previous->second.datatype != p.datatype || previous->second.unit != p.unit;
                const bool value_changed = schema_changed || previous->second.value != p.value;
                const bool new_property = previous == d.properties.end();
                d.properties[id] = std::move(p);
                if (new_property) d.property_ids.emplace(name, id);
                d.dirty |= schema_changed;
                cache_dirty |= value_changed;
                cache_schema_dirty |= schema_changed;
                // Values are sent after the schema below, even for first sightings.
                if (value_changed) d.changed_values.insert(id);
                accepted = true;
            }
            if (accepted) {
                auto& d = device(vin);
                if (d.last_update.empty()) d.dirty = true;
                if (d.last_update != received_at) {
                    d.timestamp_dirty = true;
                    cache_dirty = true;
                }
                d.last_update = received_at;
            }
        }
        save(now);
        for (auto& [vin, entry] : devices) {
            auto& d = *entry;
            if (d.connected) d.last_online = now;
            if ((!d.client && d.last_online == std::chrono::steady_clock::time_point{}) ||
                (!d.connected && now - d.last_online >= std::chrono::seconds(30))) {
                if (d.last_online != std::chrono::steady_clock::time_point{})
                    BMB_LOG(warning) << "[homie] " << d.id << " offline for 30s; rebuilding client\n";
                stop_client(d);
                start_client(d);
            }
            if (!d.connected) continue;
            const bool resend = d.resend.exchange(false);
            if (d.dirty || resend || !d.changed_values.empty() || d.timestamp_dirty) {
                // Full replay also recovers from a broker losing its retained store.
                const bool schema_update = d.dirty || resend;
                bool ok = !schema_update || describe(d);
                if (ok) {
                    if (schema_update) {
                        for (const auto& [id, p] : d.properties)
                            ok &= publish(d, "telemetry/" + id, p.value);
                    } else {
                        for (const auto& id : d.changed_values)
                            ok &= publish(d, "telemetry/" + id, d.properties.at(id).value);
                    }
                    if (!d.last_update.empty() && (schema_update || d.timestamp_dirty))
                        ok &= publish(d, "telemetry/lastUpdate", d.last_update);
                }
                const std::string state = bmw_connected ? "ready" : "alert";
                if (ok && (schema_update || state != d.state)) ok = publish(d, "$state", state);
                if (ok) d.state = state;
                d.dirty = !ok;
                if (ok) {
                    d.changed_values.clear();
                    d.timestamp_dirty = false;
                }
                if (ok && schema_update) {
                    BMB_LOG(info) << "[homie] description and " << d.properties.size()
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
        save(std::chrono::steady_clock::now(), true);
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
