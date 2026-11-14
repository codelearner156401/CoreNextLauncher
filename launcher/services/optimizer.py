"""Startup optimizer: the recommended client mods and their Modrinth install.

Sodium, Lithium, FerriteCore and Iris are what actually turn a stuttering
vanilla install into a smooth one, but asking a player to hunt them down is
where the fun stops.  This module owns the recommendation table the UI and the
install logic share, a purely local "is it already there" scan, and a download
step that reuses :mod:`modrinth` for the file choice and :mod:`net` for the
atomic write plus SHA1 verification.
"""

from __future__ import annotations

import re
import ssl
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

from ..constants import MODRINTH_API
from . import modpack, modrinth, net

# Loaders whose instances can use Modrinth mods at all.
MODDED_LOADERS = ("fabric", "quilt", "forge", "neoforge")

NotifyCallback = Callable[[str], None]
StopCallback = Callable[[], bool]


class OptimizeError(RuntimeError):
    """A download the optimizer could not even resolve, in plain Chinese."""


@dataclass(frozen=True)
class Recommendation:
    """One suggestion: what to install and why it is worth it."""

    slug: str
    name: str
    grade: str
    default_checked: bool
    summary: str


RECOMMENDATIONS: tuple[Recommendation, ...] = (
    Recommendation(
        slug="sodium",
        name="Sodium",
        grade="SSS",
        default_checked=True,
        summary="重写渲染管线，帧率通常翻倍，画面设置几乎不受影响。",
    ),
    Recommendation(
        slug="lithium",
        name="Lithium",
        grade="SSS",
        default_checked=True,
        summary="优化实体、方块与区块的逻辑开销，单机与联机都生效。",
    ),
    Recommendation(
        slug="iris",
        name="Iris",
        grade="S",
        default_checked=False,
        summary="让 Sodium 也能加载 OptiFine 光影包；不需要光影时不必装。",
    ),
    Recommendation(
        slug="ferritecore",
        name="FerriteCore",
        grade="S",
        default_checked=True,
        summary="压缩模型与方块状态的内存占用，低内存机器最明显。",
    ),
)

RECOMMENDATION_BY_SLUG: dict[str, Recommendation] = {
    item.slug: item for item in RECOMMENDATIONS
}

# Every suggestion is offered in the prompt; default_checked decides which
# checkbox opens ticked.
DEFAULT_SLUGS: tuple[str, ...] = tuple(item.slug for item in RECOMMENDATIONS)

# Jar file names are not the slug (iris-mc1.20.1-1.7.0.jar), so a suggestion
# counts as installed when the slug shows up as one token of the file name.
JAR_ALIASES: dict[str, tuple[str, ...]] = {
    "sodium": ("sodium",),
    "lithium": ("lithium",),
    "iris": ("iris",),
    "ferritecore": ("ferritecore", "ferrite-core"),
}

# A jar that carries the slug token but is a different mod: "sodium-extra"
# depends on Sodium instead of replacing it, so it must not hide Sodium.
JAR_CONTRADICTIONS: dict[str, frozenset[str]] = {
    "sodium": frozenset({"extra"}),
}

_JAR_SUFFIXES = (".jar", ".jar.disabled")
# Jar names use dots as separators, version ids keep them ("1.20.1").
_JAR_SPLIT = re.compile(r"[-_+. ]+")
_ID_SPLIT = re.compile(r"[-_+ ]+")
_MC_VERSION = re.compile(r"^1\.\d{1,2}(?:\.\d{1,2})?$")
_VERSION_TYPE_ORDER = {"release": 0, "beta": 1, "alpha": 2}


@dataclass(frozen=True)
class ModPlan:
    """What to install, for which instance."""

    game_dir: Path
    mc_version: str
    loader: str
    slugs: tuple[str, ...] = DEFAULT_SLUGS

    @property
    def mods_dir(self) -> Path:
        return Path(self.game_dir) / "mods"


@dataclass(frozen=True)
class Candidate:
    """A selected suggestion plus the jar that already covers it."""

    recommendation: Recommendation
    jar: Path | None = None

    @property
    def installed(self) -> bool:
        return self.jar is not None


