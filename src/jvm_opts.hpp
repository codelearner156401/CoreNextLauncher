#pragma once
#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <system_error>
#include <vector>
#include "json.hpp"
#ifdef _WIN32
#include <windows.h>
#endif

namespace fs = std::filesystem;

inline std::string java_lower(std::string value) {
    for (char& c : value) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return value;
}

inline std::string java_trim(std::string value) {
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front()))) {
        value.erase(value.begin());
    }
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back()))) {
        value.pop_back();
    }
    return value;
}

inline bool memory_is_auto(const std::string& value) {
    const std::string text = java_lower(java_trim(value));
    return text.empty() || text == "auto" || text == "自动";
}

// Parses 4096M / 4G / 4GB / 4096.  Returns false for "auto" or invalid input.
inline bool parse_memory_mb(const std::string& value, long long& out) {
    std::string text = java_lower(java_trim(value));
    text.erase(std::remove_if(text.begin(), text.end(),
                              [](unsigned char c) { return std::isspace(c); }),
               text.end());
    if (text.empty()) return false;

    long long multiplier = 1;
    if (text.size() >= 2 && text.substr(text.size() - 2) == "gb") {
        multiplier = 1024;
        text.erase(text.size() - 2);
    } else if (text.size() >= 2 && text.substr(text.size() - 2) == "mb") {
        text.erase(text.size() - 2);
    } else if (!text.empty() && text.back() == 'g') {
        multiplier = 1024;
        text.pop_back();
    } else if (!text.empty() && text.back() == 'm') {
        text.pop_back();
    }
    if (text.empty()) return false;

    errno = 0;
    char* end = nullptr;
    const double number = std::strtod(text.c_str(), &end);
    if (errno != 0 || end == text.c_str() || (end && *end != '\0') || number <= 0) {
        return false;
    }
    const double megabytes = number * static_cast<double>(multiplier);
    if (megabytes < 1.0 || megabytes > 1024.0 * 1024.0) return false;
    out = static_cast<long long>(megabytes + 0.5);
    return true;
}

inline std::string java_absolute_string(const fs::path& path) {
    std::error_code ec;
    const fs::path absolute = fs::absolute(path, ec);
    return ec ? path.u8string() : absolute.u8string();
}

// Accept a JDK root, a bin directory or an executable.  New JDKs (including
// Java 27) work because no version number is assumed.  On Windows javaw.exe is
// preferred so launching the game does not open a console window.
inline std::string normalize_java_path(const std::string& input) {
    std::string raw = input;
    while (!raw.empty() && (raw.front() == ' ' || raw.front() == '\t' || raw.front() == '"')) {
        raw.erase(raw.begin());
    }
    while (!raw.empty() && (raw.back() == ' ' || raw.back() == '\t' || raw.back() == '"')) {
        raw.pop_back();
    }
    if (raw.empty()) return "java";

    std::error_code ec;
    fs::path path = fs::u8path(raw).lexically_normal();
    if (fs::is_directory(path, ec)) {
        fs::path bin = java_lower(path.filename().u8string()) == "bin" ? path : path / "bin";
#ifdef _WIN32
        const std::vector<std::string> names = {"javaw.exe", "java.exe"};
#else
        const std::vector<std::string> names = {"java"};
#endif
        for (const auto& name : names) {
            const fs::path candidate = bin / name;
            if (fs::is_regular_file(candidate, ec)) return java_absolute_string(candidate);
        }
        // Keep a predictable path so the caller can report a useful error.
        return java_absolute_string(bin / names.front());
    }

    if (fs::is_regular_file(path, ec)) {
#ifdef _WIN32
        if (java_lower(path.filename().u8string()) == "java.exe") {
            fs::path javaw = path;
            javaw.replace_filename("javaw.exe");
            if (fs::is_regular_file(javaw, ec)) return java_absolute_string(javaw);
        }
#endif
        return java_absolute_string(path);
    }
    return raw;
}

inline long long total_physical_mem_mb() {
#ifdef _WIN32
    MEMORYSTATUSEX st{};
    st.dwLength = sizeof(st);
    if (GlobalMemoryStatusEx(&st)) return st.ullTotalPhys / (1024 * 1024);
#endif
    return 8192;
}

inline std::vector<std::string> build_jvm_opt_args(const std::string& mc_version,
                                                   const std::string& max_mem,
                                                   const std::string& min_mem, bool optimize) {
    (void)mc_version;
    std::vector<std::string> args;
    if (!optimize) return args;

    const long long total = total_physical_mem_mb();
    long long default_xmx = std::min<long long>(8192, std::max<long long>(1024, total / 2));
    long long default_xms = std::min<long long>(4096, std::max<long long>(512, total / 4));

    long long xmx_mb = default_xmx;
    long long xms_mb = default_xms;
    if (!memory_is_auto(max_mem)) {
        long long parsed = 0;
        if (parse_memory_mb(max_mem, parsed)) xmx_mb = parsed;
    }
    if (!memory_is_auto(min_mem)) {
        long long parsed = 0;
        if (parse_memory_mb(min_mem, parsed)) xms_mb = parsed;
    }

    // A JVM refuses to start when -Xms is larger than -Xmx.  Older configs and
    // the memory combos can produce that combination, so clamp defensively.
    if (xmx_mb < 256) xmx_mb = 256;
    if (xms_mb < 1) xms_mb = std::min<long long>(default_xms, xmx_mb);
    if (xms_mb > xmx_mb) xms_mb = xmx_mb;

    args.push_back("-Xmx" + std::to_string(xmx_mb) + "M");
    args.push_back("-Xms" + std::to_string(xms_mb) + "M");
    args.push_back("-XX:+UseG1GC");
    args.push_back("-XX:MaxGCPauseMillis=200");
    args.push_back("-Dfile.encoding=UTF-8");
    return args;
}
