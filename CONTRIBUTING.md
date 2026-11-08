# 贡献指南

感谢你愿意为 CoreNextLauncher 做出贡献。提交代码前，请花几分钟阅读本文。

## 项目红线

以下内容**永远不接受**：

- Minecraft 游戏本体、客户端 JAR、资源文件、libraries、natives 等游戏数据；
- 破解补丁、绕过正版验证的代码、离线登录的联机方案；
- 任何账号凭证、API Key、个人令牌或本机路径；
- 引导普通用户开启隐藏开发者选项或离线模式的文案、截图或教程。

离线模式仅用于本地技术测试，相关说明见 [README](README.md#开发者模式与离线模式风险声明)。

## 行为准则

- 尊重其他贡献者，讨论问题对事不对人。
- 不发布他人的个人信息、日志中的路径或账号信息。
- 不提交来源不明、可能含恶意代码的二进制文件。

## 开发环境

- Windows 10 / 11
- Python 3.11+ 与 PySide6 6.x
- MinGW-w64 i686、CMake 3.20+
- 详细步骤见 [README · 构建教程](README.md#构建教程) 与 [BUILD.md](BUILD.md)

## 提交之前

1. 运行自测，确保全部通过：

   ```powershell
   python tests/selftest.py
   ```

2. 如果修改了 C++ 内核，请重新编译 `build/mc_core.exe`：

   ```powershell
   cmake --build build --parallel
   ```

3. 确认没有把本地文件带进提交：

   ```powershell
   git status
   git diff --cached
   ```

   重点检查：`launcher_config.json`、`account.json`、`secrets.json`、
   `servers.json`、`.minecraft/`、`*.jar`、`build/`、`dist/`。

## 代码风格

### Python

- 4 空格缩进，遵循 PEP 8；文件头使用 `from __future__ import annotations`。
- 公共函数写简短 docstring；界面文案使用简体中文。
- 网络与文件操作放在 `launcher/services/`，不要引入 Qt 依赖。
- 阻塞操作必须放到 `launcher/ui/tasks.py` 的后台任务中，避免冻结界面。

### C++

- C++17，头文件实现为主（`src/*.hpp`）。
- 事件输出统一使用 `event::status / progress / log / done / error`。
- 下载与校验逻辑复用 `http.hpp`，不要新增重复实现。
- 保持 `mc_core.exe` 静态链接，不引入额外 DLL。

### 提交信息

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```text
feat: 新增服务器 SRV 记录解析
fix: 修正高 DPI 下版本列表错位
docs: 补充构建依赖说明
refactor: 拆分百宝箱服务器标签页
test: 补充 mrpack 覆盖写入用例
```

## Pull Request 流程

1. Fork 仓库并从 `main` 创建分支，分支名建议 `feat/xxx`、`fix/xxx`。
2. 完成修改并自测，必要时补充 `tests/selftest.py` 用例。
3. 提交 PR，说明：
   - 改了什么、为什么改；
   - 如何验证（命令、截图或日志）；
   - 是否涉及界面、配置格式或内核协议变更。
4. 等待维护者 Review。涉及界面的 PR 请附截图，但截图必须使用示例目录，
   不得包含个人路径、账号名或 API Key。

## Issue 规范

提交 Bug 时请附上：

- 启动器版本（设置页底部）与 Windows 版本；
- 复现步骤与实际结果；
- 相关日志或崩溃报告（请先删除其中的个人路径与账号信息）；
- 是否使用了 Mod、整合包或自定义 Java。

功能建议请说明使用场景与预期行为，避免提交「求破解 / 求离线联机」类请求。

## 安全漏洞

请勿在公开 Issue 中披露安全漏洞。请通过 GitHub 的 Security Advisory
（仓库 Security 标签页）私下联系维护者，并给出复现步骤与影响范围。

## 许可

提交代码即表示你同意以 [MIT License](LICENSE) 授权你的贡献，并确认你有权
提交这些代码。第三方代码请注明来源与许可。
