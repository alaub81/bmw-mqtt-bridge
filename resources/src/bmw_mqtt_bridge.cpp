/*
 * bmw_mqtt_bridge.cpp
 *
 * Bridge BMW CarData MQTT → Local Mosquitto
 * Copyright (c) 2025 Kurt, DJ0ABR
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */


// bmw_mqtt_bridge.cpp
//
// Purpose:
//   Bridge BMW CarData Streaming MQTT → local Mosquitto (republish as bmw/<VIN>/...)
//   Uses: libmosquitto (MQTT v5), libcurl (HTTPS token refresh), nlohmann/json (header-only)
//
// Features:
//   - MQTT v5 with reason codes
//   - Token expiry tracking via JWT "exp" claim
//   - Soft/Hard token refresh via HTTP refresh (no external script required at runtime)
//   - Connect watchdog + client rebuild
//   - Backoff (incl. jitter) to avoid quota/rate-limit storms
//   - LWT on local broker + status topic
//
// Built in the Docker builder stage using resources/compile.sh.
//
// Runtime configuration (env overrides):
//   BMW_CLIENT_ID         : BMW CarData client ID (GUID)              (required; no default)
//   BMW_GCID              : BMW GCID / username for the MQTT broker   (required; no default)
//   BMW_HOST              : customer.streaming-cardata.bmwgroup.com   (default: set)
//   BMW_PORT              : 9000                                      (default: 9000)
//   MQTT_LOCAL_HOST       : host.docker.internal                      (default: host.docker.internal)
//   MQTT_LOCAL_PORT       : 1883                                      (default: 1883)
//   MQTT_LOCAL_PREFIX     : bmw/                                      (default: bmw/)
//   MQTT_LOCAL_CLIENT_ID  : local MQTT client ID (empty = generated ID)
//   MQTT_LOCAL_USER       : (optional)
//   MQTT_LOCAL_PASSWORD   : (optional)
//   MQTT_LOCAL_TLS        : true/false (default false); encrypt local MQTT
//   MQTT_LOCAL_TLS_VERIFY : true/false (default true); verify certificate chain and hostname
//   MQTT_LOCAL_TLS_CA_FILE: PEM CA file (default system CA bundle)
//   MQTT_RETAIN           : 0/1 (default 0; status is always retained)
//   MQTT_SPLIT_TOPICS     : 0/1  (default: 0; split JSON into per-signal topics)
//   BMW_STATUS_STABLE_DELAY : seconds until bmw/status goes to false (default: 5; 0 = immediately)
//
//
// Notes:
//   - id_token (a JWT) is used as the MQTT password; we parse its 'exp' to know validity.
//   - Token files written by this program use permissions 0644.
//
// ------------------------------------------------------------------------

#include <mosquitto.h>
#include <curl/curl.h>
#include <random>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cctype>
#include <stdexcept>
#include <cstring>
#include <fstream>
#include <iostream>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <vector>
#include <string>
#include <sstream>
#include <algorithm>
#include <unistd.h>     // access()
#include <ctime>
#include <filesystem>
#include <sys/stat.h>
#include <fcntl.h>
#include <regex>

#ifndef NLOHMANN_JSON_HPP
  #include "json.hpp" // nlohmann/json header (json.hpp next to this file)
#endif
using json = nlohmann::json;

static bool refresh_tokens();
static mosquitto* create_bmw_client();

// ---------------------- tiny helpers for env config ----------------------
static std::string env_str(const char* key, const char* defv){
    const char* v = std::getenv(key);
    return v && *v ? std::string(v) : std::string(defv);
}
static int env_int(const char* key, int defv){
    const char* v = std::getenv(key);
    if(!v || !*v) return defv;
    try{
        size_t consumed = 0;
        int value = std::stoi(v, &consumed);
        if (v[consumed] != '\0') throw std::invalid_argument("trailing characters");
        return value;
    }catch(const std::exception&){
        throw std::invalid_argument(std::string(key) + " must be an integer");
    }
}
static bool env_switch(const char* key, bool default_value) {
    std::string value = env_str(key, default_value ? "true" : "false");
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (value == "true") return true;
    if (value == "false") return false;
    throw std::invalid_argument(std::string(key) + " must be true or false");
}
// ===================== Configuration =====================
static std::string BMW_CLIENT_ID;
static std::string BMW_GCID;
static std::string BMW_HOST;
static int         BMW_PORT;
static std::string MQTT_LOCAL_HOST;
static int         MQTT_LOCAL_PORT;
static std::string MQTT_LOCAL_PREFIX;
static std::string MQTT_LOCAL_CLIENT_ID;
static std::string MQTT_LOCAL_USER;
static std::string MQTT_LOCAL_PASSWORD;
static std::string MQTT_LOCAL_STATUS_TOPIC;
static int         MQTT_SPLIT_TOPICS = 0;
static int         BMW_STATUS_STABLE_DELAY = 5; // seconds; 0 = no delay
static std::string ID_TOKEN_FILE;
static std::string REFRESH_TOKEN_FILE;
static int         MQTT_RETAIN = 0; // 0 = no retain (default), 1 = retain

// ===================== Globals =====================
static std::atomic<bool> g_stop{false};
static std::string g_id_token;
static std::atomic<long> g_id_token_exp{0};

static mosquitto* g_bmw = nullptr;
static mosquitto* g_local = nullptr;
// Serialize publishing and pointer replacement, but never hold this while joining a loop thread.
static std::mutex g_local_mutex;

static std::atomic<bool> g_connected{false};
static std::atomic<bool> g_local_connected{false};
static std::atomic<bool> g_status_resend{false};
static std::mutex g_shutdown_mutex;
static std::condition_variable g_shutdown_condition;
static int g_shutdown_mid = 0;
static bool g_shutdown_acknowledged = false;
static std::atomic<long> g_next_connect_after{0}; // backoff fence for (re)connects

static std::mt19937 rng{std::random_device{}()};

