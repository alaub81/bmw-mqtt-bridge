#pragma once

#include <cstdint>
#include <iomanip>
#include <map>
#include <sstream>
#include <string>

namespace homie_names {

inline std::string word_id(const std::string& field) {
    const std::string key = field.rfind("vehicle.", 0) == 0 ? field.substr(8) : field;
    std::string id;
    for (size_t i = 0; i < key.size(); ++i) {
        const unsigned char c = key[i];
        const bool upper = c >= 'A' && c <= 'Z';
        const bool lower = c >= 'a' && c <= 'z';
        const bool digit = c >= '0' && c <= '9';
        if (!upper && !lower && !digit) {
            if (!id.empty() && id.back() != '-') id += '-';
            continue;
        }
        if (upper && i > 0 && !id.empty() && id.back() != '-') {
            const unsigned char previous = key[i - 1];
            const bool next_lower = i + 1 < key.size() && key[i + 1] >= 'a' && key[i + 1] <= 'z';
            if ((previous >= 'a' && previous <= 'z') || (previous >= '0' && previous <= '9') ||
                ((previous >= 'A' && previous <= 'Z') && next_lower)) id += '-';
        }
        id += upper ? static_cast<char>(c + ('a' - 'A')) : static_cast<char>(c);
    }
    if (!id.empty() && id.back() == '-') id.pop_back();
    return id.empty() ? "field" : id;
}

inline std::string field_id(const std::string& field) {
    std::string id;
    bool capitalize = false;
    for (char c : word_id(field)) {
        if (c == '-') {
            capitalize = true;
            continue;
        }
        id += capitalize && c >= 'a' && c <= 'z' ? static_cast<char>(c - ('a' - 'A')) : c;
        capitalize = false;
    }
    return id;
}

inline std::string suffix(const std::string& field) {
    // Stable across processes and platforms, unlike std::hash.
    std::uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : field) {
        hash ^= c;
        hash *= 1099511628211ULL;
    }
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << hash;
    return out.str();
}

inline std::string label(const std::string& field) {
    static const std::map<std::string, std::string> labels = {
        {"vehicle.body.hood.isOpen", "Hood open"},
        {"vehicle.body.trunk.door.isOpen", "Trunk door open"},
        {"vehicle.body.trunk.window.isOpen", "Trunk window open"},
        {"vehicle.cabin.infotainment.navigation.currentLocation.heading", "Heading"},
        {"vehicle.cabin.sunroof.tiltStatus", "Sunroof tilt status"},
        {"vehicle.chassis.axle.row1.wheel.left.tire.pressure", "Tire pressure — front left"},
        {"vehicle.chassis.axle.row1.wheel.right.tire.pressure", "Tire pressure — front right"},
        {"vehicle.chassis.axle.row2.wheel.left.tire.pressure", "Tire pressure — rear left"},
        {"vehicle.chassis.axle.row2.wheel.right.tire.pressure", "Tire pressure — rear right"},
        {"vehicle.drivetrain.fuelSystem.level", "Fuel level"},
        {"vehicle.drivetrain.fuelSystem.remainingFuel", "Remaining fuel"},
        {"vehicle.vehicle.travelledDistance", "Travelled distance"},
        {"vehicle.drivetrain.electricEngine.charging.smeEnergyDeltaFullyCharged", "Energy delta fully charged"},
    };
    const auto known = labels.find(field);
    if (known != labels.end()) return known->second;
    std::string result = word_id(field);
    for (char& c : result) if (c == '-') c = ' ';
    if (result[0] >= 'a' && result[0] <= 'z') result[0] -= 'a' - 'A';
    return result;
}

} // namespace homie_names
