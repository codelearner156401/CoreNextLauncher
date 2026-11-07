"""Update dialog: show release notes, download the new exe and restart."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ..constants import APP_VERSION
from ..services import updater
from ..services.util import human_size
from .tasks import start_task
from .widgets import ShimmerProgressBar


class UpdateDialog(QDialog):
    def __init__(self, parent, info: updater.UpdateInfo, on_log=None) -> None:
        super().__init__(parent)
        self.info = info
        self.on_log = on_log
        self._busy = False
        self._downloaded: Path | None = None
        self.setObjectName("ToolboxDialog")
        self.setWindowTitle("发现新版本")
        self.resize(700, 540)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel(f"发现新版本 v{info.version}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        meta_bits = [f"当前版本 v{APP_VERSION}"]
        if info.published_at:
            meta_bits.append(f"发布时间 {info.published_at[:10]}")
        if info.asset is not None and info.asset.size:
            meta_bits.append(f"安装包 {human_size(info.asset.size)}")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Hint")
        meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(meta)

        self.notes = QTextBrowser()
        self.notes.setObjectName("CrashView")
        self.notes.setOpenExternalLinks(True)
        if info.notes.strip():
            try:
                self.notes.setMarkdown(info.notes)
            except Exception:
                self.notes.setPlainText(info.notes)
        else:
            self.notes.setPlainText("这个版本没有提供更新说明。")
        layout.addWidget(self.notes, 1)

        self.progress = ShimmerProgressBar()
        self.progress.setObjectName("InstallProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("等待下载")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel(
            "自动更新会下载新的 exe，退出后由脚本替换并重启。"
            if updater.can_self_update()
            else "当前以源码方式运行，自动替换不可用；请前往 Release 页面手动下载。"
        )
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.install_button = QPushButton("下载并安装")
        self.install_button.setObjectName("Primary")
        self.install_button.setEnabled(updater.can_self_update() and info.has_asset)
        self.install_button.clicked.connect(self.download)
        buttons.addWidget(self.install_button)

        page_button = QPushButton("打开 Release 页面")
        page_button.setObjectName("Ghost")
        page_button.clicked.connect(self._open_page)
        buttons.addWidget(page_button)
        buttons.addStretch(1)
        later = QPushButton("稍后")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        layout.addLayout(buttons)

    def _open_page(self) -> None:
        if self.info.html_url:
            QDesktopServices.openUrl(QUrl(self.info.html_url))

    def download(self) -> None:
        if self._busy or not self.info.has_asset:
            return
        self._busy = True
        self.install_button.setEnabled(False)
        self.progress.show()
        self.progress.set_shimmer(True)
        self.progress.setRange(0, 0)
        self.status.setText("正在下载更新…")

        def work(progress, cancelled):
            return updater.download_update(
                self.info,
                progress=lambda done, total: progress("", done, total),
                cancel=cancelled,
            )

        start_task(
            self,
            work,
            on_done=self._on_downloaded,
            on_failed=self._on_failed,
            on_progress=self._on_progress,
        )

    def _on_progress(self, _message: str, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
            self.progress.setFormat(f"{human_size(done)} / {human_size(total)}")
        else:
            self.progress.setRange(0, 0)

    def _on_downloaded(self, path: Path) -> None:
        self._busy = False
        self._downloaded = path
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("下载完成")
        self.install_button.setEnabled(True)
        self.install_button.setText("立即重启安装")
        try:
            self.install_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.install_button.clicked.connect(self.apply)
        self.status.setText(f"已下载到 {path}；点击“立即重启安装”完成替换。")
        if self.on_log is not None:
            self.on_log("success", f"更新包已下载：{path}")

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.progress.set_shimmer(False)
        self.progress.hide()
        self.install_button.setEnabled(True)
        self.status.setText(f"更新下载失败：{message}")
        if self.on_log is not None:
            self.on_log("error", f"更新下载失败：{message}")

    def apply(self) -> None:
        if self._downloaded is None:
            return
        answer = QMessageBox.question(
            self,
            "重启并安装更新",
            "启动器将关闭，更新脚本会在后台替换程序并自动重新打开。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            updater.apply_update(self._downloaded)
        except Exception as exc:
            QMessageBox.warning(self, "无法自动更新", str(exc))
            self._open_page()
            return
        if self.on_log is not None:
            self.on_log("info", "已启动更新脚本，正在退出启动器…")
        app = QApplication.instance()
        if app is not None:
            # Closing the main window flushes launcher_config.json before the
            # helper script replaces the executable.
            for widget in app.topLevelWidgets():
                widget.close()
            app.quit()