// ===================== Helpers =====================
// MQTT_LOCAL_TLS_VERIFY controls both chain and hostname verification.
// Configure only the local client; BMW always retains its existing TLS policy.
static bool configure_local_tls(mosquitto* client) {
    try {
        if (!env_switch("MQTT_LOCAL_TLS", false)) return true;
        const bool verify = env_switch("MQTT_LOCAL_TLS_VERIFY", true);
        const std::string ca_file = env_str("MQTT_LOCAL_TLS_CA_FILE",
                                           "/etc/ssl/certs/ca-certificates.crt");
        int rc = mosquitto_tls_set(client, ca_file.c_str(), nullptr,
                                   nullptr, nullptr, nullptr);
        if (rc == MOSQ_ERR_SUCCESS) {
            // cert_reqs: 1 = verify the certificate chain, 0 = no verification.
            rc = mosquitto_tls_opts_set(client, verify ? 1 : 0, nullptr, nullptr);
        }
        if (rc == MOSQ_ERR_SUCCESS) {
            rc = mosquitto_tls_insecure_set(client, !verify);
        }
        if (rc != MOSQ_ERR_SUCCESS) {
            std::cerr << "[bridge] local TLS configuration failed: "
                      << mosquitto_strerror(rc) << '\n';
            return false;
        }
        std::cerr << "[bridge] local MQTT TLS enabled; certificate verification "
                  << (verify ? "true" : "false") << '\n';
        return true;
    } catch (const std::invalid_argument& error) {
        std::cerr << "[bridge] " << error.what() << '\n';
        return false;
    }
}

// Helper: dirname
static std::string dirname_of(const std::string& p){
    std::filesystem::path pp(p);
    auto d = pp.parent_path();
    return d.empty() ? std::string(".") : d.string();
}

// Internal container paths shared with Compose and the authentication helper.
static std::string token_dir() {
    return env_str("BMW_TOKEN_DIR", "/app/token");
}

// Health telemetry: main-loop liveness and continuous MQTT downtime.
static long mqtt_offline_seconds(bool connected, std::chrono::steady_clock::time_point now,
                                 std::chrono::steady_clock::time_point& last_online) {
    if (connected) last_online = now;
    return std::chrono::duration_cast<std::chrono::seconds>(now - last_online).count();
}

static bool write_heartbeat(const std::string& path, long local_offline = 0, long bmw_offline = 0) {
    if (path.empty()) return true;
    const std::string temporary = path + ".tmp";
    std::ofstream output(temporary, std::ios::trunc);
    if (!output) return false;
    output << static_cast<long>(time(nullptr)) << ' ' << ::getpid()
           << ' ' << local_offline << ' ' << bmw_offline << '\n';
    output.close();
    return !output.fail() && std::rename(temporary.c_str(), path.c_str()) == 0;
}

// helper: simple placeholder check for 1111-IDs
static bool is_placeholder_uuid(const std::string& v){
    static const std::regex all_ones("^1{8}-1{4}-1{4}-1{4}-1{12}$");
    return v.empty() || std::regex_match(v, all_ones);
}

// Rebuild after a stalled loop or token rotation; called only by the main thread.
static bool bmw_full_reconnect(){
    if (g_bmw) {
        mosquitto_loop_stop(g_bmw, true);
        mosquitto_destroy(g_bmw);
        g_bmw = nullptr;
    }
    // Old callbacks have finished; they cannot restore a stale connected state.
    g_connected = false;
    g_bmw = create_bmw_client();
    if (!g_bmw) {
        std::cerr << "[bridge] BMW client rebuild failed\n";
        return false;
    }
    int rc = mosquitto_connect_async(g_bmw, BMW_HOST.c_str(), BMW_PORT, 30);
    if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_loop_start(g_bmw);
    std::cerr << "[bridge] BMW rebuild+connect rc=" << rc
              << " (" << mosquitto_strerror(rc) << ")\n";
    if (rc != MOSQ_ERR_SUCCESS) {
        mosquitto_destroy(g_bmw);
        g_bmw = nullptr;
        return false;
    }
    return true;
}

static void check_bmw_connection(std::chrono::steady_clock::time_point now) {
    static auto last_connected = now;
    if (g_connected.load()) {
        last_connected = now;
        return;
    }
    if (g_stop || now - last_connected < std::chrono::seconds(30) ||
        time(nullptr) < g_next_connect_after.load()) return;
    // Retry even if DNS/TLS failed before CONNECT or the network loop has ended.
    last_connected = now;
    std::cerr << "[bridge] BMW MQTT disconnected for 30s; rebuilding client\n";
    bmw_full_reconnect();
}

// Debounced status publisher for MQTT_LOCAL_STATUS_TOPIC
static void publish_status(std::chrono::steady_clock::time_point status_now = std::chrono::steady_clock::now()) {
    static std::mutex status_mutex;
    std::lock_guard<std::mutex> lock(status_mutex);
    static long  disconnected_since = 0;   // 0 = not currently timing
    static bool  last_published     = false;
    static bool  initialized        = false;
    static auto last_sent = std::chrono::steady_clock::time_point{};

    std::lock_guard<std::mutex> local_lock(g_local_mutex);
    if (!g_local || !g_local_connected.load()) return;
    // Read the BMW state after acquiring both locks, rather than using a stale caller snapshot.
    const bool connected = g_connected.load();
    const bool refresh_due = status_now - last_sent >= std::chrono::seconds(30);
    if (g_status_resend.exchange(false)) {
        initialized = false;
        disconnected_since = 0;
    }

    auto do_publish = [&](bool val){
        json j;
        j["connected"] = val;
        j["timestamp"] = static_cast<long>(time(nullptr));
        std::string payload = j.dump();
        const int rc = mosquitto_publish(g_local, nullptr, MQTT_LOCAL_STATUS_TOPIC.c_str(),
                                        payload.size(), payload.data(), 1, true);
        if (rc != MOSQ_ERR_SUCCESS) {
            std::cerr << "[bridge] status publish failed: " << mosquitto_strerror(rc) << '\n';
            return;
        }
        std::cerr << "[bridge] status publish queued: " << MQTT_LOCAL_STATUS_TOPIC
                  << " connected=" << (val ? "true" : "false") << '\n';
        last_published = val;
        initialized = true;
        last_sent = status_now;
    };

    if (connected) {
        disconnected_since = 0;
        if (!initialized || last_published != true || refresh_due) {
            do_publish(true); // Report a successful connection immediately.
        }
        return;
    }

    // connected == false
    if (BMW_STATUS_STABLE_DELAY == 0) {
        if (!initialized || last_published != false || refresh_due) {
            do_publish(false); // Report a disconnect immediately.
        }
        disconnected_since = 0;
        return;
    }
    long now = time(nullptr);
    if (disconnected_since == 0) { disconnected_since = now; return; }
    if ((now - disconnected_since) >= BMW_STATUS_STABLE_DELAY && (!initialized || last_published != false || refresh_due)) {
        do_publish(false); // Report a disconnect after the debounce interval.
    }
}

