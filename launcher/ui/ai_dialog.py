"""DeepSeek AI diagnosis dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..config import load_config
from ..services import ai_diag
from ..services.ai_diag import api_key_configured, load_api_key


class AIDialog(QDialog):
    def __init__(
        self,
        parent,
        game_dir: str | Path | None,
        crash_text: str = "",
        auto_start: bool = True,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir) if game_dir else None
        self.crash_text = crash_text
        self._busy = False
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("AI 日志诊断 · DeepSeek")
        self.resize(780, 640)
        self.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("AI 日志诊断")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "启动器会把最新日志、崩溃报告、Mod 列表与 Java/内存设置发送给 DeepSeek，"
            "由它给出针对性的修复建议。日志里可能包含本机路径，请自行确认是否介意。"
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.status = QLabel("")
        self.status.setObjectName("AuthStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.output = QPlainTextEdit()
        self.output.setObjectName("ToolReport")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("AI 分析结果会显示在这里…")
        layout.addWidget(self.output, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = QPushButton("开始分析")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start)
        buttons.addWidget(self.start_button)
        self.copy_button = QPushButton("复制结果")
        self.copy_button.setObjectName("Ghost")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        if not api_key_configured():
            self.status.setText("还没有填写 DeepSeek API Key，请到「设置 → AI 诊断」保存后再试。")
            self.status.setProperty("level", "error")
            self.start_button.setEnabled(False)
        elif self.game_dir is None or not self.game_dir.is_dir():
            self.status.setText("游戏目录不可用，无法收集日志。")
            self.status.setProperty("level", "error")
            self.start_button.setEnabled(False)
        elif auto_start:
            self.start()

    # ------------------------------------------------------------- analysis
    def start(self) -> None:
        if self._busy:
            return
        config = load_config()
        api_key = load_api_key()
        if not api_key:
            self.status.setText("缺少 DeepSeek API Key。")
            self.status.setProperty("level", "error")
            return
        self._busy = True
        self.start_button.setEnabled(False)
        self.start_button.setText("分析中…")
        self.status.setProperty("level", "running")
        self.status.setText("正在请求 DeepSeek 分析…")
        self._repolish()
        self.output.setPlainText("")

        def work(progress, cancelled):
            return ai_diag.diagnose(
                api_key,
                config.ai_model,
                self.game_dir,
                self.crash_text,
                progress=lambda text: progress(text, 0, 0),
            )

        from ..tasks import start_task

        start_task(
            self,
            work,
            on_done=self._on_done,
            on_failed=self._on_failed,
            on_progress=lambda text, _done, _total: self.status.setText(text),
        )

    def _on_done(self, diagnosis: ai_diag.Diagnosis) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("重新分析")
        self.status.setProperty("level", "success")
        usage = diagnosis.usage or {}
        tokens = ""
        if usage:
            tokens = f" · tokens {int(usage.get('total_tokens') or 0)}"
        self.status.setText(f"分析完成（{diagnosis.model}{tokens}）")
        self._repolish()
        self.output.setPlainText(diagnosis.text)
        self.copy_button.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText("重试")
        self.status.setProperty("level", "error")
        self.status.setText(message)
        self._repolish()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.output.toPlainText())

    def _repolish(self) -> None:
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def reject(self) -> None:  # noqa: D401 - QDialog override
        super().reject()
