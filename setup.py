import os

# 自动定位你的项目目录
base_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(base_dir, "src")
os.makedirs(src_dir, exist_ok=True)

files = {}

# 1. common.hpp
files["common.hpp"] = r"""#pragma once
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
"""

# 2. sha1.hpp
files["sha1.hpp"] = r"""#pragma once
#include <iomanip>
#include <sstream>
#include <string>
#include <fstream>
#include <openssl/evp.h>

// SHA1 is used only for Mojang's content-addressed asset metadata compatibility.
inline std::string sha1_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "";
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    if (!ctx) return "";
    if (EVP_DigestInit_ex(ctx, EVP_sha1(), nullptr) != 1) {
        EVP_MD_CTX_free(ctx);
        return "";
    }
    char buffer[65536];
    while (f.read(buffer, sizeof(buffer)) || f.gcount() > 0) {
        EVP_DigestUpdate(ctx, buffer, static_cast<size_t>(f.gcount()));
    }
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    if (EVP_DigestFinal_ex(ctx, digest, &length) != 1) {
        EVP_MD_CTX_free(ctx);
        return "";
    }
    EVP_MD_CTX_free(ctx);
    std::ostringstream out;
    for (unsigned int i = 0; i < length; ++i) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(digest[i]);
    }
    return out.str();
}
"""

