#pragma once
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <set>
#include <string>
#include <system_error>
#include "version.hpp"
#ifdef _WIN32
#include <windows.h>
#endif

namespace fs = std::filesystem;

inline std::string native_lower(std::string value) {
    for (char& c : value) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return value;
}

// The C++ core is a 32-bit process, but Minecraft normally runs on the native
// 64-bit JVM.  Use the native OS architecture, not the launcher's bitness.
inline std::string native_arch_token() {
#ifdef _WIN32
    SYSTEM_INFO info{};
    GetNativeSystemInfo(&info);
    switch (info.wProcessorArchitecture) {
        case PROCESSOR_ARCHITECTURE_ARM64:
            return "arm64";
        case PROCESSOR_ARCHITECTURE_INTEL:
            return "32";
        default:
            return "64";
    }
#else
    return "64";
#endif
}

inline std::string native_classifier_key() {
#ifdef _WIN32
    const std::string arch = native_arch_token();
    if (arch == "arm64") return "natives-windows-arm64";
    if (arch == "32") return "natives-windows-x86";
    return "natives-windows";
#else
    return "natives-" + os_name();
#endif
}

inline std::string maven_path(const std::string& name) {
    size_t c1 = name.find(':');
    size_t c2 = name.find(':', c1 + 1);
    if (c1 == std::string::npos || c2 == std::string::npos) return "";
    std::string group = name.substr(0, c1);
    std::string artifact = name.substr(c1 + 1, c2 - c1 - 1);
    std::string version = name.substr(c2 + 1);
    std::string group_path = group;
    for (auto& ch : group_path) {
        if (ch == '.') ch = '/';
    }
    return group_path + "/" + artifact + "/" + version + "/" + artifact + "-" + version + ".jar";
}

inline fs::path extract_natives(const json& vjson, const fs::path& mc_dir,
                                const std::string& vid, const std::string& osn) {
    fs::path natives_dir = mc_dir / "versions" / vid / "natives";
    std::error_code ec;
    // Remove stale natives so switching Java/architecture cannot leave old
    // DLLs behind.
    fs::remove_all(natives_dir, ec);
    fs::create_directories(natives_dir, ec);

    std::set<fs::path> native_jars;
    const std::string architecture = native_arch_token();
    const std::string classifier_key = native_classifier_key();

    for (auto& lib : vjson.value("libraries", json::array())) {
        if (lib.contains("rules") && !check_rules(lib["rules"])) continue;
        if (!lib.contains("downloads") || !lib["downloads"].is_object()) continue;
        const json& downloads = lib["downloads"];

        // Legacy format: downloads.classifiers["natives-windows"].
        if (downloads.contains("classifiers") && downloads["classifiers"].is_object()) {
            std::set<std::string> keys;
            if (lib.contains("natives") && lib["natives"].is_object() &&
                lib["natives"].contains(osn) && lib["natives"][osn].is_string()) {
                std::string key = lib["natives"][osn].get<std::string>();
                size_t pos = 0;
                while ((pos = key.find("${arch}")) != std::string::npos) {
                    key.replace(pos, 7, architecture);
                }
                keys.insert(key);
            }
            keys.insert(classifier_key);
            keys.insert("natives-" + osn);
            for (const auto& key : keys) {
                if (!downloads["classifiers"].contains(key)) continue;
                const json& classifier = downloads["classifiers"][key];
                std::string path = classifier.value("path", "");
                if (!path.empty()) native_jars.insert(mc_dir / "libraries" / path);
            }
        }

        // Modern format (1.19+): a separate library entry whose artifact is
        // lwjgl-...-natives-windows.jar.  Select only the architecture we are
        // actually running on, otherwise arm64/x86 DLLs would be mixed in.
        if (downloads.contains("artifact") && downloads["artifact"].is_object()) {
            const json& artifact = downloads["artifact"];
            const std::string path = artifact.value("path", "");
            if (!path.empty()) {
                const fs::path jar = mc_dir / "libraries" / path;
                const std::string filename = native_lower(jar.filename().string());
                const std::string wanted = "-" + classifier_key + ".jar";
                if (filename.size() >= wanted.size() &&
                    filename.compare(filename.size() - wanted.size(), wanted.size(), wanted) == 0) {
                    native_jars.insert(jar);
                }
            }
        }
    }

    for (const auto& jar : native_jars) {
        if (!fs::is_regular_file(jar)) {
            event::log("natives 文件缺失，无法解压：" + jar.filename().string());
            continue;
        }
        // Windows 10+ ships bsdtar, which can extract zip/jar archives.
        const std::string command =
            "tar -xf \"" + jar.string() + "\" -C \"" + natives_dir.string() + "\"";
        const int rc = std::system(command.c_str());
        if (rc != 0) {
            event::log("natives 解压失败（" + std::to_string(rc) + "）：" + jar.filename().string());
        }
    }

    std::error_code count_ec;
    std::size_t extracted = 0;
    for (auto& entry : fs::recursive_directory_iterator(natives_dir, count_ec)) {
        if (!entry.is_regular_file(count_ec)) continue;
        const std::string ext = native_lower(entry.path().extension().string());
        if (ext == ".dll" || ext == ".so" || ext == ".dylib") ++extracted;
    }
    if (extracted == 0) {
        event::log("natives 目录中没有本地库，游戏可能因缺少 LWJGL DLL 而启动失败");
    } else {
        event::log("natives 已解压 " + std::to_string(extracted) + " 个本地库");
    }
    return natives_dir;
}