// Wait for the broker to acknowledge the retained offline status before disconnecting.
static void on_local_publish(struct mosquitto*, void*, int mid) {
    std::lock_guard<std::mutex> lock(g_shutdown_mutex);
    if (g_shutdown_mid != 0 && mid == g_shutdown_mid) {
        g_shutdown_acknowledged = true;
        g_shutdown_condition.notify_all();
    }
}

static bool publish_shutdown_status() {
    std::lock_guard<std::mutex> local_lock(g_local_mutex);
    if (!g_local || !g_local_connected.load()) return false;
    json status = {{"connected", false}, {"timestamp", static_cast<long>(time(nullptr))}};
    const std::string payload = status.dump();
    // Hold the mutex while setting the message ID to avoid a fast callback race.
    std::unique_lock<std::mutex> lock(g_shutdown_mutex);
    g_shutdown_mid = 0;
    g_shutdown_acknowledged = false;
    const int rc = mosquitto_publish(g_local, &g_shutdown_mid, MQTT_LOCAL_STATUS_TOPIC.c_str(),
                                    payload.size(), payload.data(), 1, true);
    if (rc != MOSQ_ERR_SUCCESS) {
        std::cerr << "[bridge] shutdown status publish failed: " << mosquitto_strerror(rc) << '\n';
        return false;
    }
    const bool acknowledged = g_shutdown_condition.wait_for(lock, std::chrono::seconds(2), [] {
        return g_shutdown_acknowledged;
    });
    std::cerr << "[bridge] shutdown status " << (acknowledged ? "acknowledged" : "timed out") << '\n';
    return acknowledged;
}

static std::string read_file(const std::string& path) {
    std::ifstream f(path);
    if (!f) return {};
    std::ostringstream ss; ss << f.rdbuf();
    return ss.str();
}

static std::string trim(std::string s) {
    auto isws = [](unsigned char c){ return c=='\n'||c=='\r'||c=='\t'||c==' '; };
    while (!s.empty() && isws(s.back())) s.pop_back();
    size_t i=0; while (i<s.size() && isws(s[i])) ++i;
    return s.substr(i);
}

// Replace characters that would change the topic hierarchy.
static std::string sanitize_key(std::string s){
    for (auto& c : s){
        if (c=='/' || c==' ' || c=='\t' || c=='\r' || c=='\n') c = '_';
    }
    return s;
}

// ---- Base64url decode (no OpenSSL; safe handling of '=' padding) ----
static inline uint8_t b64tbl(char c){
    if(c>='A'&&c<='Z') return c-'A';
    if(c>='a'&&c<='z') return c-'a'+26;
    if(c>='0'&&c<='9') return c-'0'+52;
    if(c=='+') return 62;
    if(c=='/') return 63;
    return 0xFF; // INVALID (do not map '=' here!)
}
static std::string b64url_to_b64(std::string s){
    std::replace(s.begin(), s.end(), '-', '+');
    std::replace(s.begin(), s.end(), '_', '/');
    while (s.size() % 4) s.push_back('=');
    return s;
}
static std::string base64url_decode(std::string s){
    s = b64url_to_b64(std::move(s));
    std::string out; out.reserve((s.size()*3)/4);
    for (size_t i=0; i<s.size(); i+=4){
        uint8_t a=b64tbl(s[i]), b=b64tbl(s[i+1]);
        uint8_t c=(s[i+2]=='=')?0xFF:b64tbl(s[i+2]);
        uint8_t d = (s[i+3] == '=') ? 0xFF : b64tbl(s[i+3]);
        if(a==0xFF||b==0xFF) break;
        out.push_back(char((a<<2)|(b>>4)));
        if(c!=0xFF){
            out.push_back(char(((b&0x0F)<<4)|(c>>2)));
            if(d!=0xFF)
                out.push_back(char(((c&0x03)<<6)|d));
        }
    }
    return out;
}

static long jwt_exp_unix(const std::string& jwt){
    // JWT: header.payload.sig  → we want the payload part
    auto p1 = jwt.find('.'); if(p1==std::string::npos) return 0;
    auto p2 = jwt.find('.', p1+1); if(p2==std::string::npos) return 0;
    auto payload = base64url_decode(jwt.substr(p1+1, p2-(p1+1)));
    json j = json::parse(payload, nullptr, false);
    if(j.is_discarded()) return 0;
    return j.value("exp", 0L);
}

static size_t curl_write_cb(void* ptr, size_t size, size_t nmemb, void* userdata){
    auto* s = static_cast<std::string*>(userdata);
    s->append(static_cast<const char*>(ptr), size*nmemb);
    return size*nmemb;
}

// ===================== MQTT Callbacks =====================
static void on_local_connect(struct mosquitto* client, void*, int rc) {
    {
        std::lock_guard<std::mutex> lock(g_local_mutex);
        if (client != g_local) return;
        g_local_connected = (rc == 0);
        if (rc == 0) g_status_resend = true;
    }
    std::cerr << "[bridge] local MQTT CONNACK rc=" << rc
              << " (" << mosquitto_connack_string(rc) << ")\n";
    if (rc == 0) publish_status();
}

