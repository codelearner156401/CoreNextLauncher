#pragma once
#include <algorithm>
#include <atomic>
#include <climits>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#include "json.hpp"
#include "common.hpp"
#include "http.hpp"
#include "version.hpp"

namespace fs = std::filesystem;

// Only Mojang's own endpoints are used here. No mirrors, no third-party CDN.
inline constexpr const char* kMojangVersionManifest =
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json";
inline constexpr const char* kMojangAssetBase =
    "https://resources.download.minecraft.net/";

inline int progress_value(long long value) {
    if (value <= 0) return 0;
    if (value > static_cast<long long>(INT_MAX)) return INT_MAX;
    return static_cast<int>(value);
}

inline std::string format_size(long long bytes) {
    if (bytes >= 1024LL * 1024LL * 1024LL) {
        return std::to_string(bytes / (1024LL * 1024LL * 1024LL)) + " GB";
    }
    if (bytes >= 1024LL * 1024LL) {
        return std::to_string(bytes / (1024LL * 1024LL)) + " MB";
    }
    if (bytes >= 1024LL) {
        return std::to_string(bytes / 1024LL) + " KB";
    }
    return std::to_string(bytes) + " B";
}

inline json fetch_official_json(const std::string& url, const std::string& what) {
    std::string body;
    if (!fetch_bytes(url, body)) {
        throw std::runtime_error("无法访问 Mojang 官方源（" + what + "）：" + url);
    }
    try {
        return json::parse(body);
    } catch (const std::exception& e) {
        throw std::runtime_error("Mojang 官方返回的 " + what + " 不是有效 JSON：" + std::string(e.what()));
    }
}

inline std::string read_text_file(const fs::path& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("无法读取文件：" + path.string());
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

inline void write_text_file(const fs::path& path, const std::string& data) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("无法写入文件：" + path.string());
    out.write(data.data(), static_cast<std::streamsize>(data.size()));
    if (!out) throw std::runtime_error("写入失败：" + path.string());
}

inline std::string resolve_version_id(const json& manifest, const std::string& requested) {
    if (requested == "latest-release" || requested == "latest-snapshot") {
        const char* key = requested == "latest-release" ? "release" : "snapshot";
        if (manifest.contains("latest") && manifest["latest"].contains(key)) {
            return manifest["latest"][key].get<std::string>();
        }
    }
    return requested;
}

