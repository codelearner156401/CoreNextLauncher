"""高速下载 tab: 内核分片下载 + GitHub 镜像加速, 内核不支持时回退内置下载器."""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core_client import CoreClient
from ...paths import cache_dir
from ...services import fast_download
from ..widgets import ShimmerProgressBar
from .common import card, human_size, start_task

SPEED_REFRESH = 0.5  # 速度显示每 0.5 秒刷新一次


class DownloadTab(QWidget):
    log_requested = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_dir = ""
        self._task = None
        self._client: CoreClient | None = None
        self._cancelled = False
        self._dest_auto = True
        self._dest: Path | None = None
        self._mirrors = fast_download.load_mirrors()
        self._meter = fast_download.SpeedMeter()
        self._started_at = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(12)

        download_card, download_layout = card(
            "高速下载",
            "支持 HTTP Range 分片的并发下载；GitHub 地址可自动走镜像加速，"
            "内部分片失败时会退回单流，下载过程中写 .part 临时文件。",
        )

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/…/releases/download/v1/mod.zip")
        self.url_input.setClearButtonEnabled(True)
        download_layout.addWidget(self.url_input)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        self.dest_input = QLineEdit()
        self.dest_input.setPlaceholderText("保存到…")
        self.dest_input.setClearButtonEnabled(True)
        dest_row.addWidget(self.dest_input, 1)
        self.browse_button = QPushButton("浏览…")
        self.browse_button.clicked.connect(self.browse_dest)
        dest_row.addWidget(self.browse_button)
        download_layout.addLayout(dest_row)

        option_row = QHBoxLayout()
        option_row.setSpacing(10)
        option_row.addWidget(self._field("线程数"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(fast_download.DEFAULT_THREADS)
        self.threads_spin.setMinimumWidth(80)
        option_row.addWidget(self.threads_spin)
        self.mirror_check = QCheckBox("GitHub 镜像加速")
        self.mirror_check.setChecked(True)
        self.mirror_check.setToolTip(
            "只对 github.com / objects.githubusercontent.com / "
            "raw.githubusercontent.com 生效：镜像前缀 + 原始完整 URL"
        )
        option_row.addWidget(self.mirror_check)
        option_row.addStretch(1)
        self.start_button = QPushButton("开始下载")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_download)
        option_row.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        option_row.addWidget(self.cancel_button)
        download_layout.addLayout(option_row)

        self.progress = ShimmerProgressBar()
        self.progress.setObjectName("InstallProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("等待开始")
        download_layout.addWidget(self.progress)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("填写下载地址后点击“开始下载”。")
        self.status_label.setObjectName("InstallStatus")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.speed_label = QLabel("0 B/s")
        self.speed_label.setObjectName("Muted")
        self.speed_label.setMinimumWidth(110)
        self.speed_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        status_row.addWidget(self.speed_label)
        download_layout.addLayout(status_row)
        layout.addWidget(download_card)

        mirror_card, mirror_layout = card(
            "GitHub 镜像",
            "每行一个镜像前缀，规则是“镜像前缀 + 原始完整 URL”；"
            "下载时按顺序尝试，全部失败后再直连原始地址。",
        )
        self.mirror_edit = QPlainTextEdit()
        self.mirror_edit.setPlainText("\n".join(self._mirrors))
        self.mirror_edit.setFixedHeight(78)
        mirror_layout.addWidget(self.mirror_edit)
        mirror_row = QHBoxLayout()
        mirror_row.setSpacing(8)
        self.reset_mirror_button = QPushButton("恢复默认")
        self.reset_mirror_button.clicked.connect(self.reset_mirror_list)
        mirror_row.addWidget(self.reset_mirror_button)
        mirror_row.addStretch(1)
        self.save_mirror_button = QPushButton("保存镜像列表")
        self.save_mirror_button.clicked.connect(self.save_mirror_list)
        mirror_row.addWidget(self.save_mirror_button)
        mirror_layout.addLayout(mirror_row)
        layout.addWidget(mirror_card)
        layout.addStretch(1)

        self.url_input.textChanged.connect(self._sync_default_dest)
        self.dest_input.textEdited.connect(self._mark_dest_edited)
        self._sync_default_dest()

    @staticmethod
    def _field(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # ---------------------------------------------------------------- state
    def set_game_dir(self, game_dir: str) -> None:
        self.game_dir = game_dir or ""

    def _mark_dest_edited(self, _text: str = "") -> None:
        self._dest_auto = False

    def _default_dest(self) -> str:
        url = self.url_input.text().strip()
        name = ""
        try:
            name = Path(urllib.parse.urlsplit(url).path).name
        except ValueError:
            name = ""
        return str(cache_dir() / "downloads" / (name or "download.bin"))

    def _sync_default_dest(self) -> None:
        if self._dest_auto:
            self.dest_input.setText(self._default_dest())

    def current_mirrors(self) -> list[str]:
        self._mirrors = fast_download.parse_mirror_text(self.mirror_edit.toPlainText())
        return self._mirrors or list(fast_download.DEFAULT_MIRRORS)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_running(self, running: bool) -> None:
        for widget in (
            self.start_button,
            self.url_input,
            self.dest_input,
            self.browse_button,
            self.threads_spin,
            self.mirror_check,
        ):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    # -------------------------------------------------------------- mirrors
    def browse_dest(self) -> None:
        start = self.dest_input.text().strip() or self._default_dest()
        chosen, _ = QFileDialog.getSaveFileName(
            self, "选择保存位置", start, "所有文件 (*.*)"
        )
        if not chosen:
            return
        self._dest_auto = False
        self.dest_input.setText(chosen)
        self.log_requested.emit("info", f"高速下载：保存路径设为 {chosen}")

    def save_mirror_list(self) -> None:
        mirrors = self.current_mirrors()
        try:
            target = fast_download.save_mirrors(mirrors)
        except OSError as exc:
            self._set_status(f"镜像列表保存失败：{exc}")
            self.log_requested.emit("error", f"镜像列表保存失败：{exc}")
            return
        self.mirror_edit.setPlainText("\n".join(mirrors))
        self._set_status(f"镜像列表已保存（{len(mirrors)} 条）：{target}")
        self.log_requested.emit("success", f"镜像列表已保存到 {target}")

    def reset_mirror_list(self) -> None:
        self._mirrors = list(fast_download.DEFAULT_MIRRORS)
        self.mirror_edit.setPlainText("\n".join(self._mirrors))
        self._set_status("镜像列表已恢复为默认值，点击“保存镜像列表”写入配置。")

    # ------------------------------------------------------------- download
    def start_download(self) -> None:
        if self._task is not None:
            self._set_status("已有下载任务在进行，请等待完成或先取消。")
            return
        url = self.url_input.text().strip()
        if not url:
            self._set_status("请先填写下载地址。")
            self.log_requested.emit("warn", "高速下载：下载地址为空")
            return
        if "://" not in url:
            url = "https://" + url
            self.url_input.setText(url)
        if not url.lower().startswith(("http://", "https://")):
            self._set_status("下载地址必须以 http:// 或 https:// 开头。")
            return
        dest_text = self.dest_input.text().strip()
        if not dest_text:
            self._set_status("请选择保存路径。")
            return
        dest = Path(dest_text).expanduser()
        if dest.is_dir():
            self._set_status(f"保存路径是目录，请给出文件名：{dest}")
            return

        mirrors = self.current_mirrors()
        try:
            saved = fast_download.save_mirrors(mirrors)
            self.log_requested.emit("info", f"高速下载：镜像列表已保存到 {saved}")
        except OSError as exc:
            self.log_requested.emit("warn", f"镜像列表保存失败（不影响下载）：{exc}")

        threads = int(self.threads_spin.value())
        use_mirror = self.mirror_check.isChecked()
        requested = fast_download.apply_mirror(url, mirrors, use_mirror)
        note = "，镜像加速" if requested != url else ""

        self._dest = dest
        self._cancelled = False
        self._started_at = time.monotonic()
        self._meter.reset()
        self.speed_label.setText("0 B/s")
        self.progress.set_shimmer(True)
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.progress.setFormat("准备中…")
        self._set_running(True)
        self._set_status(f"正在下载：{requested}\n保存到 {dest}（{threads} 线程{note}）")
        self.log_requested.emit(
            "info", f"高速下载开始：{requested} -> {dest}（{threads} 线程{note}）"
        )

        last_emit = [0.0]

        def work(progress, is_cancelled):
            client = CoreClient()
            self._client = client

            def report(done: int, total: int) -> None:
                now = time.monotonic()
                finished = total > 0 and done >= total
                if not finished and now - last_emit[0] < SPEED_REFRESH:
                    return
                last_emit[0] = now
                progress("下载中", int(done), int(total))

            return fast_download.download_via(
                client,
                url,
                dest,
                sha1="",
                threads=threads,
                progress=report,
                cancel=is_cancelled,
                mirrors=mirrors,
                use_mirror=use_mirror,
            )

        task = start_task(
            self,
            work,
            on_done=self._on_done,
            on_failed=self._on_failed,
            on_progress=self._on_progress,
        )
        task.finished.connect(self._on_task_finished)
        self._task = task

    def cancel_download(self) -> None:
        if self._task is None or self._cancelled:
            return
        self._cancelled = True
        self.cancel_button.setEnabled(False)
        self._set_status("正在取消下载…")
        self.log_requested.emit("warn", "高速下载：收到取消请求")
        self._task.cancel()
        client = self._client
        if client is not None:
            try:
                client.kill()
            except Exception:
                pass

    # -------------------------------------------------------------- results
    def _update_speed(self, done: int, now: float | None = None) -> float:
        speed = self._meter.update(done, now)
        self.speed_label.setText(fast_download.format_rate(speed))
        return speed

    def _on_progress(self, message: str, done: int, total: int) -> None:
        if total > 0:
            percent = max(0, min(100, int(done * 100 / total)))
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
            self.progress.setFormat(
                f"{percent}%  {human_size(done)} / {human_size(total)}"
            )
            detail = f"{human_size(done)} / {human_size(total)}"
        else:
            self.progress.setRange(0, 0)
            self.progress.setFormat(human_size(done))
            detail = human_size(done)
        speed = self._update_speed(done)
        self._set_status(f"{message}：{detail}，速度 {fast_download.format_rate(speed)}")

    def _on_done(self, result) -> None:
        info = result if isinstance(result, dict) else {}
        path = str(info.get("path") or self._dest or "")
        engine = str(info.get("engine") or "内核分片下载")
        seconds = float(info.get("seconds") or 0.0)
        if seconds <= 0:
            seconds = max(0.0, time.monotonic() - self._started_at)
        size = int(info.get("bytes") or 0)
        average = (size / seconds) if seconds > 0 and size > 0 else 0.0
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("100%")
        self.speed_label.setText(fast_download.format_rate(average))
        note = "使用内置下载器" if engine == "内置下载器" else engine
        self._set_status(
            f"下载完成：{path}\n{human_size(size)}，用时 {seconds:.1f} 秒，"
            f"平均 {fast_download.format_rate(average)}（{note}）"
        )
        self.log_requested.emit(
            "success",
            f"高速下载完成：{path}（{human_size(size)}，{seconds:.1f} 秒，{note}）",
        )

    def _on_failed(self, message: str) -> None:
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("下载失败")
        self.speed_label.setText("0 B/s")
        self._set_status(f"下载失败：{message}")
        self.log_requested.emit("error", f"高速下载失败：{message}")

    def _on_task_finished(self) -> None:
        was_cancelled = self._cancelled
        self._task = None
        self._client = None
        self._cancelled = False
        self._set_running(False)
        if not was_cancelled:
            return
        removed = fast_download.discard_partial(self._dest) if self._dest else False
        self.progress.set_shimmer(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("已取消")
        self.speed_label.setText("0 B/s")
        detail = "临时文件已清理" if removed else "没有残留临时文件"
        self._set_status(f"已取消下载，{detail}。")
        self.log_requested.emit("warn", f"高速下载已取消（{detail}）")
