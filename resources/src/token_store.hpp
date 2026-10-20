#pragma once

#include <filesystem>
#include <fstream>
#include <string>
#include "token_validation.hpp"

namespace token_store {

// Bound input before parsing. Corrupt canonical state must never fall back to
// stale legacy credentials; an interrupted staging file is never authoritative.
inline bool read(const std::filesystem::path& path, std::string& content) {
    std::ifstream input(path, std::ios::binary);
    if (!input) return false;
    content.clear();
    char buffer[4096];
    while (input.read(buffer, sizeof(buffer)) || input.gcount()) {
        content.append(buffer, static_cast<size_t>(input.gcount()));
        if (content.size() > 1024 * 1024) return false;
    }
    return input.eof();
}

inline bool decode(const std::string& content, token_validation::Tokens& result, bool bundle) {
    const auto data = nlohmann::json::parse(content, nullptr, false);
    if (!data.is_object() || (bundle && (!data.contains("version") || !data.at("version").is_number_integer() || data.at("version") != 1))) return false;
    token_validation::Tokens candidate;
    for (const auto* key : {"id_token", "refresh_token", "access_token"}) {
        if (!data.contains(key) || !data.at(key).is_string()) return false;
        const auto value = data.at(key).get<std::string>();
        // Old deployments may not have retained an access token; BMW streaming
        // and refresh need only the ID and refresh tokens.
        if (!(bundle && std::string(key) == "access_token" && value.empty()) &&
            !token_validation::valid_token(value)) return false;
    }
    candidate.id = data.at("id_token").get<std::string>();
    candidate.refresh = data.at("refresh_token").get<std::string>();
    candidate.access = data.at("access_token").get<std::string>();
    candidate.expires = token_validation::expiration(candidate.id);
    result = std::move(candidate);
    return true;
}

inline bool load(const std::filesystem::path& directory, token_validation::Tokens& result) {
    std::string content;
    std::error_code error;
    const auto canonical = directory / "tokens.json";
    const auto status = std::filesystem::symlink_status(canonical, error);
    if (error && error != std::errc::no_such_file_or_directory) return false;
    if (status.type() != std::filesystem::file_type::not_found)
        return read(canonical, content) && decode(content, result, true);
    token_validation::Tokens candidate;
    auto legacy = [&](const char* name, std::string& value) {
        if (!read(directory / name, value)) return false;
        const auto end = value.find_last_not_of(" \t\r\n");
        value = end == std::string::npos ? "" : value.substr(0, end + 1);
        const auto begin = value.find_first_not_of(" \t\r\n");
        if (begin != std::string::npos) value.erase(0, begin);
        return token_validation::valid_token(value);
    };
    if (!legacy("id_token.txt", candidate.id) || !legacy("refresh_token.txt", candidate.refresh)) return false;
    if (std::filesystem::exists(directory / "access_token.txt", error) &&
        !legacy("access_token.txt", candidate.access)) return false;
    candidate.expires = token_validation::expiration(candidate.id);
    result = std::move(candidate);
    return true;
}

template<typename Writer>
inline bool save(const std::filesystem::path& directory, const token_validation::Tokens& tokens, Writer writer) {
    const nlohmann::json data{{"version", 1}, {"id_token", tokens.id},
                              {"refresh_token", tokens.refresh}, {"access_token", tokens.access}};
    token_validation::Tokens validated;
    if (!decode(data.dump(), validated, true)) return false;
    if (!writer((directory / "tokens.json").string(), data.dump())) return false;
    // Cleanup follows the durable commit. If interrupted, the bundle still wins.
    for (const auto* name : {"id_token.txt", "refresh_token.txt", "access_token.txt"}) {
        std::error_code error;
        std::filesystem::remove(directory / name, error);
    }
    return true;
}

} // namespace token_store