# 3. http.hpp
files["http.hpp"] = r"""#pragma once
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>
#include <vector>
#include <curl/curl.h>
#include "common.hpp"
#include "sha1.hpp"
namespace fs = std::filesystem;

struct DownloadTask {
    std::string url;
    fs::path dest;
    std::string sha1;
    long long size = 0;
};

// One initial attempt plus this many retries.
inline constexpr int kDownloadRetries = 3;

inline std::string ascii_lower(std::string value) {
    for (char& c : value) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return value;
}

inline long long file_size_or_negative(const fs::path& p) {
    std::error_code ec;
    auto size = fs::file_size(p, ec);
    return ec ? -1 : static_cast<long long>(size);
}

// A file is accepted only when its byte size and SHA1 match the official
// manifest.  Files with no expected SHA1 fall back to a size check.
inline bool verify_download_file(const fs::path& p, const DownloadTask& task) {
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return false;
    const long long actual = file_size_or_negative(p);
    if (actual < 0) return false;
    if (task.size > 0 && actual != task.size) return false;
    if (!task.sha1.empty()) {
        const std::string digest = ascii_lower(sha1_file(p.string()));
        if (digest != ascii_lower(task.sha1)) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Shared DNS / TLS session / connection pool.  libcurl requires lock callbacks
// when a share handle is used from multiple threads.
// ---------------------------------------------------------------------------
inline CURLSH* g_share = nullptr;
inline std::once_flag g_share_once;
inline std::mutex g_share_mutex;

inline void share_lock_cb(CURL*, curl_lock_data, curl_lock_access, void*) {
    g_share_mutex.lock();
}
inline void share_unlock_cb(CURL*, curl_lock_data, void*) {
    g_share_mutex.unlock();
}

inline void init_share() {
    std::call_once(g_share_once, [] {
        CURLSH* share = curl_share_init();
        if (!share) return;
        curl_share_setopt(share, CURLSHOPT_SHARE, CURL_LOCK_DATA_DNS);
        curl_share_setopt(share, CURLSHOPT_SHARE, CURL_LOCK_DATA_SSL_SESSION);
        curl_share_setopt(share, CURLSHOPT_LOCKFUNC, share_lock_cb);
        curl_share_setopt(share, CURLSHOPT_UNLOCKFUNC, share_unlock_cb);
        g_share = share;
    });
}

inline void apply_common_options(CURL* c) {
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_MAXREDIRS, 10L);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "CoreLauncher/1.3.1");
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 20L);
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_LIMIT, 512L);
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_TIME, 45L);
    curl_easy_setopt(c, CURLOPT_HTTP_VERSION, CURL_HTTP_VERSION_2TLS);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(c, CURLOPT_BUFFERSIZE, 256L * 1024L);
    if (g_share) curl_easy_setopt(c, CURLOPT_SHARE, g_share);
}

inline int download_thread_count() {
    const unsigned hc = std::thread::hardware_concurrency();
    const int suggested = hc ? static_cast<int>(hc) * 2 : 24;
    return std::max(16, std::min(32, suggested));
}

inline int normalize_worker_count(int requested) {
    if (requested <= 0) return download_thread_count();
    return std::max(1, std::min(64, requested));
}

// ---------------------------------------------------------------------------
// Small metadata request (version manifest / asset index).
// ---------------------------------------------------------------------------
inline bool fetch_bytes(const std::string& url, std::string& out) {
    init_share();
    for (int attempt = 0; attempt <= kDownloadRetries; ++attempt) {
        CURL* c = curl_easy_init();
        if (!c) return false;
        apply_common_options(c);
        std::string buf;
        curl_easy_setopt(c, CURLOPT_URL, url.c_str());
        curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, +[](void* ptr, size_t size, size_t n, void* ud) -> size_t {
            auto* s = static_cast<std::string*>(ud);
            s->append(static_cast<char*>(ptr), size * n);
            return size * n;
        });
        curl_easy_setopt(c, CURLOPT_WRITEDATA, &buf);
        const CURLcode rc = curl_easy_perform(c);
        long code = 0;
        curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(c);
        if (rc == CURLE_OK && code >= 200 && code < 300) {
            out = std::move(buf);
            return true;
        }
        if (attempt < kDownloadRetries) {
            std::this_thread::sleep_for(std::chrono::milliseconds(500 << std::min(attempt, 4)));
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// Single-file download with retry and strict verification.
// ---------------------------------------------------------------------------
inline bool download_single(const DownloadTask& task, int max_retries = kDownloadRetries) {
    if (verify_download_file(task.dest, task)) return true;
    std::error_code ec;
    if (task.dest.has_parent_path()) {
        fs::create_directories(task.dest.parent_path(), ec);
        if (ec) return false;
    }

    fs::path tmp = task.dest;
    tmp += ".part";

    for (int attempt = 0; attempt <= max_retries; ++attempt) {
        CURL* c = curl_easy_init();
        if (!c) return false;
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) {
            curl_easy_cleanup(c);
            return false;
        }

        apply_common_options(c);
        curl_easy_setopt(c, CURLOPT_URL, task.url.c_str());
        if (task.size > 0) {
            curl_easy_setopt(c, CURLOPT_MAXFILESIZE_LARGE, static_cast<curl_off_t>(task.size));
        }
        curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, +[](void* ptr, size_t size, size_t n, void* ud) -> size_t {
            auto* f = static_cast<std::ofstream*>(ud);
            const size_t bytes = size * n;
            f->write(static_cast<const char*>(ptr), static_cast<std::streamsize>(bytes));
            return f->good() ? bytes : 0;
        });
        curl_easy_setopt(c, CURLOPT_WRITEDATA, &out);

        const CURLcode rc = curl_easy_perform(c);
        long code = 0;
        curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_cleanup(c);
        out.close();

        const bool http_ok = code >= 200 && code < 300;
        if (rc == CURLE_OK && http_ok && verify_download_file(tmp, task)) {
            fs::remove(task.dest, ec);
            fs::rename(tmp, task.dest, ec);
            if (!ec) return true;
        }

        fs::remove(tmp, ec);
        if (attempt < max_retries) {
            std::this_thread::sleep_for(std::chrono::milliseconds(500 << std::min(attempt, 4)));
        }
    }
    return false;
}

using DownloadProgress = std::function<void(size_t done, size_t total, const DownloadTask& task, bool ok)>;

// ---------------------------------------------------------------------------
// Worker pool for libraries / assets.  16-32 threads by default.
// ---------------------------------------------------------------------------
inline int download_all(const std::vector<DownloadTask>& tasks, int threads = 0,
                        const DownloadProgress& progress = {}) {
    if (tasks.empty()) return 0;
    init_share();
    const int workers = std::min(normalize_worker_count(threads), static_cast<int>(tasks.size()));
    std::atomic<size_t> next{0};
    std::atomic<size_t> finished{0};
    std::atomic<int> failed{0};
    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(workers));

    for (int i = 0; i < workers; ++i) {
        pool.emplace_back([&]() {
            while (true) {
                const size_t index = next.fetch_add(1);
                if (index >= tasks.size()) break;
                bool ok = false;
                try {
                    ok = download_single(tasks[index]);
                } catch (...) {
                    ok = false;
                }
                if (!ok) failed.fetch_add(1);
                const size_t done = finished.fetch_add(1) + 1;
                if (progress) {
                    try {
                        progress(done, tasks.size(), tasks[index], ok);
                    } catch (...) {
                        // A reporter must never terminate a worker.
                    }
                }
            }
        });
    }
    for (auto& thread : pool) thread.join();
    return failed.load();
}

// ---------------------------------------------------------------------------
// Client JAR: probe size when necessary, split into HTTP Range chunks, then
// concatenate and verify the whole file's SHA1.
// ---------------------------------------------------------------------------
inline long long fetch_remote_length(const std::string& url) {
    init_share();
    for (int attempt = 0; attempt <= 1; ++attempt) {
        CURL* c = curl_easy_init();
        if (!c) return -1;
        apply_common_options(c);
        curl_easy_setopt(c, CURLOPT_URL, url.c_str());
        curl_easy_setopt(c, CURLOPT_NOBODY, 1L);
        const CURLcode rc = curl_easy_perform(c);
        long code = 0;
        curl_off_t length = -1;
        curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
        curl_easy_getinfo(c, CURLINFO_CONTENT_LENGTH_DOWNLOAD_T, &length);
        curl_easy_cleanup(c);
        if (rc == CURLE_OK && code >= 200 && code < 300 && length > 0) {
            return static_cast<long long>(length);
        }
        if (attempt == 0) std::this_thread::sleep_for(std::chrono::milliseconds(300));
    }
    return -1;
}

struct RangeWriteContext {
    std::ofstream* file = nullptr;
    int status_code = 0;
    std::atomic<bool>* cancelled = nullptr;
    std::atomic<long long>* aggregate = nullptr;
    long long total = 0;
    long long bytes = 0;
    bool io_error = false;
    std::function<void(long long, long long)>* report = nullptr;
    std::chrono::steady_clock::time_point last_report{};
};

inline size_t range_header_cb(char* buffer, size_t size, size_t items, void* userdata) {
    auto* ctx = static_cast<RangeWriteContext*>(userdata);
    const size_t bytes = size * items;
    std::string line(buffer, bytes);
    if (line.compare(0, 5, "HTTP/") == 0) {
        const size_t space = line.find(' ');
        if (space != std::string::npos && space + 3 < line.size()) {
            int code = 0;
            bool valid = true;
            for (size_t i = 1; i <= 3; ++i) {
                const unsigned char ch = static_cast<unsigned char>(line[space + i]);
                if (!std::isdigit(ch)) {
                    valid = false;
                    break;
                }
                code = code * 10 + (ch - '0');
            }
            if (valid) ctx->status_code = code;
        }
    }
    return bytes;
}

inline size_t range_write_cb(char* ptr, size_t size, size_t items, void* userdata) {
    auto* ctx = static_cast<RangeWriteContext*>(userdata);
    if (ctx->cancelled && ctx->cancelled->load()) return 0;
    if (ctx->status_code != 206) return 0;
    const size_t bytes = size * items;
    ctx->file->write(ptr, static_cast<std::streamsize>(bytes));
    if (!ctx->file->good()) {
        ctx->io_error = true;
        return 0;
    }
    ctx->bytes += static_cast<long long>(bytes);
    if (ctx->aggregate) {
        const long long done = ctx->aggregate->fetch_add(static_cast<long long>(bytes)) + static_cast<long long>(bytes);
        const auto now = std::chrono::steady_clock::now();
        if (now - ctx->last_report >= std::chrono::milliseconds(100)) {
            ctx->last_report = now;
            if (ctx->report && *ctx->report) (*ctx->report)(done, ctx->total);
        }
    }
    return bytes;
}

inline bool download_ranged(const DownloadTask& task, int chunk_threads = 8,
                            const std::function<void(long long, long long)>& byte_progress = {}) {
    if (verify_download_file(task.dest, task)) {
        if (byte_progress) {
            const long long total = task.size > 0 ? task.size : 1;
            byte_progress(total, total);
        }
        return true;
    }

    long long total = task.size;
    if (total <= 0) total = fetch_remote_length(task.url);
    if (total <= 0 || total < 1024LL * 1024LL) return download_single(task);

    int chunks = 8;
    if (total < 4LL * 1024LL * 1024LL) chunks = 2;
    else if (total < 16LL * 1024LL * 1024LL) chunks = 4;
    chunks = std::max(1, chunks);
    const int workers = std::max(1, std::min(chunk_threads, chunks));

    std::error_code ec;
    if (task.dest.has_parent_path()) {
        fs::create_directories(task.dest.parent_path(), ec);
        if (ec) return false;
    }

    std::vector<fs::path> chunk_paths;
    chunk_paths.reserve(static_cast<size_t>(chunks));
    for (int i = 0; i < chunks; ++i) {
        fs::path chunk = task.dest;
        chunk += ".part" + std::to_string(i);
        chunk_paths.push_back(chunk);
    }
    fs::path assembled = task.dest;
    assembled += ".part";

    auto cleanup_parts = [&]() {
        std::error_code ignore;
        for (auto& path : chunk_paths) fs::remove(path, ignore);
        fs::remove(assembled, ignore);
    };
    cleanup_parts();

    std::atomic<int> next_chunk{0};
    std::atomic<int> completed{0};
    std::atomic<int> failed{0};
    std::atomic<bool> cancelled{false};
    std::atomic<long long> aggregate{0};
    std::mutex report_mutex;
    std::function<void(long long, long long)> report = [&](long long done, long long total_bytes) {
        if (!byte_progress) return;
        std::lock_guard<std::mutex> lock(report_mutex);
        byte_progress(done, total_bytes);
    };

    auto worker = [&]() {
        while (true) {
            if (cancelled.load()) break;
            const int index = next_chunk.fetch_add(1);
            if (index >= chunks) break;

            const long long start = total * index / chunks;
            const long long end = total * (index + 1) / chunks - 1;
            if (end < start) {
                completed.fetch_add(1);
                continue;
            }
            const long long expected = end - start + 1;
            const fs::path chunk = chunk_paths[static_cast<size_t>(index)];
            bool ok = false;

            for (int attempt = 0; attempt <= kDownloadRetries && !cancelled.load(); ++attempt) {
                CURL* c = curl_easy_init();
                if (!c) break;
                std::ofstream out(chunk, std::ios::binary | std::ios::trunc);
                if (!out) {
                    curl_easy_cleanup(c);
                    break;
                }

                apply_common_options(c);
                const std::string range = std::to_string(start) + "-" + std::to_string(end);
                RangeWriteContext ctx;
                ctx.file = &out;
                ctx.cancelled = &cancelled;
                ctx.aggregate = &aggregate;
                ctx.total = total;
                ctx.report = &report;
                ctx.last_report = std::chrono::steady_clock::now();

                curl_easy_setopt(c, CURLOPT_URL, task.url.c_str());
                curl_easy_setopt(c, CURLOPT_RANGE, range.c_str());
                curl_easy_setopt(c, CURLOPT_MAXFILESIZE_LARGE, static_cast<curl_off_t>(expected));
                curl_easy_setopt(c, CURLOPT_HEADERFUNCTION, range_header_cb);
                curl_easy_setopt(c, CURLOPT_HEADERDATA, &ctx);
                curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, range_write_cb);
                curl_easy_setopt(c, CURLOPT_WRITEDATA, &ctx);

                const CURLcode rc = curl_easy_perform(c);
                long code = 0;
                curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
                curl_easy_cleanup(c);
                out.close();

                const bool size_ok = file_size_or_negative(chunk) == expected;
                if (rc == CURLE_OK && code == 206 && !ctx.io_error && ctx.bytes == expected && size_ok) {
                    ok = true;
                    break;
                }

                // 200 means the server ignored Range; 416 means the range was
                // rejected.  In both cases the whole-file fallback is safer.
                if (code == 200 || code == 416) cancelled.store(true);
                std::error_code ignore;
                fs::remove(chunk, ignore);
                if (attempt < kDownloadRetries && !cancelled.load()) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(400 << std::min(attempt, 3)));
                }
            }

            if (ok) {
                completed.fetch_add(1);
            } else {
                failed.fetch_add(1);
                cancelled.store(true);
            }
        }
    };

    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(workers));
    for (int i = 0; i < workers; ++i) pool.emplace_back(worker);
    for (auto& thread : pool) thread.join();

    if (failed.load() > 0 || cancelled.load() || completed.load() != chunks) {
        cleanup_parts();
        const bool ok = download_single(task);
        if (ok && byte_progress) report(total, total);
        return ok;
    }

    bool assembled_ok = true;
    {
        std::ofstream out(assembled, std::ios::binary | std::ios::trunc);
        if (!out) {
            assembled_ok = false;
        } else {
            for (auto& chunk : chunk_paths) {
                std::ifstream in(chunk, std::ios::binary);
                if (!in) {
                    assembled_ok = false;
                    break;
                }
                out << in.rdbuf();
                if (!out.good()) {
                    assembled_ok = false;
                    break;
                }
            }
        }
    }

    if (!assembled_ok || !verify_download_file(assembled, task)) {
        cleanup_parts();
        const bool ok = download_single(task);
        if (ok && byte_progress) report(total, total);
        return ok;
    }

    fs::remove(task.dest, ec);
    fs::rename(assembled, task.dest, ec);
    if (ec) {
        cleanup_parts();
        return false;
    }

    cleanup_parts();
    report(total, total);
    return true;
}
"""

# 4. version.hpp
files["version.hpp"] = r"""#pragma once
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
"""

# 5. natives.hpp (用系统 tar 解压，绕过 miniz 依赖，非常轻量)
files["natives.hpp"] = r"""#pragma once
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
"""

# 6. jvm_opts.hpp
files["jvm_opts.hpp"] = r"""#pragma once
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
"""

# 7. launch.hpp
files["launch.hpp"] = r"""#pragma once
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
        {"${launcher_name}", "CoreLauncher"},
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
    if (offline) cmd.push_back("-Dminecraft.launcher.brand=CoreLauncher-Offline");
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
"""

# 8. crash.hpp
files["crash.hpp"] = r"""#pragma once
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
"""

# 9. main.cpp
files["main.cpp"] = r"""#include <iostream>
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
"""

# 将内容写入文件
files["install.hpp"] = r"""#pragma once
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
"""

for filename, content in files.items():
    filepath = os.path.join(src_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")

print(f"Success: Generated {len(files)} C++ files in {src_dir}")
