#include <iostream>
#include <string>
#include <sstream>
#include <exception>
#include <filesystem>
#include <curl/curl.h>
#include "json.hpp"
#include "common.hpp"
#include "version.hpp"
#include "launch.hpp"
#include "crash.hpp"
#include "install.hpp"
#include "http.hpp"

using json = nlohmann::json;
namespace fs = std::filesystem;

int main(int argc, char** argv) {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    try {
        if (argc < 2) return 1;
        std::string cmd = argv[1];
        std::stringstream ss; ss << std::cin.rdbuf();
        json input = json::parse(ss.str());
        std::string osn = os_name();

        if (cmd == "scan") {
            fs::path mc_dir = input["mc_dir"].get<std::string>();
            fs::path vdir = mc_dir / "versions";
            json out = json::array();
            if (fs::exists(vdir)) {
                for (auto& d : fs::directory_iterator(vdir)) {
                    if (!d.is_directory()) continue;
                    std::string id = d.path().filename().string();
                    if (!fs::exists(d.path() / (id + ".json"))) continue;
                    out.push_back({{"id", id}, {"type", "release"}});
                }
            }
            event::done(out);
        } else if (cmd == "launch") {
            fs::path mc_dir = input["mc_dir"].get<std::string>();
            std::string vid = input["version_id"].get<std::string>();
            json vjson = resolve_version(mc_dir, vid);
            extract_natives(vjson, mc_dir, vid, osn);
            std::vector<std::string> extra_args;
            if (input.contains("extra_game_args") && input["extra_game_args"].is_array()) {
                for (auto& item : input["extra_game_args"]) {
                    if (item.is_string()) extra_args.push_back(item.get<std::string>());
                }
            }
            std::vector<std::string> jvm_args;
            if (input.contains("jvm_args") && input["jvm_args"].is_array()) {
                for (auto& item : input["jvm_args"]) {
                    if (item.is_string()) jvm_args.push_back(item.get<std::string>());
                }
            }
            fs::path game_dir;
            if (input.contains("game_dir") && input["game_dir"].is_string()) {
                game_dir = input["game_dir"].get<std::string>();
            }
            auto argv_list = build_launch_command(vjson, mc_dir, vid, input["account"], input.value("java_path", "java"), input.value("max_mem", "auto"), input.value("min_mem", "auto"), true, input.value("offline_mode", false), extra_args, jvm_args, game_dir);
            int code = spawn_and_wait(argv_list, game_dir.empty() ? mc_dir : game_dir);
            event::done({{"exit_code", code}});
        } else if (cmd == "install") {
            fs::path mc_dir = input["mc_dir"].get<std::string>();
            std::string vid = input["version_id"].get<std::string>();
            install_version(mc_dir, vid);
        } else if (cmd == "download-url") {
            // Generic ranged download for the toolbox: same worker pool, retry and
            // checksum path the installer uses, so a broken mirror cannot leave a
            // half file behind.
            DownloadTask task;
            task.url = input.at("url").get<std::string>();
            task.dest = fs::path(input.at("dest").get<std::string>());
            task.sha1 = input.value("sha1", "");
            task.size = input.value("size", 0LL);
            const int threads = input.value("threads", 8);
            if (task.url.empty()) throw std::runtime_error("缺少下载地址");
            if (task.dest.empty()) throw std::runtime_error("缺少保存路径");
            event::status("开始下载：" + task.url);
            const std::string label = task.dest.filename().string();
            const bool ok = download_ranged(task, threads,
                [&](long long done, long long total) { event::progress(done, total, label); });
            if (!ok) throw std::runtime_error("下载失败：" + task.url);
            const long long written = file_size_or_negative(task.dest);
            event::done({{"dest", task.dest.string()}, {"size", written}});
        } else {
            event::done({{"data", "OK"}});
        }
    } catch (std::exception& e) {
        event::error(e.what());
        curl_global_cleanup();
        return 1;
    }
    curl_global_cleanup();
    return 0;
}
