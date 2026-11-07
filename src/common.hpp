#pragma once
#include <iostream>
#include <string>
#include <mutex>
#include <chrono>
#include "json.hpp"
using json = nlohmann::json;
namespace event {
inline std::mutex g_mtx;
inline std::mutex g_progress_mtx;
inline bool g_progress_started = false;
inline std::chrono::steady_clock::time_point g_last_progress;

inline void emit(const json& obj) {
    std::lock_guard<std::mutex> lk(g_mtx);
    std::cout << obj.dump() << std::endl;
}

// Progress lines are hard-throttled to one every 100 ms.  Download workers can
// call this from any thread; only the completion update passes force=true.
inline void progress(int done, int total, const std::string& current, bool force = false) {
    {
        std::lock_guard<std::mutex> lock(g_progress_mtx);
        const auto now = std::chrono::steady_clock::now();
        if (!force && g_progress_started &&
            std::chrono::duration_cast<std::chrono::milliseconds>(now - g_last_progress).count() < 100) {
            return;
        }
        g_progress_started = true;
        g_last_progress = now;
    }
    emit({{"type", "progress"}, {"done", done}, {"total", total}, {"current", current}});
}
inline void status(const std::string& msg) { emit({{"type", "status"}, {"text", msg}}); }
inline void log(const std::string& line) { emit({{"type", "log"}, {"text", line}}); }
inline void done(const json& data = json::object()) { emit({{"type", "done"}, {"data", data}}); }
inline void error(const std::string& msg) { emit({{"type", "error"}, {"message", msg}}); }
}
