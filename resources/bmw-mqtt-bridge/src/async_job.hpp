#pragma once

#include <chrono>
#include <future>
#include <optional>
#include <utility>
#include "bridge_log.hpp"

// At most one owned operation is in flight. No thread is kept awake while idle.
// The owner polls results; only finish()/destruction waits for unfinished work.
template<typename Result>
class AsyncJob {
    std::future<Result> result;

    std::optional<Result> take() {
        if (!result.valid()) return std::nullopt;
        try { return result.get(); }
        catch (const std::exception&) {
            BMB_LOG(error) << "[bridge] background operation failed (details omitted)\n";
            return Result{};
        }
    }

public:
    bool running() const { return result.valid(); }

    template<typename Operation>
    bool start(Operation&& operation) {
        if (running()) return false;
        try {
            result = std::async(std::launch::async, std::forward<Operation>(operation));
            return true;
        } catch (const std::exception&) {
            BMB_LOG(error) << "[bridge] cannot start background operation\n";
            return false;
        }
    }

    std::optional<Result> poll() {
        if (!running() || result.wait_for(std::chrono::seconds(0)) != std::future_status::ready)
            return std::nullopt;
        return take();
    }

    std::optional<Result> finish() { return take(); }
};
