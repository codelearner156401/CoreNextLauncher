"""Microsoft device-code login dialog and background worker."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import auth
from ..account import Account

# Workers detached from a closed dialog stay referenced here until they finish.
_ORPHAN_WORKERS: set = set()


class DeviceLoginWorker(QThread):
    code_ready = Signal(dict)
    status = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.client_id = client_id
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            code = auth.request_device_code(self.client_id)
            if self._cancel.is_set():
                return
            self.code_ready.emit(code)

            interval = max(5, int(code.get("interval", 5) or 5))
            expires = max(60, int(code.get("expires_in", 900) or 900))
            deadline = time.time() + expires
            token = None
            while time.time() < deadline:
                if self._cancel.wait(interval):
                    return
                _status, payload = auth.poll_device_token(self.client_id, code["device_code"])
                if payload.get("access_token"):
                    token = payload
                    break
                error = payload.get("error")
                if error == "authorization_pending":
                    self.status.emit("等待浏览器中完成授权…")
                elif error == "slow_down":
                    interval += 5
                    self.status.emit("等待授权…")
                elif error == "expired_token":
                    raise auth.AuthError("设备码已过期，请重新开始登录")
                elif error == "access_denied":
                    raise auth.AuthError("登录已被取消")
                elif error:
                    raise auth.AuthError(auth.error_text(payload, "登录失败"))
            if token is None:
                raise auth.AuthError("设备码已过期，请重新开始登录")

            self.status.emit("正在验证 Xbox / Minecraft 账号…")
            account = auth.complete_login(token, self.client_id, self.status.emit)
            if not self._cancel.is_set():
                self.succeeded.emit(account)
        except Exception as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))


class AccountDialog(QDialog):
    logged_in = Signal(object)
    logged_out = Signal()

    def __init__(self, parent, client_id: str, account: Account | None = None) -> None:
        super().__init__(parent)
        self.client_id = client_id
        self.account = account
        self.worker: DeviceLoginWorker | None = None
        self._started = False
        self._closing = False
        self._verification_uri = ""

        self.setObjectName("AccountDialog")
        self.setWindowTitle("正版账号 · Microsoft")
        self.resize(620, 500)
        self.setMinimumSize(540, 440)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(12)

        title = QLabel("正版账号")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        subtitle = QLabel("默认启动方式：Microsoft 正版登录。登录在 Microsoft 官方页面完成，启动器不会接触你的密码。")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self._build_intro_page()
        self._build_code_page()
        self._build_account_page()

        self.status = QLabel("")
        self.status.setObjectName("AuthStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        if account is not None:
            self._show_account(account)
        else:
            self.stack.setCurrentWidget(self.intro_page)

    # ------------------------------------------------------------------ UI
    def _build_intro_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(12)
        heading = QLabel("使用 Microsoft 账号登录")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        description = QLabel(
            "点击下方按钮后，启动器会申请一个设备码并自动打开 microsoft.com/link。\n"
            "在浏览器里输入屏幕上的代码并登录，即可完成 Xbox Live 与 Minecraft 正版验证。"
        )
        description.setObjectName("Muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)
        self.login_button = QPushButton("开始 Microsoft 登录")
        self.login_button.setObjectName("Primary")
        self.login_button.clicked.connect(self.start_login)
        layout.addWidget(self.login_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.intro_page = page
        self.stack.addWidget(page)

    def _build_code_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)
        hint = QLabel("在浏览器中输入以下代码")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self.code_label = QLabel("········")
        self.code_label.setObjectName("DeviceCode")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.code_label)

        self.code_hint = QLabel("已打开 microsoft.com/link；若浏览器没有打开，请点击下方按钮。")
        self.code_hint.setObjectName("Hint")
        self.code_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_hint.setWordWrap(True)
        layout.addWidget(self.code_hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.open_button = QPushButton("打开登录页面")
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(self._open_browser)
        row.addWidget(self.open_button)
        self.copy_button = QPushButton("复制代码")
        self.copy_button.setObjectName("Ghost")
        self.copy_button.clicked.connect(self._copy_code)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("Ghost")
        self.cancel_button.clicked.connect(self._cancel_login)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

        self.retry_button = QPushButton("重新获取设备码")
        self.retry_button.setObjectName("Primary")
        self.retry_button.clicked.connect(self.start_login)
        self.retry_button.hide()
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        self.code_page = page
        self.stack.addWidget(page)

    def _build_account_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        self.account_name = QLabel("—")
        self.account_name.setObjectName("AccountName")
        layout.addWidget(self.account_name)
        self.account_uuid = QLabel("")
        self.account_uuid.setObjectName("Muted")
        self.account_uuid.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.account_uuid)
        mode = QLabel("启动方式：Microsoft 正版登录")
        mode.setObjectName("Hint")
        layout.addWidget(mode)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.relogin_button = QPushButton("重新登录")
        self.relogin_button.setObjectName("Ghost")
        self.relogin_button.clicked.connect(self.start_login)
        row.addWidget(self.relogin_button)
        self.logout_button = QPushButton("退出登录")
        self.logout_button.setObjectName("DangerGhost")
        self.logout_button.clicked.connect(self._logout)
        row.addWidget(self.logout_button)
        row.addStretch(1)
        close = QPushButton("完成")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.account_page = page
        self.stack.addWidget(page)

    # ------------------------------------------------------------- behaviour
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.account is None and not self._started:
            QTimer.singleShot(250, self.start_login)

    def start_login(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._started = True
        self.retry_button.hide()
        self.code_label.setText("········")
        self.open_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.stack.setCurrentWidget(self.code_page)
        self.status.setProperty("level", "running")
        self.status.setText("正在联系 Microsoft 申请设备码…")
        self._repolish_status()

        worker = DeviceLoginWorker(self.client_id, None)
        worker.code_ready.connect(self._on_code_ready)
        worker.status.connect(self._on_worker_status)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.worker = worker
        worker.start()

    def _worker_finished(self, worker: DeviceLoginWorker) -> None:
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def _on_code_ready(self, code: dict) -> None:
        if self._closing:
            return
        self._verification_uri = str(code.get("verification_uri", "https://www.microsoft.com/link"))
        self.code_label.setText(str(code.get("user_code", "")))
        self.open_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.status.setProperty("level", "running")
        self.status.setText("请在浏览器中输入代码并完成登录，此窗口会自动等待。")
        self._repolish_status()
        self._open_browser()

    def _on_worker_status(self, text: str) -> None:
        if not self._closing:
            self.status.setProperty("level", "running")
            self.status.setText(text)
            self._repolish_status()

    def _on_success(self, account: Account) -> None:
        if self._closing:
            return
        self.account = account
        self.logged_in.emit(account)
        self._show_account(account)
        self.status.setProperty("level", "success")
        self.status.setText(f"登录成功：{account.name}")
        self._repolish_status()
        QTimer.singleShot(800, self.accept)

    def _on_failure(self, message: str) -> None:
        if self._closing:
            return
        self.status.setProperty("level", "error")
        self.status.setText(message)
        self._repolish_status()
        self.retry_button.show()
        self.stack.setCurrentWidget(self.code_page)

    def _show_account(self, account: Account) -> None:
        self.account_name.setText(account.name)
        self.account_uuid.setText(f"UUID：{account.uuid}")
        self.stack.setCurrentWidget(self.account_page)

    def _open_browser(self) -> None:
        url = self._verification_uri or "https://www.microsoft.com/link"
        QDesktopServices.openUrl(QUrl(url))

    def _copy_code(self) -> None:
        QApplication.clipboard().setText(self.code_label.text())

    def _detach_worker(self) -> None:
        """Never let a running QThread be destroyed with its dialog.

        Cancelling only sets an event; the worker may still be inside a network
        request. Detach it, disconnect its UI signals and keep a reference until
        its finished signal fires.
        """
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        worker.cancel()
        try:
            worker.code_ready.disconnect(self._on_code_ready)
            worker.status.disconnect(self._on_worker_status)
            worker.succeeded.disconnect(self._on_success)
            worker.failed.disconnect(self._on_failure)
            worker.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        worker.setParent(None)
        if worker.isFinished():
            worker.deleteLater()
            return
        _ORPHAN_WORKERS.add(worker)

        def cleanup(w=worker) -> None:
            _ORPHAN_WORKERS.discard(w)
            w.deleteLater()

        worker.finished.connect(cleanup)

    def _cancel_login(self) -> None:
        self._detach_worker()
        self.stack.setCurrentWidget(self.intro_page)
        self.status.setProperty("level", "muted")
        self.status.setText("已取消登录。")
        self._repolish_status()

    def _logout(self) -> None:
        self.account = None
        self.logged_out.emit()
        self.accept()

    def _repolish_status(self) -> None:
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)

    def reject(self) -> None:  # noqa: D401 - QDialog override
        self._closing = True
        self._detach_worker()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._detach_worker()
        super().closeEvent(event)


def shutdown_workers(timeout: float = 3.0) -> None:
    """Stop detached login workers before the Qt application exits."""
    for worker in list(_ORPHAN_WORKERS):
        try:
            worker.cancel()
            if not worker.wait(int(timeout * 1000)):
                worker.terminate()
                worker.wait(1000)
        except RuntimeError:
            pass
        finally:
            _ORPHAN_WORKERS.discard(worker)
