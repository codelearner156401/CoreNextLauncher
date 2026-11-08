# mc_core 构建说明

本文说明如何从源码构建 C++ 内核 `mc_core.exe`。先准备两个变量（按实际位置修改）：

```powershell
$mingw = $env:MINGW_ROOT      # MinGW-w64 i686（32 位）工具链根目录
$root  = (Get-Location).Path  # 仓库根目录
```

## 依赖版本

| 依赖 | 版本 / 说明 |
| --- | --- |
| 编译器 | MinGW-w64 **i686（32 位）**，GCC 13 及以上，含 `mingw32-make` |
| CMake | 3.20 及以上，生成器 `MinGW Makefiles` |
| OpenSSL | 3.x 静态库；MinGW-w64 自带时通常位于 `$mingw/opt` |
| libcurl | 8.11.x 静态库，TLS 后端为 Windows Schannel |
| nlohmann/json | v3.11.3 单头文件，已随仓库提供 `third_party/json.hpp` |

32 位内核只是启动器进程本身的选择，它通过 `CreateProcessW` 启动系统上的
64 位 Java，因此不影响运行 64 位游戏。

## 1. 准备 libcurl 静态库

仓库根目录已附带 curl 8.11.0 源码包 `third_party/curl-8.11.0.tar.gz`。
先解压（解压目录不会进入版本库）：

```powershell
tar -xzf third_party/curl-8.11.0.tar.gz -C third_party
```

> Windows 10/11 自带的 `tar` 即可解压；也可以使用 7-Zip 等工具。

然后编译并安装到 `third_party/curl-install`：

```powershell
cmake -S third_party/curl-8.11.0 -B third_party/curl-build -G "MinGW Makefiles" `
  -DCMAKE_C_COMPILER="$mingw/bin/gcc.exe" `
  -DCMAKE_MAKE_PROGRAM="$mingw/bin/mingw32-make.exe" `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_INSTALL_PREFIX="$root/third_party/curl-install" `
  -DBUILD_SHARED_LIBS=OFF -DBUILD_CURL_EXE=OFF -DBUILD_EXAMPLES=OFF `
  -DBUILD_TESTING=OFF -DBUILD_LIBCURL_DOCS=OFF -DBUILD_MISC_DOCS=OFF `
  -DCURL_USE_SCHANNEL=ON -DCURL_USE_OPENSSL=OFF -DCURL_USE_LIBPSL=OFF `
  -DUSE_NGHTTP2=OFF -DCURL_ZLIB=OFF -DCURL_BROTLI=OFF -DCURL_ZSTD=OFF `
  -DCURL_DISABLE_LDAP=ON -DCURL_DISABLE_LDAPS=ON

cmake --build third_party/curl-build --parallel
cmake --install third_party/curl-build
```

`mc_core.exe` 需要静态链接的 `libcurl.a`。如果使用其他 TLS 后端或其他架构，
请自行调整参数，并保证最终 `mc_core.exe` 不带额外 DLL。

## 2. 配置并编译内核

```powershell
cmake -S . -B build -G "MinGW Makefiles" `
  -DCMAKE_C_COMPILER="$mingw/bin/gcc.exe" `
  -DCMAKE_CXX_COMPILER="$mingw/bin/g++.exe" `
  -DCMAKE_MAKE_PROGRAM="$mingw/bin/mingw32-make.exe" `
  -DCMAKE_BUILD_TYPE=Release `
  -DOPENSSL_ROOT_DIR="$mingw/opt"

cmake --build build --parallel
```

如果 OpenSSL 不在 `$mingw/opt`，把 `-DOPENSSL_ROOT_DIR` 指向实际安装位置即可；
也可以使用 vcpkg 等包管理器提供 OpenSSL，但需要保证是 **32 位静态库**。

产物：`build/mc_core.exe`，静态链接，仅依赖 Windows 系统 DLL。

## 3. 运行与自测

首次运行会在用户文档目录创建 `CoreNextLauncher` 工作区，并把
`build/mc_core.exe` 自动部署到 `kernel/`；实例、配置、缓存与日志也统一放在
该工作区中。可用环境变量 `CORENEXTLAUNCHER_HOME` 指定其他位置。

```powershell
# 运行界面（源码模式）
python main.py

# 服务层 + 无头界面自测（使用临时工作区，不会写入文档目录）
python tests/selftest.py
```

## 4. 打包 exe

确认 `build/mc_core.exe` 已更新，然后：

```powershell
python -m PyInstaller CoreNextLauncher.spec --noconfirm --clean --workpath pyinstaller-build
```

输出：`dist/CoreNextLauncher.exe`。`CoreNextLauncher.spec` 会把 `build/mc_core.exe`
和 `launcher/resources` 一起打包。

## 5. 重新生成源码模板（可选）

`setup.py` 是早期用于生成 `src/` 源码模板的引导脚本，模板已与当前版本同步。
日常开发请直接修改 `src/`，`src/` 是唯一权威源码位置；只有需要批量重建模板
时才运行 `setup.py`。

## 6. 第三方许可

- **libcurl** — curl License（MIT/X 派生），见 `third_party/curl-8.11.0/COPYING`。
- **OpenSSL** — Apache License 2.0。
- **nlohmann/json** — MIT License，见 <https://github.com/nlohmann/json>。

分发二进制时请保留以上项目的许可与版权声明。