static void on_local_disconnect(struct mosquitto* client, void*, int rc) {
    {
        std::lock_guard<std::mutex> lock(g_local_mutex);
        if (client != g_local) return;
        g_local_connected = false;
    }
    std::cerr << "[bridge] local MQTT disconnected rc=" << rc
              << " (" << mosquitto_strerror(rc) << ")\n";
}

static void on_local_log(struct mosquitto*, void*, int level, const char* message) {
    if (!message) return;
    if ((level & (MOSQ_LOG_ERR | MOSQ_LOG_WARNING)) || std::strstr(message, "sending CONNECT")) {
        std::cerr << "[local/log] level=" << level << " " << message << '\n';
    }
}

// Shared by raw telemetry, split fields and status publishers during client replacement.
static int publish_local(const std::string& topic, const std::string& payload, bool retain) {
    std::lock_guard<std::mutex> lock(g_local_mutex);
    if (!g_local || !g_local_connected.load()) return MOSQ_ERR_NO_CONN;
    return mosquitto_publish(g_local, nullptr, topic.c_str(), payload.size(), payload.data(), 0, retain);
}

// Local MQTT lifecycle and watchdog; only the main thread creates or retires clients.
static mosquitto* create_local_client() {
    mosquitto* client = mosquitto_new(MQTT_LOCAL_CLIENT_ID.empty() ? nullptr : MQTT_LOCAL_CLIENT_ID.c_str(),
                                     true, nullptr);
    if (!client) return nullptr;
    mosquitto_connect_callback_set(client, on_local_connect);
    mosquitto_disconnect_callback_set(client, on_local_disconnect);
    mosquitto_publish_callback_set(client, on_local_publish);
    mosquitto_log_callback_set(client, on_local_log);
    const char* lwt = "{\"connected\":false}";
    int rc = mosquitto_reconnect_delay_set(client, 1, 10, true);
    if (rc == MOSQ_ERR_SUCCESS) {
        rc = mosquitto_will_set(client, MQTT_LOCAL_STATUS_TOPIC.c_str(), strlen(lwt), lwt, 0, true);
    }
    if (rc == MOSQ_ERR_SUCCESS && !MQTT_LOCAL_USER.empty()) {
        rc = mosquitto_username_pw_set(client, MQTT_LOCAL_USER.c_str(),
                                       MQTT_LOCAL_PASSWORD.empty() ? nullptr : MQTT_LOCAL_PASSWORD.c_str());
    }
    if (rc != MOSQ_ERR_SUCCESS) {
        std::cerr << "[bridge] local client configuration failed: " << mosquitto_strerror(rc) << '\n';
        mosquitto_destroy(client);
        return nullptr;
    }
    if (!configure_local_tls(client)) {
        mosquitto_destroy(client);
        return nullptr;
    }
    return client;
}

static void stop_local_client(bool clean_disconnect) {
    mosquitto* old_client;
    {
        std::lock_guard<std::mutex> lock(g_local_mutex);
        old_client = g_local;
        g_local = nullptr;
        g_local_connected = false;
    }
    // Callbacks can finish and concurrent BMW messages see a null client safely.
    if (old_client) {
        if (clean_disconnect) mosquitto_disconnect(old_client);
        mosquitto_loop_stop(old_client, true);
        mosquitto_destroy(old_client);
    }
    g_local_connected = false;
}

static bool restart_local_client() {
    stop_local_client(false);
    mosquitto* client = create_local_client();
    if (!client) {
        std::cerr << "[bridge] local MQTT client rebuild failed\n";
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(g_local_mutex);
        g_local = client;
    }
    int rc = mosquitto_connect_async(client, MQTT_LOCAL_HOST.c_str(), MQTT_LOCAL_PORT, 30);
    if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_loop_start(client);
    std::cerr << "[bridge] local MQTT rebuild+connect rc=" << rc
              << " (" << mosquitto_strerror(rc) << ")\n";
    if (rc != MOSQ_ERR_SUCCESS) {
        stop_local_client(false);
        return false;
    }
    return true;
}

static void check_local_connection(std::chrono::steady_clock::time_point now) {
    static auto last_connected = now;
    if (g_local_connected.load()) {
        last_connected = now;
        return;
    }
    if (g_stop || now - last_connected < std::chrono::seconds(30)) return;
    // Rate-limit failed rebuilds independently of BMW's token/connect backoff.
    last_connected = now;
    std::cerr << "[bridge] local MQTT disconnected for 30s; rebuilding client\n";
    restart_local_client();
}

// v5 connect callback (no property iteration, Debian header only forward-declares properties)
static void on_bmw_connect_v5(struct mosquitto* client, void*, int rc, int flags, const mosquitto_property* /*props*/){
    const char* reason = mosquitto_reason_string(rc);
    std::cout << "[bridge] BMW on_connect_v5 rc=" << rc
              << " (" << (reason ? reason : "unknown") << ")"
              << " sp=" << ((flags & 0x01) ? 1 : 0)
              << "\n";

    if(rc == 0){
        g_connected = true;
        std::string sub = BMW_GCID + std::string("/+");
        int mid = 0;
        int s_rc = mosquitto_subscribe(client, &mid, sub.c_str(), 1);
        std::cerr << "[bridge] subscribe '" << sub << "' rc=" << s_rc << " mid=" << mid << "\n";
        publish_status();
        g_next_connect_after = 0;
        return;
    }

    // failed → set backoff
    long now = time(nullptr);
    long delay = 5; // default
    if (rc == 151) delay = 60;              // Quota exceeded
    if (rc == 128 || rc == 136 || rc == 137) delay = 20; // Unspecified / Server unavailable / Server busy
    if (rc == 135) delay = 30;              // Not authorized

    g_next_connect_after = now + delay;
    g_connected = false;
    publish_status();
}

static void on_bmw_disconnect(struct mosquitto*, void*, int rc){
    std::cout << "[bridge] BMW disconnect rc=" << rc << "\n";
    g_connected = false;
    publish_status();
}

