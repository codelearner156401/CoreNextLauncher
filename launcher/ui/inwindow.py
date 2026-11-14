"""Route QMessageBox statics into the main window's page stack."""

from __future__ import annotations

import os
from pathlib import Path as _Path

from PySide6.QtCore import QEventLoop
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

_HOST: QWidget | None = None
_INSTALLED = False


def set_host(host: QWidget | None) -> None:
    global _HOST
    _HOST = host


def _resolve(parent) -> QWidget | None:
    if _HOST is not None:
        return _HOST
    if isinstance(parent, QWidget):
        window = parent.window()
        if hasattr(window, "run_dialog"):
            return window
    return None


def _show(parent, title, text, icon, buttons, default) -> QMessageBox.StandardButton:
    host = _resolve(parent)
    if host is None or not hasattr(host, "tabs"):
        return QMessageBox.StandardButton.Ok
    box = QMessageBox(parent)
    box.setWindowTitle(str(title))
    box.setIcon(icon)
    box.setText(str(text))
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    return _run_message(host, box, str(title))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def information(parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                    default=QMessageBox.StandardButton.NoButton):
        return _show(parent, title, text, QMessageBox.Icon.Information, buttons, default)

    def warning(parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                default=QMessageBox.StandardButton.NoButton):
        return _show(parent, title, text, QMessageBox.Icon.Warning, buttons, default)

    def critical(parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                 default=QMessageBox.StandardButton.NoButton):
        return _show(parent, title, text, QMessageBox.Icon.Critical, buttons, default)

    def question(parent, title, text,
                 buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                 default=QMessageBox.StandardButton.NoButton):
        return _show(parent, title, text, QMessageBox.Icon.Question, buttons, default)

    QMessageBox.information = staticmethod(information)
    QMessageBox.warning = staticmethod(warning)
    QMessageBox.critical = staticmethod(critical)
    QMessageBox.question = staticmethod(question)


class _MessagePage(QWidget):
    def __init__(self, title: str, text: str, informative: str, detailed: str,
                 icon, buttons, default, parent=None) -> None:
        super().__init__(parent)
        from .subpage import SubPage

        self.page = SubPage(title, parent=self)
        holder = QVBoxLayout()
        holder.setContentsMargins(22, 18, 22, 18)
        holder.setSpacing(12)
        mark = {QMessageBox.Icon.Warning: "⚠", QMessageBox.Icon.Critical: "⛔",
                QMessageBox.Icon.Question: "？"}.get(icon, "ℹ")
        glyph = QLabel(mark)
        glyph.setStyleSheet("font-size: 28px;")
        holder.addWidget(glyph)
        body = QLabel(str(text))
        body.setWordWrap(True)
        body.setMinimumWidth(360)
        holder.addWidget(body)
        if informative:
            extra = QLabel(str(informative))
            extra.setWordWrap(True)
            extra.setObjectName("Muted")
            holder.addWidget(extra)
        if detailed:
            detail = QLabel(str(detailed))
            detail.setWordWrap(True)
            detail.setObjectName("Hint")
            holder.addWidget(detail)
        holder.addSpacing(6)
        row = QHBoxLayout()
        row.addStretch(1)
        self._result = QMessageBox.StandardButton.NoButton
        label_map = {
            QMessageBox.StandardButton.Ok: "确定",
            QMessageBox.StandardButton.Yes: "是",
            QMessageBox.StandardButton.No: "否",
            QMessageBox.StandardButton.Cancel: "取消",
            QMessageBox.StandardButton.Close: "关闭",
        }
        for value in (
            QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Close,
        ):
            if buttons & value:
                button = QPushButton(label_map[value])
                button.setObjectName("Primary" if value == default else "Ghost")
                button.clicked.connect(lambda _c=False, v=value: self._finish(v))
                row.addWidget(button)
        holder.addLayout(row)
        holder.addStretch(1)
        self.page.body.addLayout(holder)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page)
        self.page.back_button.clicked.connect(
            lambda: self._finish(QMessageBox.StandardButton.NoButton if default == QMessageBox.StandardButton.No else QMessageBox.StandardButton.Cancel)
        )

    def _finish(self, value) -> None:
        self._result = value
        self.page.back_requested.emit()


