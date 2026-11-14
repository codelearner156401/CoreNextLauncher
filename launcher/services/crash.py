"""Read Minecraft crash reports and turn them into plain-language advice.

The rules below cover the failures that actually show up in launcher support
requests: wrong Java version, too little memory, missing mod dependencies,
mixin/loader conflicts, graphics driver trouble, corrupt downloads, permission
problems and expired sessions.  Every match produces a short explanation plus
concrete steps instead of a raw stack trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_READ = 4 * 1024 * 1024
# Share of U+FFFD a UTF-8 decode may leave before we treat the file as another
# encoding: one or two corrupted bytes in a long log stay far below 0.5%, while
# a GBK file turns a whole run of Chinese characters into replacement noise.
UTF8_REPLACEMENT_RATIO = 0.005


@dataclass(frozen=True)
class Rule:
    pattern: str
    title: str
    cause: str
    fixes: tuple[str, ...]
    severity: str = "error"


@dataclass
class Finding:
    title: str
    cause: str
    fixes: list[str]
    severity: str = "error"
    evidence: str = ""


@dataclass
class CrashAnalysis:
    report_path: Path | None
    source: str
    timestamp: float
    headline: str
    findings: list[Finding] = field(default_factory=list)
    game_version: str = ""
    java_version: str = ""
    suspect_mods: list[str] = field(default_factory=list)
    excerpt: str = ""
    raw: str = ""

    @property
    def title(self) -> str:
        if self.findings:
            return self.findings[0].title
        return self.headline or "游戏崩溃"


# --------------------------------------------------------------------- rules
RULES: tuple[Rule, ...] = (
    Rule(
        r"java\.lang\.OutOfMemoryError:\s*Java heap space|java\.lang\.OutOfMemoryError\b(?!: Metaspace)",
        "内存不够用",
        "游戏分配到的内存被用光了。整合包、高清材质或大量 Mod 都会让内存需求变大。",
        (
            "在“设置 → 最大内存”里调大内存（建议为物理内存的一半，例如 8G/12G）。",
            "启动游戏前关闭浏览器、视频剪辑等吃内存的程序。",
            "确认使用的是 64 位 Java：32 位 Java 最多只能用到约 1.5G。",
        ),
    ),
    Rule(
        r"java\.lang\.OutOfMemoryError:\s*Metaspace|Compressed class space",
        "元空间不足（通常是 Mod 太多）",
        "Java 存放类信息的“元空间”被占满了，常见于一次加载几百个 Mod。",
        (
            "减少同时启用的 Mod 数量。",
            "使用 64 位 Java 21，并把最大内存调大到 6G 以上。",
            "如果仍不够，可在高级 JVM 参数里加 -XX:MaxMetaspaceSize=1G。",
        ),
    ),
    Rule(
        r"GC overhead limit exceeded",
        "内存压力过大",
        "JVM 花了几乎全部时间做垃圾回收，说明内存严重不足。",
        (
            "调大最大内存（Xmx）。",
            "关闭其他占用内存的程序。",
            "移除体积过大或已知泄漏的 Mod。",
        ),
    ),
    Rule(
        r"UnsupportedClassVersionError|has been compiled by a more recent version of the Java Runtime|class file version \d+\.0",
        "Java 版本不对（太旧）",
        "游戏或 Mod 是用更新的 Java 编译的，当前 Java 版本读不懂这些类文件。",
        (
            "在“设置 → Java 路径”里改选 Java 21（1.20.5 以上）或 Java 17（1.18~1.20.4）。",
            "如果本机没有合适的 Java，用“一键安装 Java”自动下载。",
        ),
    ),
    Rule(
        r"java\.lang\.NoSuchMethodError|java\.lang\.NoSuchFieldError",
        "Mod 与游戏版本不匹配",
        "某个 Mod 调用了不存在的游戏方法，通常是 Mod 版本和 Minecraft 版本对不上。",
        (
            "在 Mod 管理里检查该 Mod 是否支持当前游戏版本。",
            "把 Mod 更新到与游戏版本匹配的版本，或换回旧版。",
            "同时确认 Fabric API / Forge 版本与加载器一致。",
        ),
    ),
    Rule(
        r"java\.lang\.NoClassDefFoundError|java\.lang\.ClassNotFoundException",
        "缺少前置 Mod 或依赖库",
        "游戏找不到某个类，通常是缺少前置 Mod，或者依赖库下载不完整。",
        (
            "查看下面的“可疑 Mod”，为它安装缺少的前置。",
            "在“百宝箱 → Mod 管理”点“冲突检测”，一键检查缺失前置。",
            "在“下载 / 安装”重新安装一次版本，修复缺失的库文件。",
        ),
    ),
    Rule(
        r"ModResolutionException|Incompatible mod set|requires .{0,80}which is missing|Missing or unsupported mandatory dependencies",
        "Mod 依赖冲突（缺少前置或版本不符）",
        "加载器在启动前就发现 Mod 之间的依赖关系不满足，于是直接拒绝启动。",
        (
            "在 Mod 管理里查看缺失的前置并安装。",
            "把冲突的两个 Mod 都更新到支持当前 Minecraft 版本的最新版。",
            "暂时禁用最近新加的 Mod，逐个排查。",
        ),
    ),
    Rule(
        r"Duplicate mod|duplicate mods|already registered|duplicate entry",
        "存在重复的 Mod",
        "同一个 Mod 被放了两个版本，加载器无法决定用哪一个。",
        (
            "在 Mod 管理里搜索同名 Mod，只保留一个版本。",
            "被禁用的 Mod 不会生效，确认没有把新旧两个版本都启用。",
        ),
    ),
    Rule(
        r"Mixin apply failed|MixinApplyError|mixin\.injection|Mixin transformation of .* failed",
        "Mixin 注入失败（Mod 不兼容）",
        "某个 Mod 想修改游戏代码但失败了，通常是两个 Mod 改了同一处，或 Mod 版本太旧。",
        (
            "更新最近安装的 Mod 到最新版。",
            "禁用“优化类 / 修改界面类”Mod 做对比测试。",
            "确认 Forge/Fabric 加载器版本与 Mod 要求一致。",
        ),
    ),
    Rule(
        r"Pixel format not accelerated|WGL: The driver does not appear to support OpenGL|GLFW error 65542|Failed to create window|WGL: Failed to make context current",
        "显卡驱动无法创建游戏窗口",
        "OpenGL 初始化失败，常见原因是显卡驱动过旧、使用了远程桌面，或误用了集成显卡。",
        (
            "更新显卡驱动（NVIDIA / AMD / Intel 官网）。",
            "不要用远程桌面或虚拟机运行游戏。",
            "笔记本请确认游戏使用的是独立显卡。",
            "在启动器高级参数中尝试加 -Dorg.lwjgl.opengl.Display.allowSoftwareOpenGL=true。",
        ),
    ),
    Rule(
        r"The driver does not appear to support OpenGL|OpenGL 3\.2|OpenGL 4\.\d.*(not supported|required)",
        "显卡不支持游戏所需的 OpenGL",
        "游戏需要 OpenGL 3.2 以上，当前驱动或显卡不满足要求。",
        (
            "更新显卡驱动。",
            "如果显卡很旧，请降低游戏版本或使用 Sodium 等性能 Mod。",
            "确认没有强制使用软件渲染。",
        ),
    ),
    Rule(
        r"EXCEPTION_ACCESS_VIOLATION|SIGSEGV|A fatal error has been detected by the Java Runtime|EXCEPTION_STACK_OVERFLOW",
        "Java 虚拟机崩溃（驱动 / 内存 / 超频）",
        "这是 JVM 级别的崩溃，通常和显卡驱动、内存超频或系统环境有关，而不是普通的游戏报错。",
        (
            "更新显卡驱动，并重启电脑。",
            "关闭内存 XMP/EXPO 超频后再试。",
            "把最大内存调低一些（例如 6G），避免超出系统可用内存。",
            "查看游戏目录下的 hs_err_pid*.log，里面记录了崩溃模块。",
        ),
    ),
    Rule(
        r"java\.lang\.StackOverflowError",
        "Mod 递归调用导致栈溢出",
        "某个 Mod 在初始化或运行中无限递归，通常是 Mod 组合冲突。",
        (
            "禁用最近添加的 Mod 再试。",
            "更新涉及的 Mod 到最新版。",
        ),
    ),
    Rule(
        r"ConcurrentModificationException",
        "Mod 之间互相干扰",
        "两个 Mod 同时修改同一份数据导致冲突。",
        (
            "逐个禁用最近安装的 Mod 排查。",
            "把相关 Mod 更新到最新版。",
        ),
    ),
    Rule(
        r"java\.lang\.ExceptionInInitializerError|Could not execute entrypoint|Failed to load mod|Could not load mod",
        "Mod 初始化失败",
        "某个 Mod 在加载阶段就抛出了异常，通常是缺少前置或版本不兼容。",
        (
            "查看下面的可疑 Mod 和缺失前置。",
            "更新该 Mod 以及它的前置。",
            "临时禁用该 Mod，确认游戏能否正常启动。",
        ),
    ),
    Rule(
        r"ZipException|zip END header not found|invalid CEN header|Invalid or corrupted jarfile|error in opening zip file",
        "有文件下载损坏",
        "某个 jar 压缩包不完整，通常是下载中断或被安全软件改动。",
        (
            "在“下载 / 安装”重新安装当前版本，修复损坏的库。",
            "删除 mods 里最近下载的 Mod 后重新下载。",
            "把游戏目录加入杀毒软件白名单，避免文件被拦截。",
        ),
    ),
    Rule(
        r"java\.io\.FileNotFoundException|java\.nio\.file\.NoSuchFileException",
        "文件缺失",
        "游戏需要的某个文件不存在，可能是安装不完整或被误删。",
        (
            "在“下载 / 安装”重新安装当前版本。",
            "检查游戏目录是否被杀毒软件清理过。",
        ),
    ),
    Rule(
        r"AccessDeniedException|Access is denied|java\.io\.IOException: 拒绝访问|Permission denied",
        "没有文件写入权限",
        "游戏无法写入存档、日志或配置，通常是把游戏装在了受保护目录，或被杀毒软件拦截。",
        (
            "不要把游戏目录放在 C:\\Program Files 等受保护位置。",
            "把游戏目录加入杀毒软件 / Windows Defender 白名单。",
            "右键游戏目录 → 属性 → 安全，确认当前用户有“完全控制”。",
        ),
    ),
    Rule(
        r"Address already in use|BindException|Failed to bind to port",
        "端口被占用",
        "游戏要用的网络端口已被其他程序占用。",
        (
            "关闭上一个没有完全退出的 Minecraft 进程。",
            "重启电脑后再试。",
        ),
    ),
    Rule(
        r"Invalid session|Failed to verify username|AuthenticationException|Invalid credentials|invalid_token",
        "登录状态失效",
        "启动器保存的登录凭证过期或被服务器拒绝。",
        (
            "在启动器右上角重新登录 Microsoft 账号。",
            "确认系统时间准确（时间偏差会导致令牌失效）。",
        ),
    ),
    Rule(
        r"UnknownHostException|ConnectException|SocketTimeoutException|Failed to download|Unable to download|Connection reset",
        "网络连接失败",
        "游戏无法连接 Mojang 服务器或资源站点，可能是网络波动、代理或防火墙拦截。",
        (
            "检查网络连接，关闭代理/VPN 后重试。",
            "在“下载 / 安装”重新安装版本，补齐缺失资源。",
            "把 java.exe 加入防火墙白名单。",
        ),
    ),
    Rule(
        r"Ticking entity|Ticking block entity|Ticking screen|Exception ticking",
        "某个 Mod 在游戏运行中崩溃",
        "游戏已经进入世界，但某个实体/方块/界面的逻辑抛出了异常。",
        (
            "查看下面的可疑 Mod，更新或禁用它。",
            "回到出问题前的存档备份（启动器会自动备份存档）。",
            "如果只有特定存档崩溃，可能是该存档里的方块数据损坏。",
        ),
    ),
    Rule(
        r"java\.lang\.IllegalArgumentException: Unsupported class file major version|Unsupported class file major version",
        "Java 版本过新",
        "当前 Java 版本比游戏/Mod 支持的版本更新。",
        (
            "改选较旧的 Java：1.16.5 及以下用 Java 8，1.18~1.20.4 用 Java 17。",
            "在“设置 → Java 路径”里切换运行时。",
        ),
    ),
    Rule(
        r"Could not reserve enough space|Cannot allocate memory|Native memory allocation|There is insufficient memory",
        "系统内存不足",
        "系统无法再分配内存给 Java，可能是 Xmx 设置超过了系统可用内存。",
        (
            "调低最大内存（Xmx），留出至少 2G 给系统。",
            "关闭其他大型程序后重试。",
            "确认使用的是 64 位 Java。",
        ),
    ),
    Rule(
        r"java\.lang\.NullPointerException",
        "空指针异常（通常是 Mod 问题）",
        "某个 Mod 在数据为空时继续执行，触发空指针。",
        (
            "更新最近安装的 Mod。",
            "在 Mod 管理里禁用可疑 Mod 后重试。",
            "把日志交给内置 AI 诊断做进一步分析。",
        ),
    ),
    Rule(
        r"java\.lang\.RuntimeException: Mixin|Mixin config .* failed",
        "Mixin 配置加载失败",
        "Mod 的 Mixin 配置文件无法应用，通常是加载器版本或 Mod 版本不匹配。",
        (
            "更新 Fabric Loader / Forge 到最新版。",
            "更新出问题的 Mod。",
        ),
    ),
    Rule(
        r"java\.lang\.OutOfMemoryError: Direct buffer memory|OutOfDirectMemoryError",
        "显存/直接内存不足",
        "游戏申请的堆外内存过多，常见于高清材质包或大量区块渲染。",
        (
            "降低渲染距离与材质分辨率。",
            "调大最大内存并更新显卡驱动。",
        ),
    ),
)

_COMPILED = [(re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE), rule) for rule in RULES]

_MC_VERSION_RE = re.compile(r"Minecraft Version:\s*([^\s,]+)", re.IGNORECASE)
_JAVA_VERSION_RE = re.compile(r"Java Version:\s*([^,\n]+)", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"Description:\s*([^\n]+)", re.IGNORECASE)
_EXCEPTION_RE = re.compile(
    r"^((?:java|javax|net|com|org|io|kotlin)\.[\w.$]+(?:Exception|Error))(?::\s*(.*))?$",
    re.MULTILINE,
)
_SUSPECT_RE = re.compile(
    r"(?:Suspected mods?|Suspect(?:ed)? Mod[s]?|Failure message)[:：]\s*([^\n]+)",
    re.IGNORECASE,
)
_MOD_ID_RE = re.compile(r"Mod ID:\s*([\w.-]+)", re.IGNORECASE)
_XMX_RE = re.compile(r"-Xmx(\d+)([kKmMgG]?)", re.IGNORECASE)
_XMS_RE = re.compile(r"-Xms(\d+)([kKmMgG]?)", re.IGNORECASE)
_FABRIC_MODS_RE = re.compile(r"Fabric Mods:[ \t]*\n((?:[ \t]+\S[^\n]*\n?)+)", re.IGNORECASE)
# A line of that block reads "\t\tfabric-api: Fabric API 0.83.0"; the id sits
# before the colon, so a stack frame such as "\tat foo.bar(Baz.java:42)" never
# matches it.
_FABRIC_MOD_LINE_RE = re.compile(r"^[ \t]+(\S+?):\s")


def _read_text(path: Path) -> str:
    start = 0
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            # Crash reports are small; huge latest.log files keep the error at
            # the end, so read the tail instead of the first megabytes.
            if size > MAX_READ:
                start = size - MAX_READ
                handle.seek(start)
            data = handle.read(MAX_READ)
    except OSError:
        return ""
    if start:
        # A tail read can begin inside a multi-byte character or in the middle
        # of a line; dropping that partial first line (or, for a single huge
        # line, the broken leading bytes) keeps the strict decodes below from
        # mis-detecting the encoding.
        newline = data.find(b"\n")
        if 0 <= newline < 4096:
            data = data[newline + 1 :]
        else:
            trimmed = 0
            while trimmed < 4 and data[:1] and data[0] & 0xC0 == 0x80:
                data = data[1:]
                trimmed += 1
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        replaced = data.decode("utf-8", "replace")
    if replaced.count("\ufffd") / max(1, len(replaced)) <= UTF8_REPLACEMENT_RATIO:
        return replaced
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        return replaced


def _safe_mtime(path: Path) -> float:
    """Modification time of *path*, or 0.0 when it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def latest_crash_report(game_dir: str | Path) -> Path | None:
    folder = Path(game_dir) / "crash-reports"
    if not folder.is_dir():
        return None
    candidates: list[Path] = []
    try:
        for path in folder.iterdir():
            if path.is_file() and path.name.lower().endswith((".txt", ".log")):
                candidates.append(path)
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=_safe_mtime)


