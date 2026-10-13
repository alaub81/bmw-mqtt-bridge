#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>

namespace bridge_log {

enum class Level { debug, info, warning, error };
inline std::atomic<Level> minimum{Level::info};

inline void configure(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (value == "debug") minimum = Level::debug;
    else if (value == "info") minimum = Level::info;
    else if (value == "warning") minimum = Level::warning;
    else if (value == "error") minimum = Level::error;
    else throw std::invalid_argument("BMB_LOG_LEVEL must be debug, info, warning or error");
}

inline bool enabled(Level level) { return level >= minimum.load(); }

// Assemble a complete line before taking the output lock: one stream write,
// without interleaved fragments from MQTT callbacks and the main thread.
class Line {
    std::ostringstream output;
public:
    explicit Line(size_t suppressed = 0) {
        if (suppressed) output << "[log] suppressed " << suppressed << " similar messages; ";
    }
    ~Line() {
        static std::mutex mutex;
        const auto text = output.str();
        std::lock_guard<std::mutex> lock(mutex);
        std::cerr << text;
    }
    std::ostream& stream() { return output; }
};

struct Event { bool emit; size_t suppressed; };
class Limiter {
    std::mutex mutex;
    std::chrono::steady_clock::time_point next{};
    size_t suppressed = 0;
public:
    Event check(Level level, std::chrono::steady_clock::time_point now = std::chrono::steady_clock::now()) {
        if (!enabled(level)) return {false, 0};
        std::lock_guard<std::mutex> lock(mutex);
        if (minimum == Level::debug || now >= next) {
            const auto count = suppressed;
            suppressed = 0;
            next = now + std::chrono::seconds(30);
            return {true, count};
        }
        ++suppressed;
        return {false, 0};
    }
};

constexpr std::uint64_t source_id(const char* filename) {
    std::uint64_t hash = 14695981039346656037ULL;
    while (*filename) {
        hash ^= static_cast<unsigned char>(*filename++);
        hash *= 1099511628211ULL;
    }
    return hash;
}

template<std::uint64_t Source, int SourceLine>
inline Event limited(Level level) {
    // A warning burst must not hide the first subsequent error at this site.
    static std::array<Limiter, 4> limiters;
    return limiters[static_cast<size_t>(level)].check(level);
}

} // namespace bridge_log

// A for statement keeps these stream macros safe in unbraced if/else branches,
// and avoids evaluating payload formatting when the log level is disabled.
#define BMB_LOG_AT(severity) \
    for (bool bmb_log_once = bridge_log::enabled(severity); bmb_log_once; bmb_log_once = false) \
        bridge_log::Line().stream()
#define BMB_LOG(level) BMB_LOG_AT(bridge_log::Level::level)
#define BMB_LOG_THROTTLED_AT(severity) \
    for (auto bmb_log_event = bridge_log::limited<bridge_log::source_id(__FILE__), __LINE__>(severity); \
         bmb_log_event.emit; bmb_log_event.emit = false) \
        bridge_log::Line(bmb_log_event.suppressed).stream()
#define BMB_LOG_THROTTLED(level) BMB_LOG_THROTTLED_AT(bridge_log::Level::level)