def run_message_box(host, box: QMessageBox, title: str = ""):
    return _run_message(host, box, title or box.windowTitle() or "消息")


def _run_message(host, box: QMessageBox, title: str):
    page = _MessagePage(
        title,
        box.text(),
        box.informativeText(),
        box.detailedText(),
        box.icon(),
        box.standardButtons(),
        box.standardButton(box.defaultButton())
        if box.defaultButton() is not None
        else QMessageBox.StandardButton.NoButton,
    )
    loop = QEventLoop()
    page.page.back_requested.connect(loop.quit)
    host.tabs.push_page(page.page)
    loop.exec()
    host.tabs.pop_page()
    return page._result


class _InputPage(QWidget):
    def __init__(self, title: str, label: str, text: str = "", items=None, parent=None) -> None:
        super().__init__(parent)
        from .subpage import SubPage

        self.page = SubPage(title, parent=self)
        layout = self.page.body
        layout.addWidget(QLabel(label))
        self.edit = QLineEdit(text) if items is None else QComboBox()
        if items is not None:
            self.edit.addItems([str(item) for item in items])
            self.edit.setCurrentIndex(0)
        layout.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.page.back_requested.emit)
        row.addWidget(cancel)
        ok = QPushButton("确定")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.page.back_requested.emit)
        row.addWidget(ok)
        layout.addLayout(row)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page)
        self.page.back_requested.connect(self._done)
        self._ok = False
        ok.clicked.disconnect()
        ok.clicked.connect(self._accept)

    def _accept(self) -> None:
        self._ok = True
        self.page.back_requested.emit()

    def _done(self) -> None:
        pass

    def value(self) -> str:
        return self.edit.currentText() if isinstance(self.edit, QComboBox) else self.edit.text()

    def accepted(self) -> bool:
        return self._ok


def _ask(host, title: str, label: str, text: str, items=None):
    page = _InputPage(title, label, text, items)
    loop = QEventLoop()
    page.page.back_requested.connect(loop.quit)
    host.tabs.push_page(page.page)
    loop.exec()
    value = page.value()
    ok = page.accepted()
    host.tabs.pop_page()
    return value, ok


def install_input_hooks() -> None:
    if getattr(QInputDialog, "_inwindow", False):
        return
    QInputDialog._inwindow = True

    def get_text(parent, title, label, echo=QLineEdit.EchoMode.Normal, text="",
                 flags=None, input_method_hints=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "tabs"):
            return text, False
        return _ask(host, str(title), str(label), str(text))

    def get_item(parent, title, label, items, current=0, editable=True, flags=None,
                 input_method_hints=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "tabs"):
            return "", False
        value, ok = _ask(host, str(title), str(label), "", list(items))
        return value, ok

    QInputDialog.getText = staticmethod(get_text)
    QInputDialog.getItem = staticmethod(get_item)


def _patterns(text: str) -> list[str]:
    if not text:
        return ["*"]
    inner = text.split("(", 1)[1].split(")", 1)[0] if "(" in text else text
    values = [part.strip() for part in inner.replace(";", " ").split() if part.strip()]
    return values or ["*"]


def _available_roots() -> list[_Path]:
    """Existing drive roots on Windows, "/" elsewhere; never raises."""
    roots: list[_Path] = []
    if os.name == "nt":
        mask = 0
        try:
            import ctypes

            mask = int(ctypes.windll.kernel32.GetLogicalDrives())
        except Exception:
            mask = 0
        letters = [chr(65 + index) for index in range(26) if mask & (1 << index)]
        if not letters:
            letters = [
                chr(65 + index)
                for index in range(26)
                if _Path(f"{chr(65 + index)}:/").is_dir()
            ]
        # Drives that exist in the bitmask but are not ready (empty card
        # reader, CD-ROM without a disc) are not usable as a start folder.
        roots = [_Path(f"{letter}:/") for letter in letters]
        roots = [root for root in roots if root.is_dir()]
    return roots or [_Path("/")]