def latest_jvm_crash(game_dir: str | Path) -> Path | None:
    root = Path(game_dir)
    try:
        candidates = [p for p in root.glob("hs_err_pid*.log") if p.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=_safe_mtime)


def _human_xmx(text: str) -> str:
    match = _XMX_RE.search(text)
    if not match:
        return ""
    amount, unit = match.group(1), match.group(2).lower()
    if unit in {"g"}:
        return f"{amount}G"
    if unit in {"m"}:
        return f"{int(amount) // 1024}G" if int(amount) >= 1024 else f"{amount}M"
    if unit in {"k"}:
        return f"{int(amount) // (1024 * 1024)}G"
    return f"{int(amount) // (1024 * 1024 * 1024)}G"


def _suggest_xmx(text: str) -> str:
    match = _XMX_RE.search(text)
    if not match:
        return "6G~8G"
    amount, unit = int(match.group(1)), match.group(2).lower()
    megabytes = amount
    if unit == "g":
        megabytes = amount * 1024
    elif unit == "k":
        megabytes = amount // 1024
    elif unit == "":
        megabytes = amount // (1024 * 1024)
    target = min(16384, max(2048, megabytes * 2))
    return f"{target // 1024}G" if target >= 1024 else f"{target}M"


def _extract_suspects(text: str) -> list[str]:
    suspects: list[str] = []
    for match in _SUSPECT_RE.finditer(text):
        for name in re.split(r"[,，;；/]", match.group(1)):
            cleaned = name.strip().strip("()[]")
            if cleaned and cleaned.lower() not in {"none", "unknown"}:
                suspects.append(cleaned)
    for match in _MOD_ID_RE.finditer(text):
        suspects.append(match.group(1).strip())
    fabric = _FABRIC_MODS_RE.search(text)
    if fabric:
        for line in fabric.group(1).splitlines()[:12]:
            stripped = line.strip()
            # Stack frames and "... 12 more" lines are not mods.
            if not stripped or stripped.startswith("at ") or re.match(r"\.\.\.\s*\d+\s+more", stripped):
                continue
            mod_line = _FABRIC_MOD_LINE_RE.match(line)
            if mod_line:
                suspects.append(mod_line.group(1))
    seen: set[str] = set()
    unique: list[str] = []
    for name in suspects:
        key = name.lower()
        if key in seen or len(name) > 80:
            continue
        seen.add(key)
        unique.append(name)
    return unique[:8]


