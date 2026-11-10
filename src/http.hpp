#pragma once
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
    curl_easy_setopt(c, CURLOPT_USERAGENT, "CoreNextLauncher/1.8.1");
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
