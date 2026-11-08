#pragma once
#include <filesystem>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include "jvm_opts.hpp"
#include "natives.hpp"
#include "version.hpp"
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

inline std::vector<std::string> flatten_args(const json& items, const std::string& osn) {
    std::vector<std::string> out;
    for (auto& item : items) {
        if (item.is_string()) {
            out.push_back(item.get<std::string>());
        } else if (item.is_object() && (!item.contains("rules") || check_rules(item["rules"]))) {
            if (item.contains("value")) {
                if (item["value"].is_string()) out.push_back(item["value"]);
                else if (item["value"].is_array()) for (auto& x : item["value"]) out.push_back(x);
            }
        }
    }
    return out;
}

inline std::vector<std::string> build_launch_command(const json& vjson, const fs::path& mc_dir,
                                                     const std::string& vid, const json& account,
                                                     const std::string& java_path,
                                                     const std::string& max_mem,
                                                     const std::string& min_mem, bool optimize,
                                                     bool offline,
                                                     const std::vector<std::string>& extra_game_args = {}) {
    const std::string osn = os_name();
    const std::string path_sep = ";";
    std::vector<std::string> cp;
    for (auto& lib : vjson.value("libraries", json::array())) {
        if (lib.contains("rules") && !check_rules(lib["rules"])) continue;
        fs::path p = lib.contains("downloads") && lib["downloads"].contains("artifact")
                         ? mc_dir / "libraries" / lib["downloads"]["artifact"]["path"].get<std::string>()
                         : mc_dir / "libraries" / maven_path(lib.value("name", ""));
        if (!p.empty()) cp.push_back(fs::absolute(p).u8string());
    }
    cp.push_back(vjson.value("_client_jar", ""));
    std::string classpath;
    for (size_t i = 0; i < cp.size(); ++i) {
        if (i) classpath += path_sep;
        classpath += cp[i];
    }
    std::map<std::string, std::string> ph = {
        {"${auth_player_name}", account.value("name", "Steve")},
        {"${version_name}", vid},
        {"${game_directory}", fs::absolute(mc_dir).u8string()},
        {"${assets_root}", fs::absolute(mc_dir / "assets").u8string()},
        {"${assets_index_name}", vjson.value("assetIndex", json::object()).value("id", "legacy")},
        {"${auth_uuid}", account.value("uuid", "00000000-0000-0000-0000-000000000000")},
        {"${auth_access_token}", account.value("access_token", "0")},
        {"${auth_xuid}", account.value("xuid", "0")},
        {"${clientid}", account.value("clientid", "0")},
        {"${user_type}", account.value("user_type", "legacy")},
        {"${version_type}", vjson.value("type", "release")},
        {"${natives_directory}", fs::absolute(mc_dir / "versions" / vid / "natives").u8string()},
        {"${launcher_name}", "CoreNextLauncher"},
        {"${launcher_version}", "1.6.0"},
        {"${classpath}", classpath}};
    auto replace = [&](std::string s) {
        for (auto& [key, value] : ph) {
            size_t pos = 0;
            while ((pos = s.find(key, pos)) != std::string::npos) {
                s.replace(pos, key.size(), value);
                pos += value.size();
            }
        }
        return s;
    };

    // A JDK root / bin directory is normalised here as well, so direct CLI use
    // of mc_core.exe gets the same behaviour as the Python UI.
    std::vector<std::string> cmd = {normalize_java_path(java_path)};
    if (offline) cmd.push_back("-Dminecraft.launcher.brand=CoreNextLauncher-Offline");
    if (vjson.contains("arguments")) {
        for (auto& a : flatten_args(vjson["arguments"].value("jvm", json::array()), osn)) {
            cmd.push_back(replace(a));
        }
    }
    for (auto& a : build_jvm_opt_args(vid, max_mem, min_mem, optimize)) cmd.push_back(a);
    cmd.push_back(vjson["mainClass"]);
    if (vjson.contains("arguments")) {
        for (auto& a : flatten_args(vjson["arguments"].value("game", json::array()), osn)) {
            cmd.push_back(replace(a));
        }
    }
    // Launcher-supplied game arguments (for example --server/--port from the
    // server monitor's one-click join) come after the version's own arguments.
    for (const auto& extra : extra_game_args) {
        cmd.push_back(extra);
    }
    return cmd;
}

#ifdef _WIN32
inline std::wstring utf8_to_wide(const std::string& text) {
    if (text.empty()) return std::wstring();
    UINT code_page = CP_UTF8;
    DWORD flags = MB_ERR_INVALID_CHARS;
    int size = MultiByteToWideChar(code_page, flags, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (size <= 0) {
        code_page = CP_ACP;
        flags = 0;
        size = MultiByteToWideChar(code_page, flags, text.data(), static_cast<int>(text.size()), nullptr, 0);
    }
    if (size <= 0) return std::wstring();
    std::wstring out(static_cast<size_t>(size), L'\0');
    MultiByteToWideChar(code_page, flags, text.data(), static_cast<int>(text.size()), out.data(), size);
    return out;
}

// Quote every argument explicitly.  Windows paths must always be quoted so
// spaces in "Program Files" / JDK roots and user directories are preserved.
inline std::string quote_windows_arg(const std::string& arg) {
    std::string out;
    out.reserve(arg.size() + 2);
    out.push_back('"');
    size_t backslashes = 0;
    for (char ch : arg) {
        if (ch == '\\') {
            ++backslashes;
            continue;
        }
        if (ch == '"') {
            out.append(backslashes * 2 + 1, '\\');
            out.push_back('"');
            backslashes = 0;
            continue;
        }
        out.append(backslashes, '\\');
        backslashes = 0;
        out.push_back(ch);
    }
    out.append(backslashes * 2, '\\');
    out.push_back('"');
    return out;
}
#endif

inline int spawn_and_wait(const std::vector<std::string>& cmd, const fs::path& cwd) {
    std::string cmdline;
    for (const auto& arg : cmd) {
        if (!cmdline.empty()) cmdline.push_back(' ');
#ifdef _WIN32
        cmdline += quote_windows_arg(arg);
#else
        cmdline += "\"" + arg + "\"";
#endif
    }
#ifdef _WIN32
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    std::wstring wide_cmd = utf8_to_wide(cmdline);
    std::vector<wchar_t> buffer(wide_cmd.begin(), wide_cmd.end());
    buffer.push_back(L'\0');
    std::wstring wide_cwd;
    if (!cwd.empty()) {
        std::error_code ec;
        const fs::path absolute = fs::absolute(cwd, ec);
        wide_cwd = (ec ? cwd : absolute).wstring();
    }
    if (!CreateProcessW(nullptr, buffer.data(), nullptr, nullptr, TRUE, 0, nullptr,
                        wide_cwd.empty() ? nullptr : wide_cwd.c_str(), &si, &pi)) {
        return -1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return static_cast<int>(code);
#else
    return std::system(cmdline.c_str());
#endif
}