def _excerpt(text: str, headline: str, evidence: str = "") -> str:
    lines = text.splitlines()
    anchor = 0
    needle = evidence or headline
    for index, line in enumerate(lines):
        if needle and needle[:60] in line:
            anchor = index
            break
    start = max(0, anchor - 6)
    stop = min(len(lines), anchor + 16)
    return "\n".join(lines[start:stop])[:2400]


def analyze(game_dir: str | Path, report: str | Path | None = None) -> CrashAnalysis | None:
    """Analyse the newest crash report (or ``latest.log`` as a fallback)."""
    game = Path(game_dir)
    path: Path | None = Path(report) if report else latest_crash_report(game)
    source = "崩溃报告"
    if path is None or not path.is_file():
        jvm = latest_jvm_crash(game)
        if jvm is not None:
            path, source = jvm, "JVM 崩溃日志"
    if path is None or not path.is_file():
        log = game / "logs" / "latest.log"
        if log.is_file():
            path, source = log, "最新日志"

    if path is None or not path.is_file():
        return None

    text = _read_text(path)
    if not text.strip():
        return None

    try:
        timestamp = path.stat().st_mtime
    except OSError:
        timestamp = 0.0

    game_version = ""
    match = _MC_VERSION_RE.search(text)
    if match:
        game_version = match.group(1).strip()
    java_version = ""
    match = _JAVA_VERSION_RE.search(text)
    if match:
        java_version = match.group(1).strip()

    description = ""
    match = _DESCRIPTION_RE.search(text)
    if match:
        description = match.group(1).strip()

    headline = description
    match = _EXCEPTION_RE.search(text)
    if match:
        exception = match.group(1)
        detail = (match.group(2) or "").strip()
        if detail:
            exception = f"{exception}: {detail[:120]}"
        headline = headline or exception
    if not headline:
        headline = "游戏异常退出，未找到明确错误"

    findings: list[Finding] = []
    used_titles: set[str] = set()
    for regex, rule in _COMPILED:
        match = regex.search(text)
        if not match:
            continue
        if rule.title in used_titles:
            continue
        used_titles.add(rule.title)
        fixes = list(rule.fixes)
        if rule.title == "内存不够用":
            fixes.insert(
                0,
                f"当前 Xmx 约为 {_human_xmx(text) or '未记录'}，建议先调到 {_suggest_xmx(text)}。",
            )
        findings.append(
            Finding(
                title=rule.title,
                cause=rule.cause,
                fixes=fixes,
                severity=rule.severity,
                evidence=match.group(0)[:200],
            )
        )
        if len(findings) >= 4:
            break

    if not findings:
        findings.append(
            Finding(
                title="未识别的崩溃",
                cause=(
                    "日志里没有命中常见的错误特征。"
                    "这可能是某个 Mod 的特殊问题，或日志被截断了。"
                ),
                fixes=[
                    "打开下面的崩溃报告，搜索 “Caused by” 一行，通常能看到真正的起因。",
                    "把最近安装的 Mod 暂时禁用后再试。",
                    "点“AI 深入分析”，让 AI 结合完整日志给出结论。",
                ],
                evidence=headline,
            )
        )

    return CrashAnalysis(
        report_path=path,
        source=source,
        timestamp=timestamp,
        headline=headline,
        findings=findings,
        game_version=game_version,
        java_version=java_version,
        suspect_mods=_extract_suspects(text),
        excerpt=_excerpt(text, headline, findings[0].evidence if findings else ""),
        raw=text,
    )


