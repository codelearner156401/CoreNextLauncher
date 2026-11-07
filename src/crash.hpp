#pragma once
#include <string>
#include <vector>
#include <regex>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <sstream>
namespace fs = std::filesystem;
inline std::vector<std::string> analyze_crash(const fs::path& mc_dir) {
    fs::path cdir = mc_dir / "crash-reports"; if (!fs::exists(cdir)) return {};
    fs::path latest; std::time_t lt = 0;
    for (auto& p : fs::directory_iterator(cdir)) {
        auto t = fs::last_write_time(p.path()); auto tt = std::chrono::duration_cast<std::chrono::seconds>(t.time_since_epoch()).count();
        if (tt > lt) { lt = tt; latest = p.path(); }
    }
    if (latest.empty()) return {};
    std::ifstream f(latest); std::stringstream ss; ss << f.rdbuf(); std::string text = ss.str();
    std::vector<std::string> res;
    if (std::regex_search(text, std::regex("OutOfMemoryError"))) res.push_back("内存不足：请增大最大内存。");
    if (std::regex_search(text, std::regex("ClassVersionError"))) res.push_back("Java版本不匹配。");
    if (res.empty()) res.push_back("未知崩溃，请查看日志。");
    return res;
}
