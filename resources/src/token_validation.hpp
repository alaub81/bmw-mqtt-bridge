#pragma once

#include <cstdint>
#include <ctime>
#include <limits>
#include <string>
#include <utility>
#include "json.hpp"

namespace token_validation {

inline bool valid_token(const std::string& token) {
    if (token.empty()) return false;
    for (unsigned char c : token) if (c < 33 || c > 126) return false;
    return true;
}

// Only inspect expiry; JWT signature verification is performed by BMW.
// Reject invalid types/encoding instead of allowing JSON conversions to throw.
inline long expiration(const std::string& jwt) {
    if (!valid_token(jwt)) return 0;
    const auto first = jwt.find('.');
    if (first == std::string::npos || first == 0) return 0;
    const auto second = jwt.find('.', first + 1);
    if (second == std::string::npos || second + 1 == jwt.size() || jwt.find('.', second + 1) != std::string::npos) return 0;
    const auto encoded = jwt.substr(first + 1, second - first - 1);
    if (encoded.empty() || encoded.size() % 4 == 1) return 0;
    std::string decoded;
    decoded.reserve(encoded.size() * 3 / 4);
    std::uint32_t buffer = 0;
    unsigned bits = 0;
    for (unsigned char c : encoded) {
        unsigned value;
        if (c >= 'A' && c <= 'Z') value = c - 'A';
        else if (c >= 'a' && c <= 'z') value = c - 'a' + 26;
        else if (c >= '0' && c <= '9') value = c - '0' + 52;
        else if (c == '-') value = 62;
        else if (c == '_') value = 63;
        else return 0;
        buffer = (buffer << 6) | value;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            decoded += static_cast<char>((buffer >> bits) & 255);
        }
    }
    if (bits && (buffer & ((1U << bits) - 1)) != 0) return 0;
    try {
        const auto claims = nlohmann::json::parse(decoded, nullptr, false);
        if (!claims.is_object() || !claims.contains("exp")) return 0;
        const auto& exp = claims.at("exp");
        if (!exp.is_number_integer()) return 0;
        if (exp.is_number_unsigned()) {
            const auto value = exp.get<std::uint64_t>();
            if (value == 0 || value > static_cast<std::uint64_t>(std::numeric_limits<long>::max())) return 0;
            return static_cast<long>(value);
        }
        const auto value = exp.get<std::int64_t>();
        return value > 0 && value <= std::numeric_limits<long>::max() ? static_cast<long>(value) : 0;
    } catch (const nlohmann::json::exception&) {
        return 0;
    }
}

struct Tokens { std::string id, refresh, access; long expires = 0; };

inline bool parse_response(const std::string& response, Tokens& result, long now = static_cast<long>(std::time(nullptr))) {
    const auto data = nlohmann::json::parse(response, nullptr, false);
    if (!data.is_object() || (data.contains("error") && !data.at("error").is_null())) return false;
    for (const auto* key : {"id_token", "refresh_token", "access_token"}) {
        if (!data.contains(key) || !data.at(key).is_string() || !valid_token(data.at(key).get<std::string>())) return false;
    }
    Tokens candidate{data.at("id_token").get<std::string>(), data.at("refresh_token").get<std::string>(),
                     data.at("access_token").get<std::string>(), 0};
    candidate.expires = expiration(candidate.id);
    if (candidate.expires <= now) return false;
    result = std::move(candidate);
    return true;
}

} // namespace token_validation
