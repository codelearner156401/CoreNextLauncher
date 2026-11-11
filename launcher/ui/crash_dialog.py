"""Plain-language crash dialog shown after a failed launch."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ..services import crash as crash_service
from .ai_dialog import AIDialog, api_key_configured
from .dialog_page import run_in_window


class CrashDialog(QDialog):
    """Show what went wrong and how to fix it, without the raw stack trace."""

    def __init__(
        self,
        parent,
        analysis: crash_service.CrashAnalysis,
        game_dir: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.analysis = analysis
        self.game_dir = Path(game_dir) if game_dir else self._detect_game_dir()
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("游戏崩溃了？先别慌")
        self.resize(760, 620)
        self.setMinimumSize(620, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("游戏没有正常启动")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "下面是根据崩溃报告自动总结的原因和修复步骤，按顺序尝试即可。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        meta_bits: list[str] = []
        if analysis.game_version:
            meta_bits.append(f"Minecraft {analysis.game_version}")
        if analysis.java_version:
            meta_bits.append(f"Java {analysis.java_version}")
        meta_bits.append(analysis.source)
        if analysis.report_path:
            meta_bits.append(analysis.report_path.name)
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Hint")
        meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(meta)

        self.view = QTextBrowser()
        self.view.setObjectName("CrashView")
        self.view.setOpenExternalLinks(True)
        self.view.setHtml(self._render_html())
        layout.addWidget(self.view, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if analysis.report_path is not None:
            open_report = QPushButton("打开崩溃报告")
            open_report.setObjectName("Ghost")
            open_report.clicked.connect(self._open_report)
            buttons.addWidget(open_report)

        copy_button = QPushButton("复制修复建议")
        copy_button.setObjectName("Ghost")
        copy_button.clicked.connect(self._copy_advice)
        buttons.addWidget(copy_button)

        if api_key_configured():
            ai_button = QPushButton("AI 深入分析")
            ai_button.setObjectName("Primary")
            ai_button.clicked.connect(self._open_ai)
            buttons.addWidget(ai_button)
        else:
            hint = QLabel("在「设置 → AI 诊断」填入 Key 后，可让 AI 继续分析")
            hint.setObjectName("Hint")
            buttons.addWidget(hint)
        buttons.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _render_html(self) -> str:
        parts: list[str] = []
        for finding in self.analysis.findings:
            parts.append(
                f'<div style="margin-bottom:18px;">'
                f'<div style="font-size:15px; font-weight:700; color:#8ab4f8; '
                f'margin-bottom:6px;">{html.escape(finding.title)}</div>'
                f'<p style="margin:4px 0 8px 0;">{html.escape(finding.cause)}</p>'
                f'<p style="margin:4px 0;"><b>建议这样修：</b></p><ol style="margin:4px 0 0 18px;">'
            )
            for fix in finding.fixes:
                parts.append(f"<li>{html.escape(fix)}</li>")
            parts.append("</ol></div>")
        if self.analysis.suspect_mods:
            parts.append(
                "<p><b>可疑 Mod：</b>"
                + html.escape("、".join(self.analysis.suspect_mods))
                + "</p>"
            )
        if self.analysis.excerpt:
            parts.append("<p><b>关键日志片段：</b></p>")
            parts.append(
                '<pre style="white-space:pre-wrap; font-size:11px;">'
                + html.escape(self.analysis.excerpt)
                + "</pre>"
            )
        return "".join(parts)

    def _open_report(self) -> None:
        if self.analysis.report_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.analysis.report_path)))

    def _copy_advice(self) -> None:
        QApplication.clipboard().setText(
            crash_service.plain_text(self.analysis, include_excerpt=False)
        )

    def _open_ai(self) -> None:
        dialog = AIDialog(self, self.game_dir, self.analysis.raw)
        run_in_window(self, dialog, "AI 诊断")

    def _detect_game_dir(self) -> Path | None:
        if self.analysis.report_path is not None:
            path = self.analysis.report_path
            if path.parent.name in {"crash-reports", "logs"}:
                return path.parent.parent
            return path.parent
        return None