static void on_bmw_disconnect_v5(struct mosquitto*, void*, int rc,
                                 const mosquitto_property* /*props*/){
    const char* reason = mosquitto_reason_string(rc);
    std::cerr << "[bridge] BMW disconnect_v5 rc=" << rc
              << " (" << (reason ? reason : "unknown") << ")\n";
    g_connected = false;
    publish_status();
}

static void on_bmw_message(struct mosquitto*, void*, const struct mosquitto_message* m){
    if (!m || !m->topic) return;
    std::string in_topic = m->topic ? m->topic : "";

    // Republish both raw and legacy topics.
    auto pos = in_topic.find('/');
    std::string raw_topic    = MQTT_LOCAL_PREFIX + "raw" + (pos!=std::string::npos ? in_topic.substr(pos)   : "");
    std::string legacy_topic = MQTT_LOCAL_PREFIX          + (pos!=std::string::npos ? in_topic.substr(pos+1) : in_topic);

    bool retain_flag = (MQTT_RETAIN != 0);
    const std::string raw_payload(m->payload ? static_cast<const char*>(m->payload) : "",
                                  m->payload ? static_cast<size_t>(m->payloadlen) : 0);
    int rc1 = publish_local(raw_topic, raw_payload, retain_flag);
    int rc2 = publish_local(legacy_topic, raw_payload, retain_flag);

    std::cerr << "[bridge] fwd rc1=" << rc1
              << " rc2=" << rc2
              << " retain=" << (retain_flag ? 1 : 0)
              << " in='"  << in_topic
              << "' raw='"<< raw_topic
              << "' legacy='"<< legacy_topic
              << "' bytes="<< m->payloadlen << "\n";

    // Optionally publish individual data fields.
    if (!MQTT_SPLIT_TOPICS || !m->payload || m->payloadlen <= 0)
        return;

    try {
        std::string payload_str(static_cast<const char*>(m->payload), static_cast<size_t>(m->payloadlen));
        auto j = json::parse(payload_str, nullptr, true);

        std::string vin;
        if (j.contains("vin") && j["vin"].is_string()) {
            vin = j["vin"].get<std::string>();
        }
        if (vin.empty()) {
            auto pos = in_topic.find('/');
            if (pos != std::string::npos) {
                auto next = in_topic.find('/', pos + 1);
                if (next != std::string::npos)
                    vin = in_topic.substr(pos + 1, next - (pos + 1));
            }
        }
        if (vin.empty() || vin.size() != 17)
            throw std::runtime_error("invalid or missing VIN");

        if (j.contains("data") && j["data"].is_object()) {
            for (auto& [propName, propObj] : j["data"].items()) {
                if (propObj.contains("value")) {
                    std::string topic = MQTT_LOCAL_PREFIX + "vehicles/" + vin + "/" + sanitize_key(propName);
                    std::string val = propObj.dump();
                    int rc = publish_local(topic, val, retain_flag);
                    std::cerr << "[bridge] split '" << topic << "' val=" << val << " rc=" << rc << "\n";
                }
            }
        } else {
            throw std::runtime_error("No valid data in payload");
        }
    } catch (const std::exception& e) {
        std::cerr << "[bridge] JSON parse error: " << e.what() << "\n";
    }
}

// Log MQTT failures and suppress ping noise.
static void on_bmw_log(struct mosquitto* /*mosq*/, void* /*userdata*/,
                       int level, const char* str)
{
    if(!str) return;
    if (std::strstr(str, "PINGREQ") || std::strstr(str, "PINGRESP")) return;

    // Match explicit error messages instead of every occurrence of SSL.
    bool is_err_level =
        (level == MOSQ_LOG_ERR) ||
        (level == MOSQ_LOG_WARNING);

    if (is_err_level &&
    (std::strstr(str, "OpenSSL Error") ||
        std::strstr(str, "SSL error") ||               // Match SSL errors only.
        std::strstr(str, "Connection reset by peer") ||
        std::strstr(str, "unexpected eof") ||
        std::strstr(str, "protocol error")))
    {
        g_connected = false;
        publish_status();
        long now = time(nullptr);
        g_next_connect_after = now + 5;
    }

    std::cerr << "[bmw/log] level=" << level << " " << str << "\n";
}

static void on_bmw_suback(struct mosquitto* /*mosq*/, void* /*userdata*/,
                          int mid, int qos_count, const int* granted_qos)
{
    std::cerr << "[bmw] SUBACK mid=" << mid
              << " qos_count=" << qos_count;
    if (qos_count > 0 && granted_qos) std::cerr << " granted0=" << granted_qos[0];
    std::cerr << "\n";
}

// ===================== BMW client factory =====================

static mosquitto* create_bmw_client() {
    mosquitto* m = mosquitto_new(BMW_CLIENT_ID.c_str(), true, nullptr);
    if(!m) return nullptr;

    int rc = mosquitto_int_option(m, MOSQ_OPT_PROTOCOL_VERSION, MQTT_PROTOCOL_V5);
    if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_reconnect_delay_set(m, 1, 10, true);

    mosquitto_connect_v5_callback_set(m, on_bmw_connect_v5);
    mosquitto_disconnect_callback_set(m, on_bmw_disconnect);
    mosquitto_disconnect_v5_callback_set(m, on_bmw_disconnect_v5);
    mosquitto_message_callback_set(m, on_bmw_message);
    mosquitto_log_callback_set(m, on_bmw_log);
    mosquitto_subscribe_callback_set(m, on_bmw_suback);

    if (rc == MOSQ_ERR_SUCCESS) {
        rc = mosquitto_tls_set(m, "/etc/ssl/certs/ca-certificates.crt",
                              nullptr, nullptr, nullptr, nullptr);
    }
    if (rc == MOSQ_ERR_SUCCESS) rc = mosquitto_username_pw_set(m, BMW_GCID.c_str(), g_id_token.c_str());
    if (rc != MOSQ_ERR_SUCCESS) {
        std::cerr << "[bridge] BMW client configuration failed: " << mosquitto_strerror(rc) << '\n';
        mosquitto_destroy(m);
        return nullptr;
    }

    return m;
}

// ===================== Main =====================

static void sigint_handler(int){ g_stop = true; }