class _FilePage(QWidget):
    def __init__(self, host, title: str, start: str, filters: str, directory: bool, parent=None) -> None:
        super().__init__(parent)
        from .subpage import SubPage

        self.page = SubPage(title, parent=self)
        self.host = host
        self.directory = directory
        self._patterns = _patterns(filters)
        self._chosen = ""
        self._ok = False
        self.path = _Path(start).expanduser() if start else _Path.home()
        if self.path.is_file():
            self.path = self.path.parent
        if not self.path.is_dir():
            self.path = _Path.home()

        row = QHBoxLayout()
        self.drives = QComboBox()
        for root in _available_roots():
            self.drives.addItem(str(root), str(root))
        self.drives.currentIndexChanged.connect(self._drive_changed)
        row.addWidget(self.drives)
        self.path_edit = QLineEdit(str(self.path))
        self.path_edit.returnPressed.connect(self._navigate)
        self.path_edit.editingFinished.connect(self._navigate)
        row.addWidget(self.path_edit, 1)
        self.up_button = QPushButton("上级")
        self.up_button.setObjectName("Ghost")
        self.up_button.clicked.connect(self._up)
        row.addWidget(self.up_button)
        self.page.body.addLayout(row)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        self.page.body.addWidget(self.list, 1)
        controls = QHBoxLayout()
        controls.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.page.back_requested.emit)
        controls.addWidget(cancel)
        self.select_button = QPushButton("选择此文件夹" if directory else "打开")
        self.select_button.setObjectName("Primary")
        self.select_button.clicked.connect(self._select)
        controls.addWidget(self.select_button)
        self.page.body.addLayout(controls)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page)
        self._reload()

    def _reload(self) -> None:
        self.path_edit.setText(str(self.path))
        self.path_edit.setToolTip("")
        self._sync_drives()
        self.up_button.setEnabled(self.path.parent != self.path)
        self.list.clear()
        try:
            entries = sorted(self.path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                item = QListWidgetItem("📁  " + entry.name)
            else:
                import fnmatch
                if not any(fnmatch.fnmatch(entry.name.lower(), pat.lower()) for pat in self._patterns):
                    continue
                item = QListWidgetItem("📄  " + entry.name)
            item.setData(256, str(entry))
            self.list.addItem(item)

    def _sync_drives(self) -> None:
        anchor = str(self.path.anchor or "")
        index = self.drives.findData(anchor) if anchor else -1
        if index < 0:
            return
        self.drives.blockSignals(True)
        self.drives.setCurrentIndex(index)
        self.drives.blockSignals(False)

    def _drive_changed(self, index: int) -> None:
        data = self.drives.itemData(index)
        if not data:
            return
        target = _Path(str(data))
        if not target.is_dir():
            self._hint(self.drives, "该磁盘当前不可用")
            self._sync_drives()
            return
        self.path = target
        self._reload()

    def _hint(self, widget, message: str) -> None:
        widget.setToolTip(message)
        QToolTip.showText(widget.mapToGlobal(widget.rect().bottomLeft()), message, widget)

    def _restore_path(self, message: str = "路径不存在或不可用") -> None:
        self.path_edit.setText(str(self.path))
        self._hint(self.path_edit, message)

    def _navigate(self) -> None:
        """Jump to whatever the path box holds after return/focus-out."""
        raw = self.path_edit.text().strip()
        if not raw:
            self._restore_path()
            return
        target = _Path(raw).expanduser()
        if target.is_dir():
            self.path = target
        elif not self.directory and target.is_file():
            self.path = target.parent
        else:
            self._restore_path()
            return
        self._reload()

    def _up(self) -> None:
        parent = self.path.parent
        if parent == self.path:
            return
        self.path = parent
        self._reload()

    def _open(self, item) -> None:
        target = _Path(str(item.data(256)))
        if target.is_dir():
            self.path = target
            self._reload()
        elif not self.directory:
            self._chosen = str(target)
            self._ok = True
            self.page.back_requested.emit()

    def _select(self) -> None:
        if self.directory:
            raw = self.path_edit.text().strip()
            if raw:
                target = _Path(raw).expanduser()
                if not target.is_dir():
                    self._restore_path("目录不存在或不可用")
                    return
                self.path = target
                self._reload()
            self._chosen = str(self.path)
            self._ok = True
        else:
            item = self.list.currentItem()
            if item is not None:
                target = _Path(str(item.data(256)))
                if target.is_dir():
                    self.path = target
                    self._reload()
                    return
                self._chosen = str(target)
                self._ok = True
        self.page.back_requested.emit()


class _SaveFilePage(_FilePage):
    """Save-as page: browse a folder in the list, type the file name below."""

    def __init__(self, host, title: str, start: str, filters: str, parent=None) -> None:
        target = _Path(start).expanduser() if start else _Path.home()
        filename = ""
        if start and not target.is_dir():
            # Qt passes a suggested file name in the dir argument.
            filename = target.name
            folder = target.parent
            target = folder if folder.is_absolute() and folder.is_dir() else _Path.home()
        super().__init__(host, title, str(target), filters, True, parent)
        self.select_button.setText("保存")
        row = QHBoxLayout()
        row.addWidget(QLabel("文件名"))
        self.name_edit = QLineEdit(filename)
        self.name_edit.returnPressed.connect(self._select)
        row.addWidget(self.name_edit, 1)
        self.page.body.insertLayout(1, row)

    def _open(self, item) -> None:
        target = _Path(str(item.data(256)))
        if target.is_dir():
            self.path = target
            self._reload()
            return
        self.name_edit.setText(target.name)

    def _select(self) -> None:
        raw = self.path_edit.text().strip()
        folder = _Path(raw).expanduser() if raw else self.path
        if not folder.is_dir():
            self._restore_path("保存目录不存在或不可用")
            return
        name = self.name_edit.text().strip()
        if not name:
            self._hint(self.name_edit, "请先填写文件名")
            return
        if "/" in name or "\\" in name:
            self._hint(self.name_edit, "文件名不能包含路径分隔符")
            return
        target = folder / name
        if target.is_dir():
            self._hint(self.name_edit, "同名位置已经是一个文件夹")
            return
        if target.exists():
            answer = QMessageBox.question(
                self,
                "覆盖确认",
                f"{target}\n已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._chosen = str(target)
        self._ok = True
        self.page.back_requested.emit()


def _ask_file(host, title: str, start: str, filters: str, directory: bool):
    page = _FilePage(host, title, start, filters, directory)
    loop = QEventLoop()
    page.page.back_requested.connect(loop.quit)
    host.tabs.push_page(page.page)
    loop.exec()
    host.tabs.pop_page()
    return page._chosen if page._ok else "", page._ok


def _ask_save(host, title: str, start: str, filters: str):
    page = _SaveFilePage(host, title, start, filters)
    loop = QEventLoop()
    page.page.back_requested.connect(loop.quit)
    host.tabs.push_page(page.page)
    loop.exec()
    host.tabs.pop_page()
    return page._chosen if page._ok else "", page._ok


def install_file_hooks() -> None:
    if getattr(QFileDialog, "_inwindow", False):
        return
    QFileDialog._inwindow = True

    def get_open(parent, title, start="", filters="", selected_filter="", options=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "tabs"):
            return "", ""
        choice, ok = _ask_file(host, str(title), str(start), str(filters), False)
        return (choice, str(selected_filter)) if ok else ("", "")

    def get_existing(parent, title, start="", options=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "tabs"):
            return ""
        choice, ok = _ask_file(host, str(title), str(start), "", True)
        return choice if ok else ""

    def get_save(parent, title, start="", filters="", selected_filter="", options=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "tabs"):
            return "", ""
        choice, ok = _ask_save(host, str(title), str(start), str(filters))
        return (choice, str(selected_filter)) if ok else ("", "")

    QFileDialog.getOpenFileName = staticmethod(get_open)
    QFileDialog.getExistingDirectory = staticmethod(get_existing)
    QFileDialog.getSaveFileName = staticmethod(get_save)


def install_color_hooks() -> None:
    if getattr(QColorDialog, "_inwindow", False):
        return
    QColorDialog._inwindow = True

    def get_color(initial=None, parent=None, title="选择颜色", options=None):
        host = _resolve(parent)
        if host is None or not hasattr(host, "run_dialog"):
            return QColor()
        dialog = QColorDialog(QColor(initial) if initial is not None else QColor("#4f8cff"), parent)
        dialog.setWindowTitle(str(title))
        result = host.run_dialog(dialog, str(title))
        if result == QDialog.DialogCode.Accepted:
            return dialog.currentColor()
        return QColor()
    QColorDialog.getColor = staticmethod(get_color)
