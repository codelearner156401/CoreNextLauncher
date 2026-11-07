"""崩溃诊断 tab: crash analysis + AI diagnosis entry point."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services import crash as crash_service
from ...services.util import human_age
from ..ai_dialog import AIDialog, api_key_configured
from ..crash_dialog import CrashDialog
from .common import card, open_path


class DiagnoseTab(QWidget):
    log_requested = Signal(str, str)
    settings_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir: Path | None = None
        self._analysis: crash_service.CrashAnalysis | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(12)

        crash_card, crash_layout = card(
            "智能崩溃分析",
            "读取 crash-reports 中最新的报告，用正则匹配常见错误，给出大白话修复建议。",
        )
        self.report_info = QLabel("尚未分析")
        self.report_info.setObjectName("Muted")
        self.report_info.setWordWrap(True)
        self.report_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        crash_layout.addWidget(self.report_info)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.analyze_button = QPushButton("分析最新崩溃")
        self.analyze_button.setObjectName("Primary")
        self.analyze_button.clicked.connect(lambda: self.analyze())
        buttons.addWidget(self.analyze_button)
        choose = QPushButton("选择报告…")
        choose.clicked.connect(self.choose_report)
        buttons.addWidget(choose)
        open_button = QPushButton("打开 crash-reports")
        open_button.setObjectName("Ghost")
        open_button.clicked.connect(self.open_reports)
        buttons.addWidget(open_button)
        self.latest_log_button = QPushButton("分析 latest.log")
        self.latest_log_button.setObjectName("Ghost")
        self.latest_log_button.clicked.connect(self.analyze_log)
        buttons.addWidget(self.latest_log_button)
        buttons.addStretch(1)
        crash_layout.addLayout(buttons)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName("ToolReport")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("分析结果会显示在这里；也可以直接弹窗查看完整建议。")
        self.preview.setMinimumHeight(150)
        crash_layout.addWidget(self.preview, 1)
        layout.addWidget(crash_card, 3)

        ai_card, ai_layout = card(
            "AI 诊断 · DeepSeek",
            "把完整日志交给 AI，让它结合 Mod 列表给出更具体的结论。",
        )
        ai_row = QHBoxLayout()
        ai_row.setSpacing(10)
        self.ai_state = QLabel("检测中…")
        self.ai_state.setObjectName("Muted")
        ai_row.addWidget(self.ai_state, 1)
        self.ai_button = QPushButton("用 AI 分析")
        self.ai_button.setObjectName("Primary")
        self.ai_button.clicked.connect(self.open_ai)
        ai_row.addWidget(self.ai_button)
        settings_button = QPushButton("AI 设置")
        settings_button.setObjectName("Ghost")
        settings_button.clicked.connect(self.settings_requested.emit)
        ai_row.addWidget(settings_button)
        ai_layout.addLayout(ai_row)
        layout.addWidget(ai_card, 0)
        layout.addStretch(1)

    # ---------------------------------------------------------------- state
    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = Path(game_dir) if game_dir else None
        self._analysis = None
        self.refresh_info()
        self.refresh_ai_state()

    def refresh_ai_state(self) -> None:
        if api_key_configured():
            self.ai_state.setText("● DeepSeek API Key 已配置，可以开始分析")
            self.ai_state.setProperty("state", "ok")
        else:
            self.ai_state.setText("● 还没有配置 DeepSeek API Key（设置 → AI 诊断）")
            self.ai_state.setProperty("state", "bad")
        self._repolish(self.ai_state)

    def refresh_info(self) -> None:
        if self.game_dir is None:
            self.report_info.setText("尚未设置游戏目录。")
            return
        report = crash_service.latest_crash_report(self.game_dir)
        if report is None:
            self.report_info.setText(
                f"没有找到崩溃报告（{self.game_dir / 'crash-reports'}）。"
                "如果游戏只是闪退，可以尝试“分析 latest.log”。"
            )
            return
        try:
            stamp = report.stat().st_mtime
        except OSError:
            stamp = 0
        self.report_info.setText(
            f"最新崩溃报告：{report.name} · {human_age(stamp, time.time())} · {report.parent}"
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_info()
        self.refresh_ai_state()

    # ------------------------------------------------------------- analysis
    def analyze(self, report: str | Path | None = None) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        self.analyze_button.setEnabled(False)
        try:
            analysis = crash_service.analyze(self.game_dir, report)
        finally:
            self.analyze_button.setEnabled(True)
        if analysis is None:
            QMessageBox.information(
                self,
                "没有崩溃记录",
                "没有找到崩溃报告或 latest.log。\n如果游戏在启动器里直接失败，请查看日志页的报错。",
            )
            return
        self._show(analysis)

    def analyze_log(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        log = self.game_dir / "logs" / "latest.log"
        if not log.is_file():
            QMessageBox.information(self, "没有日志", f"没有找到 {log}")
            return
        analysis = crash_service.analyze(self.game_dir, log)
        if analysis is None:
            QMessageBox.information(self, "没有内容", "latest.log 是空的。")
            return
        self._show(analysis)

    def _show(self, analysis: crash_service.CrashAnalysis) -> None:
        self._analysis = analysis
        self.preview.setPlainText(crash_service.plain_text(analysis))
        if analysis.report_path is not None:
            try:
                stamp = analysis.report_path.stat().st_mtime
            except OSError:
                stamp = 0
            self.report_info.setText(
                f"{analysis.source}：{analysis.report_path.name} · {human_age(stamp, time.time())}"
            )
        self.log_requested.emit(
            "warn" if analysis.findings else "info",
            f"崩溃分析：{analysis.title}"
            + (f"（可疑 Mod：{'、'.join(analysis.suspect_mods)}）" if analysis.suspect_mods else ""),
        )
        dialog = CrashDialog(self, analysis, self.game_dir)
        dialog.exec()

    def choose_report(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        start = str(self.game_dir / "crash-reports")
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择崩溃报告", start, "日志文件 (*.txt *.log);;所有文件 (*)"
        )
        if chosen:
            self.analyze(chosen)

    def open_reports(self) -> None:
        if self.game_dir is None:
            return
        folder = self.game_dir / "crash-reports"
        if not open_path(folder, create=True):
            QMessageBox.warning(self, "无法打开", f"无法打开目录：{folder}")

    # ----------------------------------------------------------------- ai
    def open_ai(self) -> None:
        if self.game_dir is None:
            QMessageBox.warning(self, "未设置目录", "请先在设置页选择 .minecraft 目录。")
            return
        if not api_key_configured():
            QMessageBox.information(
                self,
                "还没有 API Key",
                "请先在「设置 → AI 诊断」中填入 DeepSeek API Key 并保存。",
            )
            self.settings_requested.emit()
            return
        crash_text = self._analysis.raw if self._analysis is not None else ""
        dialog = AIDialog(self, self.game_dir, crash_text)
        dialog.exec()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