int main() try {
    std::signal(SIGINT,  sigint_handler);
    std::signal(SIGTERM, sigint_handler);

    // Configuration comes exclusively from the container process environment.
    const std::string TDIR = token_dir();
    const std::string heartbeat_path = env_str("BMW_HEARTBEAT_FILE", "/tmp/bmw-mqtt-bridge-heartbeat");
    // Do not reuse a heartbeat from a previous run of this container.
    if (!heartbeat_path.empty()) std::remove(heartbeat_path.c_str());

    // initialize
    BMW_CLIENT_ID         = env_str("BMW_CLIENT_ID",        "");
    BMW_GCID              = env_str("BMW_GCID",             "");
    BMW_HOST              = env_str("BMW_HOST",          "customer.streaming-cardata.bmwgroup.com");
    BMW_PORT              = env_int("BMW_PORT",            9000);
    MQTT_LOCAL_HOST       = env_str("MQTT_LOCAL_HOST",   "host.docker.internal");
    MQTT_LOCAL_PORT       = env_int("MQTT_LOCAL_PORT",     1883);
    MQTT_LOCAL_PREFIX     = env_str("MQTT_LOCAL_PREFIX", "bmw/");
    MQTT_LOCAL_CLIENT_ID  = env_str("MQTT_LOCAL_CLIENT_ID",  "");
    MQTT_LOCAL_USER       = env_str("MQTT_LOCAL_USER",       "");
    MQTT_LOCAL_PASSWORD   = env_str("MQTT_LOCAL_PASSWORD",   "");
    MQTT_SPLIT_TOPICS     = env_int("MQTT_SPLIT_TOPICS",      0);
    MQTT_RETAIN           = env_int("MQTT_RETAIN",            0);
    if (BMW_PORT < 1 || BMW_PORT > 65535 || MQTT_LOCAL_PORT < 1 || MQTT_LOCAL_PORT > 65535) {
        throw std::invalid_argument("BMW_PORT and MQTT_LOCAL_PORT must be between 1 and 65535");
    }
    if ((MQTT_SPLIT_TOPICS != 0 && MQTT_SPLIT_TOPICS != 1) || (MQTT_RETAIN != 0 && MQTT_RETAIN != 1)) {
        throw std::invalid_argument("MQTT_SPLIT_TOPICS and MQTT_RETAIN must be 0 or 1");
    }
    if (MQTT_LOCAL_PREFIX.find_first_of("+#") != std::string::npos) {
        throw std::invalid_argument("MQTT_LOCAL_PREFIX must not contain MQTT wildcards (+ or #)");
    }

    // fixed token files (no env overrides)
    ID_TOKEN_FILE       = (std::filesystem::path(TDIR) / "id_token.txt").string();
    REFRESH_TOKEN_FILE  = (std::filesystem::path(TDIR) / "refresh_token.txt").string();
    // Normalize the topic prefix and apply its default.
    if (MQTT_LOCAL_PREFIX.empty()) {
        MQTT_LOCAL_PREFIX = "bmw/";             // Fallback: keeps bmw/status as default
    }
    if (MQTT_LOCAL_PREFIX.back() != '/') {
        MQTT_LOCAL_PREFIX.push_back('/');       // just for protection
    }
    MQTT_LOCAL_STATUS_TOPIC = MQTT_LOCAL_PREFIX + "status";
    std::cerr << "[bridge] using status topic: " << MQTT_LOCAL_STATUS_TOPIC << "\n";

    BMW_STATUS_STABLE_DELAY = env_int("BMW_STATUS_STABLE_DELAY", 5);
    if (BMW_STATUS_STABLE_DELAY < 0) BMW_STATUS_STABLE_DELAY = 0;
    if (BMW_STATUS_STABLE_DELAY > 3600) BMW_STATUS_STABLE_DELAY = 3600;
    std::cerr << "[bridge] status delay: " << BMW_STATUS_STABLE_DELAY << "s\n";


    // ensure token directory exists
    if (!std::filesystem::exists(TDIR)) {
        std::cerr << "✖ Token directory missing: " << TDIR << "\n"
                  << "   Run docker compose run --rm -it bmw-mqtt-bridge ./bmw_flow.sh first.\n";
        return 1;
    }

    // validate required IDs (no defaults; reject placeholders)
    if (is_placeholder_uuid(BMW_CLIENT_ID)) {
        std::cerr << "✖ BMW_CLIENT_ID missing or placeholder in container environment\n";
        return 1;
    }
    if (is_placeholder_uuid(BMW_GCID)) {
        std::cerr << "✖ BMW_GCID missing or placeholder in container environment\n";
        return 1;
    }

    // refresh logic constants
    constexpr long CLOCK_SKEW_SECS   = 60;    // 1 min safety for clock drift

    std::ios::sync_with_stdio(false);
    std::cout.setf(std::ios::unitbuf); // auto-flush stdout

    // Initialize libcurl before an initial refresh can make an HTTPS request.
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) return 2;

    // initial tokens
    g_id_token = trim(read_file(ID_TOKEN_FILE));
    const std::string refresh_token = trim(read_file(REFRESH_TOKEN_FILE));
    if(g_id_token.empty() || refresh_token.empty()){
        std::cerr << "✖ id_token.txt or refresh_token.txt missing/empty in " << TDIR << "\n";
        curl_global_cleanup();
        return 1;
    }
    g_id_token_exp = jwt_exp_unix(g_id_token);
    if (g_id_token_exp.load() == 0) {
        std::cerr << "✖ invalid id_token (no exp) → trying refresh\n";
        if (!refresh_tokens()) {
            std::cerr << "✖ cannot obtain valid token, exiting\n";
            curl_global_cleanup();
            return 1;
        }
    }

    // MQTT library initialization
    mosquitto_lib_init();

    // Start the local MQTT client asynchronously so the watchdog can supervise reconnects.
    if (!restart_local_client()) {
        mosquitto_lib_cleanup();
        curl_global_cleanup();
        return 3;
    }
    publish_status();

    // Initial failures remain supervised by the same BMW downtime watchdog.
    bmw_full_reconnect();
    std::cout << "[bridge] running… (Ctrl+C / SIGTERM to stop)\n";

    // Token refresh and MQTT downtime watchdogs.
    long last_refresh_attempt = 0;
    long last_successful_refresh = time(nullptr);
    constexpr long SOFT_MARGIN_SECS = 10*60;   // refresh 10 min before exp
    constexpr long HARD_REFRESH_SECS = 45*60;  // refresh at least every 45 min

    auto needs_soft_refresh = [&](long now){
        return (g_id_token_exp.load() - now) <= (SOFT_MARGIN_SECS + CLOCK_SKEW_SECS);
    };

    auto needs_hard_refresh = [&](long now){
            return (now - last_successful_refresh) >= HARD_REFRESH_SECS;
    };

    auto last_heartbeat = std::chrono::steady_clock::time_point{};
    auto local_last_online = std::chrono::steady_clock::now();
    auto bmw_last_online = local_last_online;
    bool previous_local_connected = false;
    bool previous_bmw_connected = false;
    while(!g_stop){
        std::this_thread::sleep_for(std::chrono::seconds(1));
        if (g_stop) break;
        long now = time(nullptr);

        const auto heartbeat_now = std::chrono::steady_clock::now();
        const bool local_connected = g_local_connected.load();
        const bool bmw_connected = g_connected.load();
        const long local_offline = mqtt_offline_seconds(local_connected, heartbeat_now, local_last_online);
        const long bmw_offline = mqtt_offline_seconds(bmw_connected, heartbeat_now, bmw_last_online);
        const bool connection_changed = local_connected != previous_local_connected ||
                                         bmw_connected != previous_bmw_connected;
        if (connection_changed || heartbeat_now - last_heartbeat >= std::chrono::seconds(10)) {
            if (!write_heartbeat(heartbeat_path, local_offline, bmw_offline)) {
                std::cerr << "[bridge] could not write liveness heartbeat\n";
            }
            last_heartbeat = heartbeat_now;
        }
        previous_local_connected = local_connected;
        previous_bmw_connected = bmw_connected;

        check_local_connection(heartbeat_now);
        check_bmw_connection(heartbeat_now);
        publish_status();

        // 0) Backoff window active? → do not trigger new actions
        if (now < g_next_connect_after.load()) continue;

        bool due_soft = needs_soft_refresh(now);
        bool due_hard = needs_hard_refresh(now);
        bool should_try = (due_soft || due_hard) && (now - last_refresh_attempt > 10);

        if (should_try){
            // small jitter to avoid sync with other processes
            std::this_thread::sleep_for(std::chrono::milliseconds(100 + (rng() % 200)));

            std::cout << "[bridge] token refresh (" << (due_soft ? "soft" : "hard") << ")\n";

            if (refresh_tokens()){
                last_refresh_attempt    = now;
                last_successful_refresh = now;

                g_connected = false;
                publish_status();

                // Pause briefly before reconnecting with the new token.
                long delay_ms = 1500 + (rng()%500);
                g_next_connect_after = time(nullptr) + 1; // Brief reconnect backoff.
                std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));

                // Rebuild after joining the old thread to replace credentials safely.
                bmw_full_reconnect();
            } else {
                last_refresh_attempt = now;
                g_next_connect_after = now + 15;
                std::cerr << "[bridge] refresh failed, retry soon\n";
            }
        }
    }

    // Cleanup
    if (!heartbeat_path.empty()) std::remove(heartbeat_path.c_str());
    if (g_bmw) {
        mosquitto_loop_stop(g_bmw, true);
        mosquitto_disconnect(g_bmw);
        mosquitto_destroy(g_bmw);
    }
    // A clean disconnect suppresses the Last Will; send offline explicitly first.
    // If delivery fails, close without DISCONNECT so the broker can publish the Will.
    const bool offline_acknowledged = publish_shutdown_status();
    stop_local_client(offline_acknowledged);
    mosquitto_lib_cleanup();
    curl_global_cleanup();
    std::cout << "[bridge] bye\n";
    return 0;
} catch (const std::invalid_argument& error) {
    std::cerr << "[bridge] invalid configuration: " << error.what() << '\n';
    return 1;
}