def analyze_since(game_dir: str | Path, since: float) -> CrashAnalysis | None:
    """Analyse only a report written after *since* (a launch attempt)."""
    report = latest_crash_report(game_dir)
    if report is None:
        return None
    try:
        if report.stat().st_mtime < since - 2:
            return None
    except OSError:
        return None
    return analyze(game_dir, report)


def plain_text(analysis: CrashAnalysis, include_excerpt: bool = True) -> str:
    """Human-readable advice used by the crash dialog and the copy button."""
    lines: list[str] = []
    meta: list[str] = []
    if analysis.game_version:
        meta.append(f"Minecraft {analysis.game_version}")
    if analysis.java_version:
        meta.append(f"Java {analysis.java_version}")
    if meta:
        lines.append(" · ".join(meta))
        lines.append("")
    for finding in analysis.findings:
        lines.append(f"【{finding.title}】")
        lines.append(finding.cause)
        lines.append("")
        lines.append("建议这样修：")
        for index, fix in enumerate(finding.fixes, 1):
            lines.append(f"  {index}. {fix}")
        lines.append("")
    if analysis.suspect_mods:
        lines.append("可疑 Mod：" + "、".join(analysis.suspect_mods))
        lines.append("")
    if include_excerpt and analysis.excerpt:
        lines.append("关键日志片段：")
        lines.append(analysis.excerpt)
    if analysis.report_path:
        lines.append("")
        lines.append(f"完整日志：{analysis.report_path}")
    return "\n".join(lines).strip()
