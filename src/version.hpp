#pragma once
#include <string>
#include <map>
#include <set>
#include <stdexcept>
#include <fstream>
#include <filesystem>
#include "json.hpp"
#include "common.hpp"
namespace fs = std::filesystem;
inline std::string os_name() {
#ifdef _WIN32
    return "windows";
#elif __APPLE__
    return "osx";
#else
    return "linux";
#endif
}
inline bool check_rules(const json& rules, const std::map<std::string, bool>& features = {}) {
    if (rules.is_null() || !rules.is_array() || rules.empty()) return true;
    std::string osn = os_name();
    bool allowed = false;
    for (auto& rule : rules) {
        bool match = true;
        if (rule.contains("os") && rule["os"].is_object()) {
            const auto& os = rule["os"];
            if (os.contains("name") && os["name"].is_string() && os["name"].get<std::string>() != osn) match = false;
        }
        // Minecraft 1.19+ gates optional arguments behind "features" (demo mode,
        // custom resolution, quick play ...). A feature is off unless the launcher
        // explicitly enables it, so an unmatched feature must drop the rule.
        if (match && rule.contains("features") && rule["features"].is_object()) {
            for (auto& [key, value] : rule["features"].items()) {
                bool wanted = value.is_boolean() ? value.get<bool>() : false;
                auto it = features.find(key);
                bool enabled = (it != features.end()) ? it->second : false;
                if (wanted != enabled) { match = false; break; }
            }
        }
        if (match) allowed = (rule.value("action", "allow") == "allow");
    }
    return allowed;
}
inline json load_json(const fs::path& p) { std::ifstream f(p); json j; f >> j; return j; }
inline json merge_version(const json& parent, const json& child) {
    json merged = parent;
    for (auto& key : {"id", "mainClass", "assetIndex", "assets", "downloads", "javaVersion", "type"}) {
        if (child.contains(key)) merged[key] = child[key];
    }
    std::map<std::string, json> libs;
    for (auto& l : parent.value("libraries", json::array())) if (l.contains("name")) libs[l["name"]] = l;
    for (auto& l : child.value("libraries", json::array())) if (l.contains("name")) libs[l["name"]] = l;
    merged["libraries"] = json::array();
    for (auto& [k, v] : libs) merged["libraries"].push_back(v);
    auto p_args = parent.value("arguments", json::object());
    auto c_args = child.value("arguments", json::object());
    json jvm = json::array(), game = json::array();
    if (p_args.contains("jvm")) for (auto& x : p_args["jvm"]) jvm.push_back(x);
    if (c_args.contains("jvm")) for (auto& x : c_args["jvm"]) jvm.push_back(x);
    if (p_args.contains("game")) for (auto& x : p_args["game"]) game.push_back(x);
    if (c_args.contains("game")) for (auto& x : c_args["game"]) game.push_back(x);
    merged["arguments"] = {{"jvm", jvm}, {"game", game}};
    return merged;
}
inline json resolve_version(const fs::path& mc_dir, const std::string& vid, std::map<std::string, json>& cache, std::set<std::string>& seen) {
    if (cache.count(vid)) return cache[vid];
    if (seen.count(vid)) throw std::runtime_error("版本继承环: " + vid);
    seen.insert(vid);
    fs::path vpath = mc_dir / "versions" / vid / (vid + ".json");
    json data = load_json(vpath);
    json merged = data.contains("inheritsFrom") ? merge_version(resolve_version(mc_dir, data["inheritsFrom"], cache, seen), data) : data;
    if (data.contains("inheritsFrom")) merged["_parent_id"] = data["inheritsFrom"];
    fs::path own_jar = mc_dir / "versions" / vid / (vid + ".jar");
    if (fs::exists(own_jar)) merged["_client_jar"] = fs::absolute(own_jar).u8string();
    else if (merged.contains("_parent_id")) {
        fs::path pj = mc_dir / "versions" / merged["_parent_id"].get<std::string>() / (merged["_parent_id"].get<std::string>() + ".jar");
        merged["_client_jar"] = fs::exists(pj) ? fs::absolute(pj).u8string() : "";
    } else merged["_client_jar"] = "";
    cache[vid] = merged;
    return merged;
}
inline json resolve_version(const fs::path& mc_dir, const std::string& vid) {
    std::map<std::string, json> cache; std::set<std::string> seen;
    return resolve_version(mc_dir, vid, cache, seen);
}