// ============= refresh tokens =============

// small utility: safely writes a file (0644 default)
static bool write_file_mode(const std::string& path, const std::string& data, mode_t mode=0644){
    int fd = ::open(path.c_str(), O_CREAT|O_TRUNC|O_WRONLY, mode);
    if (fd < 0) return false;
    if (::fchmod(fd, mode) != 0) { ::close(fd); return false; }
    ssize_t want = (ssize_t)data.size();
    const char* p = data.data();
    while (want > 0){
        ssize_t n = ::write(fd, p, want);
        if (n <= 0) { ::close(fd); return false; }
        want -= n; p += n;
    }
    ::fsync(fd);
    ::close(fd);
    return true;
}

// form-urlencode for a key/value with libcurl (uses its own CURL easy handle)
static std::string urlencode_component(const std::string& s){
    CURL* h = curl_easy_init();
    if(!h) return s; // worst case: unencoded
    char* esc = curl_easy_escape(h, s.c_str(), (int)s.size());
    std::string out = esc ? esc : "";
    if (esc) curl_free(esc);
    curl_easy_cleanup(h);
    return out;
}

static std::string build_form_body(const std::vector<std::pair<std::string,std::string>>& kv){
    std::ostringstream oss;
    bool first = true;
    for (auto& [k,v] : kv){
        if(!first) oss << "&";
        first = false;
        oss << urlencode_component(k) << "=" << urlencode_component(v);
    }
    return oss.str();
}

