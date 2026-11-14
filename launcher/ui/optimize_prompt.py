"""Pre-launch optimizer prompt: tick the recommended mods, download, then play.

The dialog never blocks a launch on its own failure: whatever happens here is
reported through the log page and the caller starts the game anyway.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..services import optimizer
from .tasks import start_task


class OptimizePromptDialog(QDialog):
    """Four recommendations, one checkbox each, downloads on confirm."""

    log_requested = Signal(str, str)

    def __init__(self, parent, plan: optimizer.ModPlan) -> None:
        super().__init__(parent)
        self.plan = plan
        self.result_summary = ""
        self.failures: list[tuple[str, str]] = []
        self._finished = False
        self._busy = False
        self._candidates = optimizer.candidates(plan)
        self._missing = [item for item in self._candidates if not item.installed]

        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("启动前智能优化")
        self.resize(640, 460)
        self.setMinimumSize(520, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("启动前智能优化")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            f"检测到 Minecraft {plan.mc_version} · {optimizer.loader_label(plan.loader)} 环境，"
            "下面这些 Mod 能明显改善帧率与卡顿；已装的会自动跳过。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.checkboxes: list[QCheckBox] = []
        for candidate in self._candidates:
            recommendation = candidate.recommendation
            row = QHBoxLayout()
            row.setSpacing(10)

            box = QCheckBox(recommendation.name)
            box.setChecked(recommendation.default_checked and not candidate.installed)
            box.setEnabled(not candidate.installed)
            self.checkboxes.append(box)
            row.addWidget(box)

            badge = QLabel(f"{recommendation.grade} 级")
            badge.setObjectName("Hint")
            badge.setMinimumWidth(48)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge)

            summary = QLabel(
                f"{recommendation.summary}（已安装，本次跳过）"
                if candidate.installed
                else recommendation.summary
            )
            summary.setObjectName("Muted")
            summary.setWordWrap(True)
            row.addWidget(summary, 1)
            layout.addLayout(row)

        self.status = QLabel("勾选需要的 Mod，点「开始下载并启动」即可。")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.result_label = QLabel("")
        self.result_label.setObjectName("Muted")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.skip_button = QPushButton("跳过，直接启动")
        self.skip_button.setObjectName("Ghost")
        self.skip_button.clicked.connect(self._on_skip)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.primary_button = QPushButton("开始下载并启动")
        self.primary_button.setObjectName("Primary")
        self.primary_button.clicked.connect(self._on_primary)
        buttons.addWidget(self.primary_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ state
    @property
    def checked_slugs(self) -> list[str]:
        """Slugs the user left ticked, in display order."""
        return [
            candidate.recommendation.slug
            for candidate, box in zip(self._candidates, self.checkboxes)
            if box.isChecked() and box.isEnabled()
        ]

    @property
    def selected_items(self) -> list[optimizer.Candidate]:
        wanted = set(self.checked_slugs)
        return [item for item in self._missing if item.recommendation.slug in wanted]

    # ---------------------------------------------------------------- actions
    def _on_primary(self) -> None:
        if self._finished:
            self.accept()
            return
        if self._busy:
            return
        items = self.selected_items
        if not items:
            self._finished = True
            self.result_summary = "推荐 Mod 均已安装，无需下载。"
            self.status.setText(self.result_summary)
            self.log_requested.emit("info", "启动前优化：推荐 Mod 全部已安装。")
            self.primary_button.setText("继续启动")
            return
        self._busy = True
        self.primary_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.primary_button.setText("正在下载…")
        self.status.setText(f"准备下载 {len(items)} 个 Mod…")

        def work(progress, cancelled):
            return optimizer.install(
                self.plan,
                items,
                notify=lambda text: progress(text, 0, len(items)),
                should_stop=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_download_done,
            on_failed=self._on_download_failed,
            on_progress=self._on_progress,
        )

    def _on_skip(self) -> None:
        if self._busy:
            return
        self.result_summary = "已跳过启动前优化。"
        self.log_requested.emit("muted", self.result_summary)
        self.reject()

    def _on_progress(self, message: str, _done: int, _total: int) -> None:
        self.status.setText(message)

    def _on_download_done(self, result: optimizer.InstallResult) -> None:
        self._busy = False
        self._finished = True
        self.result_summary = result.summary()
        self.failures = list(result.failed)
        for name, path in result.installed:
            self.log_requested.emit("success", f"已从 Modrinth 安装 {name}：{path.name}")
        for name, path in result.skipped:
            self.log_requested.emit("muted", f"{name} 已安装，跳过（{path.name}）")
        for note in result.notes:
            self.log_requested.emit("warn", note)
        for name, reason in result.failed:
            self.log_requested.emit("error", f"{name} 下载失败：{reason}")
        lines = [self.result_summary]
        if result.failed:
            lines.append("以下 Mod 未安装，游戏仍会正常启动：")
            lines.append(result.failure_text())
        self.result_label.setText("\n".join(lines))
        self.result_label.setVisible(True)
        self.status.setText("下载结束，点「继续启动」进入游戏。")
        self.primary_button.setText("继续启动")
        self.primary_button.setEnabled(True)
        self.skip_button.setEnabled(True)

    def _on_download_failed(self, message: str) -> None:
        self._busy = False
        self._finished = True
        self.result_summary = f"下载任务失败：{message}（游戏仍会正常启动）"
        self.log_requested.emit("error", self.result_summary)
        self.result_label.setText(self.result_summary)
        self.result_label.setVisible(True)
        self.status.setText("下载失败，点「继续启动」直接进入游戏。")
        self.primary_button.setText("继续启动")
        self.primary_button.setEnabled(True)
        self.skip_button.setEnabled(True)
