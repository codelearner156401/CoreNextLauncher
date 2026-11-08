# 免责声明 / Disclaimer

**本项目与 Mojang / Microsoft 无任何关联；不提供任何盗版游戏文件；开发者不承担任何因使用本软件导致的账号封禁或法律风险。**

CoreNextLauncher 是一个独立的、非官方的 Minecraft 第三方启动器。项目名称、代码与文档均不代表 Mojang Studios、Microsoft Corporation 或其关联公司的立场，也未获得其授权、认可或赞助。

## 1. 不提供游戏本体

本启动器不附带、不分发、不托管任何 Minecraft 游戏本体、客户端 JAR、资源文件或账号凭证。所有游戏文件均由用户本机上的启动器通过 Mojang 官方公开接口（`piston-meta.mojang.com` 与 Mojang 官方资源 CDN）下载，或由用户自行提供。

使用本软件下载、安装和运行 Minecraft 时，仍需遵守 Minecraft 最终用户许可协议（EULA）以及 Microsoft 服务协议。用户应通过合法渠道购买并拥有相应的 Minecraft 账号。

## 2. 账号与登录

- 联机与正版服务器游玩需要用户自行完成 Microsoft / Xbox Live 正版登录。
- 启动器仅在用户本机保存登录令牌，并使用 Windows DPAPI 进行保护；项目不会把凭证上传到任何自建服务器。
- 请勿在公共电脑、共享账号或不可信环境中保存登录信息。

## 3. 离线模式（仅限开发调试）

离线模式是开发者用于本地调试、UI 测试和单机环境排障的功能，默认隐藏，需要手动解锁开发者选项并二次确认后才能启用。开发者模式与离线模式仅在本次运行中有效，启动器每次启动和退出时都会强制重置为关闭；相关状态保存在独立文件 `developer_state.json`，不写入主配置文件。

**启用离线模式可能违反 Minecraft EULA。** 该功能不得用于盗版联机、绕过正版验证、商业分发或其他侵权用途。是否启用、以及由此产生的账号风险、法律风险和 EULA 风险，均由用户自行承担。开发者不对任何因启用离线模式导致的后果负责。

## 4. 第三方内容

通过本启动器安装的 Mod、整合包、资源包、光影、服务器列表等内容可能来自第三方。其版权、许可、安全性和兼容性由相应作者与用户负责。请在安装前确认来源可信，并自行备份存档。

## 5. 无担保

本软件按“现状”提供，不附带任何明示或暗示的担保，包括但不限于适销性、特定用途适用性和不侵权担保。开发者不对数据丢失、存档损坏、账号封禁、计算机损坏、服务中断或任何直接/间接损失承担责任。

## 6. 官方资源与商标

Minecraft、Mojang、Microsoft、Xbox 及相关名称、标识和商标归其各自权利人所有。本项目仅在描述兼容性时提及这些名称，不构成任何形式的授权或关联声明。

---

**English summary:** This is an unofficial, independent project and is not affiliated with, endorsed by, or sponsored by Mojang or Microsoft. It does not provide or distribute pirated game files. The developers are not liable for account bans, legal risks, data loss, or other damages arising from use of this software. Offline mode is a hidden developer/debug feature; enabling it is the user's own responsibility and may violate the Minecraft EULA. All game files must be obtained through legitimate channels and used in accordance with the Minecraft EULA.