static bool write_file_atomic(const std::string& final_path,
                              const std::string& data,
                              mode_t mode = 0644)
{
    namespace fs = std::filesystem;

    fs::path fpath(final_path);
    fs::path dir = fpath.parent_path();
    std::error_code ec;
    fs::create_directories(dir, ec); // File creation below reports failures.

    // Create the temporary file in the target directory.
    std::string tmpl = (dir / (fpath.filename().string() + ".tmp.XXXXXX")).string();
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');

    int tfd = ::mkstemp(buf.data()); // Creates a unique temporary file.
    if (tfd < 0) {
        std::cerr << "[bridge] mkstemp failed: " << std::strerror(errno) << "\n";
        return false;
    }

    // Set permissions independently of umask.
    if (::fchmod(tfd, mode) != 0) {
        std::cerr << "[bridge] fchmod failed: " << std::strerror(errno) << "\n";
        ::close(tfd);
        ::unlink(buf.data());
        return false;
    }

    // Write the complete payload.
    const char* p = data.data();
    ssize_t left = (ssize_t)data.size();
    while (left > 0) {
        ssize_t n = ::write(tfd, p, left);
        if (n < 0) {
            if (errno == EINTR) continue;
            std::cerr << "[bridge] write failed: " << std::strerror(errno) << "\n";
            ::close(tfd);
            ::unlink(buf.data());
            return false;
        }
        left -= n; p += n;
    }

    // Flush file contents.
    if (::fsync(tfd) != 0) {
        std::cerr << "[bridge] fsync(tmp) failed: " << std::strerror(errno) << "\n";
        ::close(tfd);
        ::unlink(buf.data());
        return false;
    }
    ::close(tfd);

    // Replace atomically within the same filesystem.
    if (::rename(buf.data(), final_path.c_str()) != 0) {
        std::cerr << "[bridge] rename failed: " << std::strerror(errno)
                  << " (errno=" << errno << ")\n";
        ::unlink(buf.data());
        return false;
    }

    // Flush the directory to persist the rename.
    int dfd = ::open(dir.c_str(), O_RDONLY | O_DIRECTORY);
    if (dfd >= 0) {
        (void)::fsync(dfd);
        ::close(dfd);
    }

    return true;
}

static bool refresh_tokens() {

    std::cout << "[bridge] refresh started\n";

    // load current refresh token (as in the script)
    std::string cur_refresh = trim(read_file(REFRESH_TOKEN_FILE));
    if (cur_refresh.empty()) {
        std::cerr << "[bridge] refresh: refresh_token.txt missing/empty\n";
        return false;
    }

    // form body
    const std::string url = "https://customer.bmwgroup.com/gcdm/oauth/token";
    const std::string body = build_form_body({
        {"grant_type",   "refresh_token"},
        {"refresh_token",cur_refresh},
        {"client_id",    BMW_CLIENT_ID}
    });

    // HTTP Request via libcurl
    std::string resp;
    long http_code = 0;

    CURL* c = curl_easy_init();
    if(!c){
        std::cerr << "[bridge] curl_easy_init failed\n";
        return false;
    }

    struct curl_slist* hdrs = nullptr;
    hdrs = curl_slist_append(hdrs, "Content-Type: application/x-www-form-urlencoded");

    curl_easy_setopt(c, CURLOPT_URL, url.c_str());
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, hdrs);
    curl_easy_setopt(c, CURLOPT_POST, 1L);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(c, CURLOPT_POSTFIELDSIZE, (long)body.size());

    // Timeout/SSL
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 20L);
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 10L);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L); // threadsafe timeouts
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, curl_write_cb);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "bmw-mqtt-bridge/1.0");

    CURLcode rc = curl_easy_perform(c);
    if (rc != CURLE_OK) {
        std::cerr << "[bridge] curl perform failed: " << curl_easy_strerror(rc) << "\n";
        curl_slist_free_all(hdrs);
        curl_easy_cleanup(c);
        return false;
    }
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &http_code);

    curl_slist_free_all(hdrs);
    curl_easy_cleanup(c);

    // determine target paths based on configured files
    std::string id_path  = ID_TOKEN_FILE;
    std::string rt_path  = REFRESH_TOKEN_FILE;
    std::string dir      = dirname_of(id_path);
    std::string at_path  = (std::filesystem::path(dir) / "access_token.txt").string();

    // save entire response (debug) in same directory as token files
    try {
        json dbg = json::parse(resp);
        write_file_mode((std::filesystem::path(dir) / "token_refresh_response.json").string(),
                        dbg.dump(2) + "\n", 0644);
    } catch (...) {
        write_file_mode((std::filesystem::path(dir) / "token_refresh_response.json").string(),
                        resp, 0644);
    }

    if (http_code != 200) {
        std::cerr << "✖ Refresh HTTP " << http_code << ":\n" << resp << "\n";
        return false;
    }

    // parse JSON & check error
    json j = json::parse(resp, nullptr, false);
    if (j.is_discarded()) {
        std::cerr << "✖ Refresh: invalid JSON\n";
        return false;
    }
    if (j.contains("error") && !j["error"].is_null()) {
        std::cerr << "✖ Refresh failed:\n" << j.dump(2) << "\n";
        return false;
    }

    // extract tokens
    std::string new_id  = j.value("id_token",      "");
    std::string new_rt  = j.value("refresh_token", "");
    std::string new_acc = j.value("access_token",  "");

    // remove \r\n / trim
    new_id  = trim(new_id);
    new_rt  = trim(new_rt);
    new_acc = trim(new_acc);

    if (new_id.empty() || new_rt.empty() || new_acc.empty()) {
        std::cerr << "✖ Refresh: missing data in response\n";
        return false;
    }

    // Write tokens atomically in their target directory.
    bool ok = true;
    ok &= write_file_atomic(id_path, new_id, 0644);
    ok &= write_file_atomic(rt_path, new_rt, 0644);
    ok &= write_file_atomic(at_path, new_acc, 0644);

    if (!ok) {
        std::cerr << "[bridge] writing tokens atomically failed\n";
        return false;
    }

    // update in-memory
    g_id_token      = new_id;
    g_id_token_exp  = jwt_exp_unix(g_id_token);

    std::cout << "✔ New Tokens saved:\n"
              << "   id_token.txt, refresh_token.txt, access_token.txt\n";
    std::cout << "[bridge] token refreshed via HTTP, exp=" << g_id_token_exp
              << " (in " << (g_id_token_exp.load() - time(nullptr)) << "s)\n";

    return true;
}
