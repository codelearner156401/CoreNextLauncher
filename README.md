# CoreNextLauncher

**现代化、原生体验的 Minecraft 第三方启动器**：C++ 内核负责版本扫描、官方源安装、文件校验与游戏进程启动，PySide6 负责界面、账号与百宝箱。

## 项目说明
CoreNextLauncher 的全部源代码由 DeepSeek 大模型编写。
作者负责定义完整产品需求、设计功能逻辑、进行功能测试、修复程序Bug、打包构建、发布版本以及后续项目维护。
项目完全开源免费，无任何广告，欢迎体验、提交Issue反馈问题。

> 本项目为非官方项目，与 Mojang Studios / Microsoft 无任何关联。
> **不提供、不分发、不托管任何 Minecraft 游戏本体、客户端 JAR、资源文件或破解内容。**
> 使用前请阅读 [免责声明](DISCLAIMER.md)。

`版本 1.9.9` · `Windows 10/11` · `MIT License`

第三方组件许可见 THIRD_PARTY_NOTICES.md 与 licenses/ 目录。

---

## 目录

- [项目介绍](#项目介绍)
- [功能特性](#功能特性)
- [界面截图](#界面截图)
- [构建教程](#构建教程)
- [使用教程](#使用教程)
- [开发者模式与离线模式风险声明](#开发者模式与离线模式风险声明)
- [已知限制](#已知限制)
- [编译依赖](#编译依赖)
- [项目结构](#项目结构)
- [贡献指南](#贡献指南)
- [开源协议](#开源协议)
- [免责声明](#免责声明)

---

## 项目介绍

CoreNextLauncher 是一个面向 Windows 的 Minecraft 启动器，采用「C++ 内核 + Python 界面」的双层架构：

- **C++ 内核（`mc_core.exe`）**：扫描版本目录、解析版本继承、从 Mojang 官方源安装游戏、并发下载并校验文件、解压 natives、构建 JVM 命令并启动游戏。内核通过标准输入输出上的逐行 JSON 协议与界面通信，可独立于界面进行调试。
- **Python 界面（PySide6）**：负责窗口、动效、液态玻璃主题、Microsoft 正版登录、下载进度展示，以及「百宝箱」中的 Mod 管理、崩溃诊断、整合包导入、服务器监控、截图管理、Java 托管、一键优化、AI 诊断与在线更新。

项目只访问 Mojang 官方源安装游戏，不使用任何第三方镜像；所有下载文件在落盘前校验大小与哈希。启动器自身不携带任何游戏文件。

## 功能特性

### v1.8.1 背景增强

- 无边框窗口同时使用 Windows 11 `DWMWA_SYSTEMBACKDROP_TYPE`（Acrylic）与
  Windows 10 `SetWindowCompositionAttribute` 两套系统级模糊实现，失败时自动
  回退为实色面板。
- 窗口背景、面板与标题区域改为真正的半透明叠加，能透出桌面壁纸与下层窗口的
  光线，而不只是模糊启动器自己的背景图。
- 降低背景底色与面板不透明度，保留文字可读性的同时增强“透过桌面的光”的毛玻璃
  观感。

### v1.9.0 皮肤 / 隔离 / Mod 与版本浏览

- **皮肤管理**：启动页内嵌皮肤子板块，支持本地 PNG 导入、Steve/Alex 自动检测、
  3D 旋转预览、手动模型切换、删除，以及从当前正版账号拉取 Mojang 皮肤。
- **版本隔离**：实例详情页可为指定版本开启/关闭隔离；开启后该版本使用独立的
  `mods / config / saves / resourcepacks`，关闭时合并回实例公共目录。
- **Mod 详情与预检测**：Mod 列表新增作者、简介详情；启动前自动预扫描缺失前置、
  重复 Mod ID、同名 jar、被禁用前置，并弹窗给出清单，可选择继续或取消启动。
- **PCL2 风格版本浏览**：圆角卡片、正式版/快照/远古版标签、发布日期、推荐标识、
  分类切换、搜索与安装选中版本。
- **蓝色线条 C 启动动画**：旋转 + 落地弹跳循环，默认开启，随全局动画速度变化。

### v1.9.1 页面栈滑动交互

- 浏览官方版本、皮肤管理、Mod 详情改为主窗口内子页面，不再打开独立弹窗。
- 页面栈支持返回按钮，点击后从左侧滑回上一页；顶部标签切换会自动清理子页面。
- 滑动动画复用全局动画速度，范围 0.2x–2.5x，三套主题一致。
- 版本扫描、Mod 文件读取、官方版本清单加载均显示蓝色线条 C 加载动画，
  本地与在线文件统一使用。

### v1.9.2 全局页面视窗与细线加载动画

- 移除独立的模态弹窗窗口：消息框、文本输入、文件/目录选择、取色器以及账号、
  整合包、崩溃诊断、更新、实例克隆/隔离等对话框全部嵌入主窗口页面栈。
- 子页面左上角「← 返回」滑动返回上一级；页面切换复用全局动画速度 0.2x–2.5x，
  三套主题一致。
- 启动加载动画改为小尺寸、单根、约 2px 的蓝色细线 C，旋转 + 落地弹跳，
  不使用粗描边或填充色块；默认开启。

### v1.9.3 联机、调试与加载动画

- **陶瓦联机**：新增独立「联机」标签，支持创建房间、公开房间列表、房间号+密码加入、
  版本一致性校验、端口检测与联机日志；与版本隔离目录兼容。
- **开发者调试面板**：仅开发者模式可见；支持一键模拟全部异常、单项模拟、自动恢复、
  重置状态、导出完整日志、动画预览和登录缓存过期测试。
- **30 天缓存登录提示**：断网使用缓存凭证时显示页面内提示；该能力在开发者选项中默认关闭，需手动开启。
- **加载动画**：极细蓝色线条 C，持续旋转；弹跳使用重力抛物线，
  上升减速、下落加速，触底反弹；默认开启并沿用 0.2x–2.5x 速度限制。
- **配置容错**：配置写入时自动保留 `.bak`，主配置损坏时回退备份。
- **皮肤增强**：滚轮缩放预览、PNG 导出；皮肤管理入口移至开发者选项内（调试用途）。
- **Mod 报告**：冲突检测结果支持一键复制为文本报告。

### v1.9.4 页面布局与加载动画

- **下载页三栏**：官方版本列表、下载安装配置、版本辅助工具三个独立滚动板块；
  版本列表完整浏览，辅助工具支持校验、补全版本文件、打开目录。
- **GitHub 更新**：默认绑定 `Codelearner156401/CoreNextLauncher`，设置页可自定义仓库。
- **背景图路径**：所有磁盘绝对路径均可读取，选择后即时预览并应用。
- **皮肤 3D**：加入背面剔除、基础层/外层合并深度排序，修复穿模与色块错乱。
- **全局滚动**：百宝箱改为页面级滚动，设置、启动、下载等溢出区域统一可滚动。
- **固定细线 C 动画**：SVG 矢量 C 环固定尺寸/缺口/线宽，3 秒旋转 + 重力弹跳，
  地面横线独立静止；使用 QParallelAnimationGroup 原生动画，覆盖启动、下载、
  版本清单、Mod 扫描与游戏启动。
- **启动过渡**：下载/启动阶段显示统一加载动画，启动事件到达后柔和退场。

### 账号与启动

- **Microsoft 正版登录**：设备码授权流程，令牌自动刷新，使用 Windows DPAPI 加密保存。
- **官方源安装**：从 `piston-meta.mojang.com` 与 Mojang 官方资源 CDN 获取版本清单、客户端 JAR、依赖库、natives 与 assets。
- **高并发下载**：依赖库与 assets 使用 16–32 个工作线程；客户端 JAR 使用 HTTP Range 分块并发；失败自动重试，写入前严格校验 SHA1。
- **版本管理**：自动扫描 `versions/`，支持 `inheritsFrom` 继承版本、旧版 `classifiers` 与 1.19+ 独立 native artifact。
- **Java 适配**：支持选择 JDK 根目录、`bin` 目录或 `java.exe`，自动补全 `javaw.exe`；按版本声明的 `javaVersion` 自动切换同目录下的运行时；启动前自动收敛非法的 `-Xms`/`-Xmx`。
- **Java 自动托管**：检测到 Java 缺失时，可从 Adoptium 官方 API 下载 Temurin JDK（SHA-256 校验），解压后自动填入设置。
- **Java 自动扫描**：读取系统注册表、常见安装目录、`PATH`、Minecraft `runtime/` 与启动器托管目录，探测版本后填入设置页下拉框。

### 实例与性能

- **实例管理**：在程序目录工作区的 `instances/` 下创建独立游戏目录；支持一键克隆（复制版本、Mod 与配置，可选是否复制存档），切换实例互不影响。
- **Mod 批量管理**：Mod 列表支持 Ctrl / Shift 多选后批量启用或禁用；冲突检测覆盖重复 Mod ID、同名 jar、缺失前置，以及“前置已安装但被禁用”。
- **内存推荐**：按物理内存自动推荐 Xmx / Xms，并为系统、显卡与其他程序预留空间。
- **JVM 可视化编辑器**：G1GC / ZGC / 预触碰 / 并行引用 / 强制 UTF-8 开关，支持预设、自定义参数与“按硬件推荐”；参数随启动命令传给 C++ 内核。
- **Java 自动探测**：扫描注册表、安装目录、`PATH`、Minecraft `runtime/` 与启动器托管的 Java；已配置路径失效时自动切换到最佳运行时。
- **崩溃自动诊断**：启动失败后自动读取最新崩溃报告，识别常见错误并以弹窗给出原因与修复步骤。

### 百宝箱

- **目录与备份**：一键打开 `.minecraft` / `mods` / `logs` / `saves` / `screenshots`；清理旧日志、崩溃报告与调试日志；手动勾选存档打包备份。
- **启动前自动备份**：每次启动前静默、增量备份 `saves/`，只有存档变化时才重新压缩，并按份数滚动清理旧备份。
- **可视化 Mod 管理**：列出所有 Mod（含已禁用），显示名称、版本、Mod ID、加载器与大小；勾选即可通过重命名 `.jar.disabled` 启用/禁用；支持多选批量开关、重复 Mod ID、同名 jar、缺失前置与前置被禁用检测。
- **Modrinth 更新检测**：对 Mod 计算 SHA1 并查询 Modrinth，按游戏版本与加载器筛选可用更新，支持批量下载；旧文件保留为 `.old`。
- **智能崩溃分析**：读取最新 `crash-reports`（或 `latest.log`、`hs_err_pid*.log`），用规则匹配内存不足、Java 版本不符、前置缺失、Mixin 冲突、显卡驱动、文件损坏、权限、端口占用、登录失效等常见问题，以弹窗形式给出通俗的原因说明与修复步骤，并列出可疑 Mod。
- **整合包导入**：支持拖拽 `.mrpack` 到窗口或选择文件，解析 `modrinth.index.json`、下载文件、解压 overrides，并可自动安装对应 Fabric 加载器。
- **服务器监控**：内置 Minecraft Server List Ping 协议，展示在线状态、延迟、人数、版本与 MOTD；一键带 `--server/--port` 参数进服。
- **截图管理**：缩略图网格预览 `screenshots/`，支持大图查看、上一张/下一张、删除与用系统看图打开。
- **一键智能优化**：安装 Fabric 加载器与依赖库，并从 Modrinth 安装 Sodium、Iris 与 Fabric API。
- **AI 诊断**：填写 DeepSeek API Key 后，将日志、崩溃报告、Mod 列表与 Java/内存设置发送给 DeepSeek，返回结构化的原因分析与修复步骤。Key 使用 DPAPI 加密保存在本机。
- **在线更新**：配置 GitHub 仓库后，通过 Releases API 比对版本，下载新 exe 并由 `.bat` 脚本在程序退出后替换、重启。

### 界面与体验（v1.8）

- **三套界面风格**：液态玻璃 / 毛玻璃 / HTML 界面；每套风格独立保存配色、毛玻璃开关、模糊强度、背景图与启动图，切换即时生效、无需重启。
- **PCL 风格滑动切换**：页面切换改为水平滑入，移除全部淡入淡出；全局动画速度支持 0.2x–2.5x 调节（设置 → 开发者选项）。
- **无边框毛玻璃窗口**：移除系统标题栏，内置最小化 / 最大化 / 关闭按钮；可通过 Windows Acrylic 看到桌面与下层窗口，并叠加毛玻璃模糊。
- **10 套预设配色 + 自定义取色器**：白色、黑色、蓝、绿、橙、粉、紫、青、黄、红；主色调与辅助色可自由取色。
- **自定义启动画面**：可选择本地图片作为启动图，支持预览与恢复默认；未设置时显示旋转 C 图标 + 落地弹跳加载动画，动画可关闭。
- **PCL 风格启动页**：新增独立「启动」页，展示当前版本、账号、Java 与内存，并提供大号启动按钮；「设置」移动到导航末尾。
- **可浏览的下载页**：直接浏览 Mojang 官方版本清单（正式版 / 快照 / 旧版），支持搜索并安装选中版本。
- **30 天本地登录凭证**：联网时优先校验与刷新正版登录；断网且缓存未超过 30 天时使用本地凭证，凭证关闭程序后仍保留。
- 高 DPI 适配、底部固定启动栏、悬停放大/按压回弹与发光阴影均保留。

## 界面截图

> 以下位置已预留给界面截图。图片放入 `docs/screenshots/` 后，把「截图待补充」
> 替换为对应的 Markdown 图片即可（文件名建议见
> [docs/screenshots/README.md](docs/screenshots/README.md)）。

| 主界面（暗黑） | 主界面（明亮） |
| :---: | :---: |
| 截图待补充 | 截图待补充 |

| 百宝箱 · Mod 管理 | 百宝箱 · 崩溃诊断 |
| :---: | :---: |
| 截图待补充 | 截图待补充 |

| 百宝箱 · 服务器监控 | 百宝箱 · 一键优化 |
| :---: | :---: |
| 截图待补充 | 截图待补充 |

| 实例管理 | JVM 参数编辑器 |
| :---: | :---: |
| 截图待补充 | 截图待补充 |

截图请使用示例游戏目录，避免暴露个人路径、账号或 API Key。

## 构建教程

### 环境要求

| 组件 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（64 位） |
| Python | 3.11 及以上 |
| PySide6 | 6.x |
| PyInstaller | 6.x（仅打包时需要） |
| MinGW-w64 | **i686（32 位）**，GCC 13+，含 `mingw32-make` |
| CMake | 3.20 及以上 |
| OpenSSL | 3.x 静态库 |
| libcurl | 8.11.x 静态库（仓库附带源码包） |
| nlohmann/json | v3.11.3（仓库内 `third_party/json.hpp`） |

> 32 位内核不影响运行 64 位游戏：内核通过 `CreateProcessW` 启动系统上的 Java。
>
> 下面的构建命令使用 `$mingw` 与 `$root` 两个变量。请先设置 `MINGW_ROOT`
> 环境变量，或把 `$mingw = $env:MINGW_ROOT` 直接改成你的工具链路径。

### 1. 获取源码

```powershell
git clone https://github.com/Codelearner156401/CoreNextLauncher.git
cd CoreNextLauncher
```

### 2. 安装 Python 依赖

```powershell
python -m pip install --upgrade pip
python -m pip install PySide6 pyinstaller
```

### 3. 编译 libcurl 静态库

仓库附带了 curl 8.11.0 源码包。先设置两个变量（按实际情况修改）：

```powershell
$mingw = $env:MINGW_ROOT      # MinGW-w64 32 位工具链根目录
$root  = (Get-Location).Path  # 当前仓库根目录
```

解压并编译安装到 `third_party/curl-install`（该目录不会进入版本库）：

```powershell
tar -xzf third_party/curl-8.11.0.tar.gz -C third_party

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

### 4. 编译 C++ 内核

```powershell
cmake -S . -B build -G "MinGW Makefiles" `
  -DCMAKE_C_COMPILER="$mingw/bin/gcc.exe" `
  -DCMAKE_CXX_COMPILER="$mingw/bin/g++.exe" `
  -DCMAKE_MAKE_PROGRAM="$mingw/bin/mingw32-make.exe" `
  -DCMAKE_BUILD_TYPE=Release `
  -DOPENSSL_ROOT_DIR="$mingw/opt"

cmake --build build --parallel
```

若 OpenSSL 不在 `$mingw/opt`，把 `-DOPENSSL_ROOT_DIR` 指向实际安装位置。
产物为 `build/mc_core.exe`，静态链接，仅依赖 Windows 系统 DLL。

更详细的依赖说明与常见问题见 [BUILD.md](BUILD.md)。

### 5. 运行源码版

```powershell
python main.py
# 指定游戏目录
python main.py --game-dir "<.minecraft 目录>"
# 指定内核路径
python main.py --exe "build/mc_core.exe"
```

首次启动会在**程序同目录**创建 `CoreNextLauncher` 工作区（设置环境变量
`CORENEXTLAUNCHER_HOME` 可覆盖位置）：

```text
CoreNextLauncher.exe
CoreNextLauncher/
├─ config/       launcher_config.json、账号与密钥
├─ kernel/       mc_core.exe 与托管的 Java 运行时
├─ instances/    各个游戏实例（default 为初始实例）
├─ skins/        本地导入 / 拉取的正版皮肤
├─ cache/        下载缓存、服务器图标、更新包
├─ logs/         启动器日志目录
└─ exports/      导出的整合包
```

C++ 内核、配置、Minecraft 下载资源与实例全部位于工作区，不再默认写入 C 盘
用户目录；`developer_state.json` 位于 `config/`，开发者/离线开关仍在每次
启动与退出时强制重置为关闭。

从 v1.7 升级时，账号、主题、Java 与内存设置会迁移，但旧的外部 `.minecraft`
游戏目录会被自动切换到工作区的 `instances/default`；如需继续使用旧目录，可在
「设置 → 游戏目录」中手动选择。

### 6. 打包 exe

确认 `build/mc_core.exe` 已更新，然后：

```powershell
python -m PyInstaller CoreNextLauncher.spec --noconfirm --clean --workpath pyinstaller-build
```

输出：`dist/CoreNextLauncher.exe`。运行后会在 exe 同目录创建
`CoreNextLauncher/` 工作区保存配置、实例、内核与 Java 运行时。

### 7. 自测

```powershell
python tests/selftest.py
```

自测覆盖配置兼容、工作区目录、实例克隆、JVM 推荐、崩溃规则、Mod 批量开关与
冲突、存档备份、截图索引、`.mrpack` 解析、服务器 Ping 协议、更新脚本、Java
扫描，以及在 offscreen 平台构建主界面、实例页与百宝箱（不会弹出窗口）。

## 使用教程

### 首次启动

1. 启动器会在**程序同目录**创建 `CoreNextLauncher` 工作区，并自动创建
   `default` 实例、部署 C++ 内核。
2. 打开「设置 → 游戏与 Java」，确认实例目录（默认已在工作区的
   `instances/default`）。
3. Java 路径下拉框会自动填入本机扫描到的运行时；若没有合适的 Java，可在
   「百宝箱 → 优化」中一键下载安装 Temurin。
4. 点击右上角「未登录」，完成 Microsoft 正版登录（推荐且默认的启动方式）。

### 实例管理

1. 打开「实例」页，可新建实例、克隆选中实例、设为当前实例、打开目录或删除。
2. 克隆会复制版本、Mod 与配置；默认包含 `saves` 存档，可取消勾选以加快速度。
3. 「设为当前实例」会同步「设置 → 游戏目录」，版本库随后自动刷新。

### 性能与 JVM

- 「设置 → 游戏与 Java」中的「一键智能内存优化」会按物理内存推荐 Xmx / Xms。
- 「设置 → JVM 参数 · 可视化」可选择预设、勾选 G1GC / ZGC / 预触碰等参数，
  也可以追加自定义参数或点击「按硬件推荐」。
- 参数会随启动命令传给内核；留空表示使用内核默认的 G1GC。ZGC 需要 Java 17+。

### 安装与启动游戏

1. 切换到「下载 / 安装」，输入 Mojang 官方版本号（如 `1.20.1`、`latest-release`）。
2. 点击「开始下载」，等待进度完成；已存在的文件会自动跳过。
3. 回到左侧「版本库」选择版本，点击底部「▶ 启动游戏」。

### 百宝箱

| 标签页 | 用途 |
| --- | --- |
| 目录与备份 | 打开常用目录、清理旧日志、手动备份存档 |
| Mod 管理 | 批量/单个启用或禁用 Mod、检测重复 ID/同名 jar/缺失前置、检查 Modrinth 更新 |
| 崩溃诊断 | 分析崩溃报告并给出修复建议；可调用 AI 深入分析 |
| 整合包 | 导入 Modrinth `.mrpack` 整合包 |
| 服务器 | 添加服务器、Ping 监控、一键进服 |
| 截图 | 预览、翻阅、删除游戏截图 |
| 优化 | 扫描/安装 Java、一键安装 Fabric + Sodium + Iris |

### 整合包与服务器

- **整合包**：把 `.mrpack` 拖到窗口任意位置，确认信息后点击「开始导入」。
  导入会把文件写入当前游戏目录，并可自动安装对应 Fabric 加载器。
- **服务器**：在「百宝箱 → 服务器」添加地址（支持 `域名:端口`），刷新状态后
  双击条目或点击「一键进服」即可带服务器参数启动游戏。

### 自动备份与在线更新

- 「设置 → 自动化」可开关启动前静默备份，并设置每个存档保留的份数。
- 同一处可填写 GitHub 仓库（`owner/repo`）以启用在线更新；Release 中需要
  附带一个 `.exe` 文件。启动器会比对版本，下载后由 `.bat` 脚本在退出后替换
  并重启。

### AI 诊断

1. 在「设置 → AI 诊断」填入 DeepSeek API Key 并保存。
2. 打开「百宝箱 → 崩溃诊断」，点击「用 AI 分析」。
3. 启动器会把日志片段、崩溃报告、Mod 列表与 Java/内存设置发送到
   `api.deepseek.com`；日志中可能包含本机路径，请自行判断是否使用。

## 开发者模式与离线模式风险声明

> **普通玩家不需要开启离线模式。默认且唯一推荐的启动方式是 Microsoft 正版登录。**
>
> 本节说明的是隐藏的开发者调试入口。它不是为了方便普通用户绕过登录而设计，
> 请勿将其理解为使用建议。本文只做风险说明，不提供开启步骤，也不建议普通
> 用户尝试。

### 入口为什么是隐藏的

「设置」页底部的版本号只是版本标识。只有连续点击 7 次才会显示「开发者选项」，
这是为了避免误触和误用。离线模式开关位于开发者选项内，默认关闭；开启时会
弹出 EULA 风险确认，必须再次确认才会生效。

开发者模式与离线模式**只在本次运行中有效**：状态保存在独立的
`developer_state.json`（不写入 `launcher_config.json`），启动器每次启动时
都会强制重置为关闭，正常退出时也会再次清零。即使上次异常退出或手动修改了
状态文件，重启后也不会自动恢复。

### 离线模式是什么

离线模式是开发者用于**本地技术测试**的调试功能，例如：

- 在没有网络或没有正版账号的开发环境中调试界面与启动流程；
- 单机环境下的故障排查与回归测试；
- 启动器自身的自动化测试。

它只跳过正版登录，使用本地昵称启动游戏；不提供正版联机能力，也不会绕过
任何服务器的正版验证。使用离线身份进入服务器还可能违反该服务器的规则。

### 明确风险（请完整阅读）

- **离线模式不符合 Minecraft 最终用户许可协议（EULA）。**
- **严禁**将离线模式用于：盗版联机、绕过正版验证、商业用途、内容分发、
  公开服务器或其他任何**非测试用途**。
- 启用离线模式产生的账号风险、法律风险与安全风险，均由使用者自行承担。
- 启动器不会因为开启离线模式而获得任何游戏文件；缺失的游戏文件仍需通过
  合法渠道获取。

### 项目不会做的事

- 不分发、不托管、不内置任何 Minecraft 客户端 JAR、资源文件、账号凭证或
  破解补丁。
- 不提供绕过正版验证的服务器、联机方案或启动参数。
- 仓库中不包含任何游戏本体文件；`.gitignore` 明确排除了 `.minecraft/`、
  `*.jar`、`libraries/`、`versions/`、`assets/objects/` 等游戏数据。

### 正确的使用方式

1. 使用 Microsoft 账号完成正版登录。
2. 通过「下载 / 安装」从 Mojang 官方源安装游戏。
3. 如果你只是想正常游玩，**请不要解锁开发者选项，也不要开启离线模式**。

完整法律声明见 [DISCLAIMER.md](DISCLAIMER.md)。

## 已知限制

- 仅面向 Windows；C++ 代码包含少量跨平台分支，但未在其他平台完整验证。
- 系统级 Acrylic 背景模糊依赖 Windows 10/11 的 DWM 实现；远程桌面、节能模式或部分显卡驱动下可能退化为普通半透明面板。
- 无边框窗口的缩放使用右下角尺寸手柄；拖动标题栏可移动窗口，双击可最大化/还原。
- 内核为 32 位进程；可启动 64 位 Java，但不支持在 32 位系统上运行 64 位 Java。
- 整合包导入面向 Modrinth `.mrpack`：自动安装 Fabric；Quilt / Forge / NeoForge
  需要用户自行安装加载器。
- 实例克隆会复制整个实例（默认包含存档），大型整合包可能需要较长时间与额外磁盘空间。
- 一键优化会新增 `fabric-loader-…` 版本，需要在版本库中手动选择该版本启动，
  不会改动原版启动方式。
- JVM 参数为高级选项：ZGC 需要 Java 17+，自定义参数不会做版本兼容校验，错误参数可能导致游戏无法启动。
- 服务器 Ping 使用默认端口或显式端口，不解析 SRV 记录。
- 在线更新只替换 `CoreNextLauncher.exe` 本体，不支持增量更新或回滚；源码模式下
  只提示，不替换文件。
- AI 诊断需要联网并消耗 DeepSeek API 配额；未配置 Key 时该功能不可用。
- 暂不支持 Mod 包版本锁定与云端同步。

## 编译依赖

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [Qt / PySide6](https://www.qt.io/qt-for-python) | 桌面界面 | LGPLv3 / GPLv3 |
| [libcurl](https://curl.se/) | HTTP 下载（Schannel TLS） | curl License |
| [OpenSSL](https://www.openssl.org/) | 静态加密库 | Apache-2.0 |
| [nlohmann/json](https://github.com/nlohmann/json) | JSON 解析 | MIT |
| [PyInstaller](https://pyinstaller.org/) | 单文件打包 | GPLv2（含例外） |
| MinGW-w64 / GCC | C++ 编译 | GPL（运行时例外） |

> 分发打包后的程序时，请遵守 Qt / PySide6 的 LGPLv3 条款，并保留上述项目的
> 版权与许可声明。仓库内 `third_party/json.hpp` 与 curl 源码包均保留原始许可。

## 项目结构

```text
CoreNextLauncher/
├─ src/                       C++ 内核源码
│  ├─ main.cpp                JSON 管道入口（scan / install / launch）
│  ├─ http.hpp                并发下载、Range、重试、校验
│  ├─ install.hpp             官方源安装流程
│  ├─ launch.hpp              启动命令构建与 CreateProcess
│  ├─ jvm_opts.hpp            内存与 JVM 参数
│  ├─ natives.hpp             本地库识别与解压
│  ├─ version.hpp             版本继承与规则匹配
│  ├─ crash.hpp               崩溃日志规则
│  └─ common.hpp              事件输出与进度节流
├─ launcher/                  Python / PySide6 界面
│  ├─ services/               百宝箱服务层（无 Qt 依赖）
│  │  ├─ instances.py         实例创建 / 克隆 / 删除
│  │  └─ jvm.py               内存推荐与 JVM 预设
│  ├─ ui/instance_page.py     实例管理页
│  ├─ ui/toolbox/             百宝箱各标签页
│  ├─ resources/themes/       暗黑 / 明亮 QSS 基础主题
│  ├─ paths.py                程序目录工作区与目录布局
│  ├─ theme.py                三套界面风格 / 配色 / 系统模糊
│  ├─ anim.py                 全局动画速度
│  ├─ ui/start_page.py        PCL 风格启动页（含皮肤子板块）
│  ├─ ui/skin_panel.py        皮肤管理 / 3D 预览
│  ├─ ui/version_card.py      PCL2 圆角版本卡片
│  ├─ ui/splash.py            蓝色线条 C 启动动画 / 自定义启动图
│  ├─ services/skins.py       皮肤库、Steve/Alex 检测、Mojang 拉取
│  ├─ services/isolation.py   版本隔离与资源合并
│  ├─ auth.py                 Microsoft 设备码登录
│  ├─ account.py              DPAPI 加密的账号存储
│  ├─ config.py               启动器配置
│  ├─ core_client.py          C++ 内核 JSON 协议客户端
│  └─ main_window.py          主窗口与启动流程
├─ tests/selftest.py          服务层 + 无头界面自测
├─ docs/screenshots/          界面截图（占位说明）
├─ assets/                    应用图标
├─ third_party/               json.hpp 与 curl 源码包
├─ CMakeLists.txt             C++ 构建配置
├─ CoreNextLauncher.spec      PyInstaller 配置
├─ main.py                    Python 入口
├─ BUILD.md                   C++ 内核构建说明
├─ DISCLAIMER.md              免责声明
├─ CONTRIBUTING.md            贡献指南
└─ LICENSE                    MIT 协议
```

## 贡献指南

欢迎提交 Issue 与 Pull Request。开始之前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，
其中包含开发环境、代码风格、提交规范与项目红线。

简要约定：

- **禁止**提交任何 Minecraft 游戏本体文件、破解补丁、账号凭证或绕过正版验证的代码。
- 修改后请运行 `python tests/selftest.py`，并确保 C++ 内核可正常编译。
- 提交信息建议使用 Conventional Commits（如 `fix: 修正高 DPI 下版本列表错位`）。
- 涉及界面的改动请附截图，但截图需使用示例目录、不得包含个人路径或密钥。

## 开源协议

本项目使用 [MIT License](LICENSE) 开源。

```text
Copyright (c) 2025-2026 CoreNextLauncher contributors
```

你可以自由使用、修改、分发本项目的代码，但需保留版权声明与许可声明。
第三方依赖遵循各自的许可，详见[编译依赖](#编译依赖)。

## 免责声明

本项目与 Mojang Studios、Microsoft Corporation 无任何关联，未获得其授权、
认可或赞助。Minecraft、Mojang、Microsoft、Xbox 及相关名称与标识归其各自
权利人所有。

使用本软件即表示你已阅读并同意 [DISCLAIMER.md](DISCLAIMER.md)。请通过合法
渠道购买并拥有 Minecraft 账号，遵守 Minecraft EULA 与 Microsoft 服务协议。