@dataclass
class InstallResult:
    """One download pass: what landed, what did not, and why."""

    installed: list[tuple[str, Path]] = field(default_factory=list)
    skipped: list[tuple[str, Path]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False

    def summary(self) -> str:
        parts: list[str] = []
        if self.installed:
            parts.append(f"已安装 {len(self.installed)} 项")
        if self.skipped:
            parts.append(f"已存在跳过 {len(self.skipped)} 项")
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 项")
        if self.cancelled:
            parts.append("已取消")
        return "，".join(parts) if parts else "没有需要下载的 Mod"

    def failure_text(self) -> str:
        return "\n".join(f"· {name}：{reason}" for name, reason in self.failed)


def loader_label(loader: str) -> str:
    for value, label in modpack.LOADER_KEYS.values():
        if value == loader:
            return label
    return loader or "原版"


# --------------------------------------------------------------- plan building
def build_plan(
    game_dir: str | Path,
    mc_version: str,
    loader: str,
    slugs: Sequence[str] | None = None,
) -> ModPlan:
    """A plan for one instance; unknown slugs are dropped instead of guessed."""
    wanted = tuple(
        slug
        for slug in (slugs if slugs is not None else DEFAULT_SLUGS)
        if slug in RECOMMENDATION_BY_SLUG
    )
    return ModPlan(
        game_dir=Path(game_dir),
        mc_version=str(mc_version or "").strip(),
        loader=str(loader or "").strip().lower(),
        slugs=wanted,
    )


def plan_for_launch(
    game_dir: str | Path,
    version_id: str,
    raw: dict | None = None,
) -> ModPlan | None:
    """Plan for a version that is about to launch, or None when it is not modded."""
    loader = loader_of(version_id, raw)
    if loader not in MODDED_LOADERS:
        return None
    mc_version = mc_version_of(version_id, raw)
    if not mc_version:
        return None
    return build_plan(game_dir, mc_version, loader)


def loader_of(version_id: str, raw: dict | None = None) -> str:
    """Detect the mod loader of a version JSON, strongest signal first."""
    raw = raw if isinstance(raw, dict) else {}
    libraries = [
        str(item.get("name") or "")
        for item in raw.get("libraries") or []
        if isinstance(item, dict)
    ]
    for prefix, loader in modpack.LOADER_LIBRARIES:
        if any(name.startswith(prefix) for name in libraries):
            return loader
    text = " ".join(
        str(part or "")
        for part in (version_id, raw.get("id"), raw.get("mainClass"), raw.get("inheritsFrom"))
    ).lower()
    # "forge" is checked last: every NeoForge profile also contains "forge".
    for marker, loader in (
        ("neoforge", "neoforge"),
        ("quilt", "quilt"),
        ("fabric", "fabric"),
        ("forge", "forge"),
    ):
        if marker in text:
            return loader
    return ""


def mc_version_of(version_id: str, raw: dict | None = None) -> str:
    """The Minecraft version a (possibly loader-prefixed) profile builds on."""
    raw = raw if isinstance(raw, dict) else {}
    inherited = str(raw.get("inheritsFrom") or "").strip()
    if _MC_VERSION.match(inherited):
        return inherited
    for token in _ID_SPLIT.split(str(version_id or "")):
        if _MC_VERSION.match(token):
            return token
    return inherited


# ------------------------------------------------------------- local inspection
def _jar_tokens(name: str) -> set[str]:
    lowered = name.lower()
    for suffix in _JAR_SUFFIXES:
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
            break
    return {token for token in _JAR_SPLIT.split(lowered) if token}


def installed_jar(mods_dir: str | Path, slug: str) -> Path | None:
    """The jar in mods_dir that already provides slug, if there is one."""
    folder = Path(mods_dir)
    if not folder.is_dir():
        return None
    aliases = set(JAR_ALIASES.get(slug, (slug,)))
    blocked = JAR_CONTRADICTIONS.get(slug, frozenset())
    for path in sorted(folder.iterdir()):
        if not path.name.lower().endswith(_JAR_SUFFIXES):
            continue
        tokens = _jar_tokens(path.name)
        if not tokens & aliases:
            continue
        if tokens & blocked:
            continue
        return path
    return None


def candidates(plan: ModPlan) -> list[Candidate]:
    """Every selected suggestion, each carrying the jar that already covers it."""
    return [
        Candidate(RECOMMENDATION_BY_SLUG[slug], installed_jar(plan.mods_dir, slug))
        for slug in plan.slugs
        if slug in RECOMMENDATION_BY_SLUG
    ]


def missing(plan: ModPlan) -> list[Candidate]:
    """Only the selected suggestions that still need a download."""
    return [item for item in candidates(plan) if not item.installed]


# ------------------------------------------------------------- version picking
def _primary_file(version: dict) -> dict:
    helper = getattr(modrinth, "_primary_file", None)
    if callable(helper):
        return dict(helper(version) or {})
    files = version.get("files") or []
    for item in files:
        if isinstance(item, dict) and item.get("primary"):
            return item
    return files[0] if files else {}


def fetch_versions(slug: str, mc_version: str, loader: str) -> list[dict]:
    """Builds Modrinth lists for one project, pre-narrowed by loader + version."""
    query = net.query({"loaders": [loader], "game_versions": [mc_version]})
    data = net.get_json(f"{MODRINTH_API}/project/{slug}/version?{query}")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def version_matches(version: dict, mc_version: str, loader: str) -> bool:
    """Strict check: the API query is a hint, the file itself is the truth."""
    loaders = [str(item).lower() for item in version.get("loaders") or []]
    games = [str(item) for item in version.get("game_versions") or []]
    if loader and loader not in loaders:
        return False
    if mc_version and mc_version not in games:
        return False
    return bool(_primary_file(version).get("url"))


def select_version(
    versions: Sequence[dict],
    slug: str,
    mc_version: str,
    loader: str,
) -> dict:
    """Newest release (then beta, then alpha) that really matches loader + MC."""
    matches = [
        version
        for version in versions
        if isinstance(version, dict) and version_matches(version, mc_version, loader)
    ]
    if not matches:
        name = RECOMMENDATION_BY_SLUG[slug].name if slug in RECOMMENDATION_BY_SLUG else slug
        raise OptimizeError(
            f"Modrinth 上没有 {name}（{slug}）支持 Minecraft {mc_version} + "
            f"{loader_label(loader)} 的文件，请升级该加载器或换一个 Minecraft 版本。"
        )
    matches.sort(key=lambda item: str(item.get("date_published") or ""), reverse=True)
    matches.sort(
        key=lambda item: _VERSION_TYPE_ORDER.get(str(item.get("version_type") or "release"), 3)
    )
    return matches[0]


# ------------------------------------------------------------------- downloads
def _is_cert_error(exc: BaseException) -> bool:
    """True when the exception chain holds a TLS certificate verification failure."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if isinstance(current, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        reason = getattr(current, "reason", None)
        current = (
            current.__cause__
            or current.__context__
            or (reason if isinstance(reason, BaseException) else None)
        )
    return False


@contextmanager
def _unverified_tls() -> Iterator[None]:
    """One retry window with check_hostname=False + CERT_NONE.

    net.download builds its own context, so the hooks are urllib's default
    factory plus the already-built global opener, whose HTTPSHandler caches a
    verified context when it is constructed.  The swap covers exactly one
    request and is restored even when that request fails.
    """
    original = ssl._create_default_https_context
    unverified = ssl._create_unverified_context()
    ssl._create_default_https_context = ssl._create_unverified_context
    patched: list[tuple[object, ssl.SSLContext]] = []
    opener = getattr(urllib.request, "_opener", None)
    for handler in getattr(opener, "handlers", None) or ():
        if isinstance(handler, urllib.request.HTTPSHandler):
            patched.append((handler, handler._context))
            handler._context = unverified
    try:
        yield
    finally:
        for handler, context in patched:
            handler._context = context
        ssl._create_default_https_context = original


def _percent_notifier(
    name: str, index: int, total: int, notify: NotifyCallback | None
) -> Callable[[int, int], None] | None:
    """Progress text naming the current mod and its place in the batch."""
    if notify is None:
        return None
    last = {"step": -1}

    def report(done: int, size: int) -> None:
        if not size:
            return
        step = min(100, int(done * 100 / size)) // 10
        if step == last["step"]:
            return
        last["step"] = step
        notify(f"正在下载 {name}（{index}/{total}） {step * 10}%")

    return report


def _install_one(
    plan: ModPlan,
    item: Candidate,
    index: int,
    total: int,
    result: InstallResult,
    notify: NotifyCallback | None,
) -> Path:
    slug = item.recommendation.slug
    name = item.recommendation.name
    version = select_version(
        fetch_versions(slug, plan.mc_version, plan.loader),
        slug,
        plan.mc_version,
        plan.loader,
    )
    file_info = _primary_file(version)
    url = str(file_info.get("url") or "")
    if not url:
        raise OptimizeError(f"Modrinth 返回的 {name}（{slug}）版本没有可下载文件")
    plan.mods_dir.mkdir(parents=True, exist_ok=True)
    target = plan.mods_dir / str(file_info.get("filename") or f"{slug}.jar")
    sha1 = str((file_info.get("hashes") or {}).get("sha1") or "") or None
    size = int(file_info.get("size") or 0) or None
    if notify is not None:
        notify(f"正在下载 {name}（{index}/{total}） 0%")
    try:
        net.download(
            url,
            target,
            expected_sha1=sha1,
            expected_size=size,
            progress=_percent_notifier(name, index, total, notify),
        )
    except Exception as exc:
        if not _is_cert_error(exc):
            raise
        try:
            with _unverified_tls():
                net.download(url, target, expected_sha1=sha1, expected_size=size)
        except Exception as retry_exc:
            result.notes.append(
                f"{name}：系统证书无法验证 Modrinth，已改成不校验证书重试一次，仍然失败。"
            )
            raise retry_exc
        result.notes.append(
            f"{name}：系统证书无法验证 Modrinth，该次下载未校验证书（不校验的重试成功）。"
        )
    return target


def install(
    plan: ModPlan,
    items: Sequence[Candidate],
    notify: NotifyCallback | None = None,
    should_stop: StopCallback | None = None,
) -> InstallResult:
    """Download items into plan.mods_dir; one bad mod never stops the rest."""
    result = InstallResult()
    total = len(items)
    for index, item in enumerate(items, start=1):
        if should_stop is not None and should_stop():
            result.cancelled = True
            result.notes.append("下载已取消，剩余 Mod 未安装。")
            break
        name = item.recommendation.name
        if item.installed:
            result.skipped.append((name, item.jar))
            continue
        if notify is not None:
            notify(f"正在下载 {name}（{index}/{total}）")
        try:
            result.installed.append(
                (name, _install_one(plan, item, index, total, result, notify))
            )
        except Exception as exc:  # one mod's failure must not hide the others
            result.failed.append((name, str(exc) or exc.__class__.__name__))
    if notify is not None:
        notify(f"下载结束：{result.summary()}")
    return result
