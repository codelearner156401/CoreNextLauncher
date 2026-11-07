cmake_minimum_required(VERSION 3.20)
project(mc_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(MC_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/third_party")

# ---------------------------------------------------------------------------
# libcurl: 静态库由 third_party/curl-install 提供（TLS 后端为 Windows Schannel，
# 可直接使用系统证书库）。如需换回 vcpkg，只需删除下面的 list(PREPEND ...) 并
# 用 vcpkg 安装 curl，然后重新配置即可；find_package 会优先使用 CONFIG 包。
# ---------------------------------------------------------------------------
list(PREPEND CMAKE_PREFIX_PATH "${MC_THIRD_PARTY}/curl-install")
find_package(CURL CONFIG REQUIRED)
message(STATUS "CURL: ${CURL_INCLUDE_DIRS}")

# ---------------------------------------------------------------------------
# OpenSSL: 使用静态库。通过 -DOPENSSL_ROOT_DIR=<OpenSSL 根目录> 指定，或把
# OpenSSL 安装到 CMake 能自动找到的位置。MinGW-w64 自带 OpenSSL 时通常位于
# <MinGW 根目录>/opt。
# ---------------------------------------------------------------------------
set(OPENSSL_USE_STATIC_LIBS TRUE)
find_package(OpenSSL REQUIRED)
message(STATUS "OpenSSL: ${OPENSSL_VERSION} (${OPENSSL_INCLUDE_DIR})")

# ---------------------------------------------------------------------------
# nlohmann/json: 单头文件（v3.11.3）已放在 third_party/json.hpp，
# 源码统一 #include "json.hpp" 即可，不依赖 vcpkg 的 nlohmann_json 包。
# ---------------------------------------------------------------------------
if(NOT EXISTS "${MC_THIRD_PARTY}/json.hpp")
    message(FATAL_ERROR "缺少 ${MC_THIRD_PARTY}/json.hpp")
endif()

add_executable(mc_core src/main.cpp)

target_include_directories(mc_core PRIVATE "${MC_THIRD_PARTY}")
target_link_libraries(mc_core PRIVATE CURL::libcurl OpenSSL::SSL OpenSSL::Crypto)

if(WIN32)
    target_link_libraries(mc_core PRIVATE ws2_32 crypt32 bcrypt advapi32)
endif()

# OpenSSL 3's EVP digest path references zlib in its static archive on this
# MinGW toolchain.  Link the bundled static zlib so the final exe stays
# standalone.
if(MINGW)
    target_link_libraries(mc_core PRIVATE z)
endif()

# 生成不依赖 MinGW 运行库 DLL 的独立 exe
if(MINGW)
    target_link_options(mc_core PRIVATE -static -static-libgcc -static-libstdc++)
endif()
