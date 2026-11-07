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
            auto argv_list = build_launch_command(vjson, mc_dir, vid, input["account"], input.value("java_path", "java"), input.value("max_mem", "auto"), input.value("min_mem", "auto"), true, input.value("offline_mode", false), extra_args);
            int code = spawn_and_wait(argv_list, mc_dir);
            event::done({{"exit_code", code}});
        } else if (cmd == "install") {
            fs::path mc_dir = input["mc_dir"].get<std::string>();
            std::string vid = input["version_id"].get<std::string>();
            install_version(mc_dir, vid);
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