inline void install_version(const fs::path& mc_dir, const std::string& requested_id) {
    event::status("正在获取 Mojang 官方版本清单…");
    json manifest = fetch_official_json(kMojangVersionManifest, "version_manifest_v2.json");
    if (!manifest.contains("versions") || !manifest["versions"].is_array()) {
        throw std::runtime_error("官方版本清单格式异常");
    }

    std::string version_id = resolve_version_id(manifest, requested_id);
    const json* entry = nullptr;
    for (const auto& v : manifest["versions"]) {
        if (v.value("id", "") == version_id) {
            entry = &v;
            break;
        }
    }
    if (!entry) {
        throw std::runtime_error("Mojang 官方清单中不存在版本：" + requested_id);
    }

    std::string version_url = entry->value("url", "");
    if (version_url.empty()) throw std::runtime_error("官方清单缺少该版本的 JSON 地址");

    event::status("正在获取 " + version_id + " 的版本清单…");
    json version = fetch_official_json(version_url, "版本 JSON");
    if (version.contains("inheritsFrom")) {
        throw std::runtime_error("这是继承版本，请安装它继承的官方版本：" +
                                 version["inheritsFrom"].get<std::string>());
    }

    const std::string vid = version.value("id", version_id);
    fs::path version_dir = mc_dir / "versions" / vid;
    fs::create_directories(version_dir);
    write_text_file(version_dir / (vid + ".json"), version.dump(2));

    event::status("正在读取官方资源索引…");
    json asset_objects = json::object();
    if (version.contains("assetIndex") && version["assetIndex"].is_object()) {
        const json& index_meta = version["assetIndex"];
        const std::string index_id = index_meta.value("id", "");
        const std::string index_url = index_meta.value("url", "");
        const std::string index_sha1 = index_meta.value("sha1", "");
        if (!index_id.empty()) {
            fs::path index_path = mc_dir / "assets" / "indexes" / (index_id + ".json");
            std::string body;
            const long long expected = index_meta.value("size", 0LL);
            bool need_fetch = true;
            if (fs::exists(index_path)) {
                need_fetch = expected > 0 && file_size_or_negative(index_path) != expected;
                if (!need_fetch && !index_sha1.empty()) {
                    need_fetch = ascii_lower(sha1_file(index_path.string())) != ascii_lower(index_sha1);
                }
            }
            if (need_fetch) {
                if (index_url.empty()) throw std::runtime_error("官方资源索引缺少 URL");
                if (!fetch_bytes(index_url, body)) {
                    throw std::runtime_error("无法下载官方资源索引：" + index_id);
                }
                write_text_file(index_path, body);
                if (!index_sha1.empty() &&
                    ascii_lower(sha1_file(index_path.string())) != ascii_lower(index_sha1)) {
                    std::error_code ignore;
                    fs::remove(index_path, ignore);
                    throw std::runtime_error("官方资源索引 SHA1 校验失败：" + index_id);
                }
            } else {
                body = read_text_file(index_path);
            }
            try {
                json index = json::parse(body);
                if (index.contains("objects") && index["objects"].is_object()) {
                    asset_objects = index["objects"];
                }
            } catch (const std::exception& e) {
                throw std::runtime_error("解析官方资源索引失败：" + std::string(e.what()));
            }
        }
    }

    std::vector<DownloadTask> bulk_tasks;
    std::vector<DownloadTask> client_tasks;
    std::set<std::string> seen_destinations;
    auto add_task = [&](std::vector<DownloadTask>& target, const std::string& url,
                        const fs::path& dest, const std::string& sha1, long long size) {
        if (url.empty()) return;
        std::string key = dest.lexically_normal().string();
        if (!seen_destinations.insert(key).second) return;
        target.push_back(DownloadTask{url, dest, sha1, size});
    };

    if (!version.contains("downloads") || !version["downloads"].contains("client")) {
        throw std::runtime_error("该版本没有官方客户端 JAR 下载信息");
    }
    {
        const json& client = version["downloads"]["client"];
        add_task(client_tasks, client.value("url", ""), version_dir / (vid + ".jar"),
                 client.value("sha1", ""), client.value("size", 0LL));
    }

    if (version.contains("libraries") && version["libraries"].is_array()) {
        for (const auto& lib : version["libraries"]) {
            if (lib.contains("rules") && !check_rules(lib["rules"])) continue;
            if (!lib.contains("downloads") || !lib["downloads"].is_object()) continue;
            const json& downloads = lib["downloads"];

            if (downloads.contains("artifact") && downloads["artifact"].is_object()) {
                const json& artifact = downloads["artifact"];
                std::string path = artifact.value("path", "");
                if (!path.empty()) {
                    add_task(bulk_tasks, artifact.value("url", ""), mc_dir / "libraries" / path,
                             artifact.value("sha1", ""), artifact.value("size", 0LL));
                }
            }

            std::set<std::string> native_keys;
            if (lib.contains("natives") && lib["natives"].is_object() &&
                lib["natives"].contains(os_name())) {
                std::string key = lib["natives"][os_name()].get<std::string>();
                size_t pos = 0;
                while ((pos = key.find("${arch}")) != std::string::npos) {
                    key.replace(pos, 7, "64");
                }
                native_keys.insert(key);
            }
            native_keys.insert("natives-" + os_name());

            if (downloads.contains("classifiers") && downloads["classifiers"].is_object()) {
                for (const auto& key : native_keys) {
                    if (!downloads["classifiers"].contains(key)) continue;
                    const json& classifier = downloads["classifiers"][key];
                    std::string path = classifier.value("path", "");
                    if (!path.empty()) {
                        add_task(bulk_tasks, classifier.value("url", ""), mc_dir / "libraries" / path,
                                 classifier.value("sha1", ""), classifier.value("size", 0LL));
                    }
                }
            }
        }
    }

    if (version.contains("logging") && version["logging"].is_object() &&
        version["logging"].contains("client") && version["logging"]["client"].is_object()) {
        const json& logging = version["logging"]["client"];
        const std::string log_id = logging.value("id", "");
        if (!log_id.empty() && logging.contains("file") && logging["file"].is_object()) {
            const json& file = logging["file"];
            add_task(bulk_tasks, file.value("url", ""), mc_dir / "assets" / "log_configs" / log_id,
                     file.value("sha1", ""), file.value("size", 0LL));
        }
    }

    if (asset_objects.is_object()) {
        for (const auto& [name, object] : asset_objects.items()) {
            (void)name;
            const std::string hash = object.value("hash", "");
            if (hash.size() < 2) continue;
            const std::string prefix = hash.substr(0, 2);
            add_task(bulk_tasks, std::string(kMojangAssetBase) + prefix + "/" + hash,
                     mc_dir / "assets" / "objects" / prefix / hash, hash, object.value("size", 0LL));
        }
    }

    if (client_tasks.empty()) {
        throw std::runtime_error("官方客户端 JAR 缺少有效下载地址");
    }

    long long bulk_total_bytes = 0;
    for (const auto& task : bulk_tasks) {
        bulk_total_bytes += task.size > 0 ? task.size : 1;
    }
    const long long client_declared_size = client_tasks.front().size;
    long long combined_total = bulk_total_bytes + std::max<long long>(client_declared_size, 1);
    bool combined_total_known = client_declared_size > 0;

    const int workers = download_thread_count();
    if (!bulk_tasks.empty()) {
        event::status("开始下载 " + std::to_string(bulk_tasks.size()) + " 个依赖库/资源文件（" +
                      format_size(bulk_total_bytes) + "，并发 " + std::to_string(workers) + " 线程）");
        std::atomic<long long> done_bytes{0};
        int failed = download_all(
            bulk_tasks, workers,
            [&](size_t /*done*/, size_t /*total*/, const DownloadTask& task, bool /*ok*/) {
                const long long weight = task.size > 0 ? task.size : 1;
                const long long done = done_bytes.fetch_add(weight) + weight;
                event::progress(progress_value(done), progress_value(combined_total),
                                task.dest.filename().string(), done >= combined_total);
            });
        if (failed > 0) {
            throw std::runtime_error("有 " + std::to_string(failed) +
                                     " 个依赖库/资源文件下载失败，已完成的文件会保留，重新安装可跳过");
        }
    }

    event::status("正在下载客户端 JAR（HTTP Range 分块并发，预计 " +
                  format_size(std::max<long long>(client_declared_size, 1)) + "）…");
    const bool client_ok = download_ranged(
        client_tasks.front(), 8,
        [&](long long done, long long total_bytes) {
            if (!combined_total_known) {
                combined_total = bulk_total_bytes + std::max<long long>(total_bytes, 1);
                combined_total_known = true;
            }
            const long long overall_done = bulk_total_bytes + std::max<long long>(done, 0);
            event::progress(progress_value(overall_done), progress_value(combined_total),
                            "client.jar", overall_done >= combined_total);
        });
    if (!client_ok) {
        throw std::runtime_error("客户端 JAR 下载失败，请检查网络连接后重试");
    }

    const int total_files = static_cast<int>(bulk_tasks.size() + client_tasks.size());
    event::progress(total_files, total_files, "完成", true);
    event::done({{"version", vid},
                 {"files", total_files},
                 {"directory", version_dir.string()},
                 {"assets", asset_objects.size()}});
}
