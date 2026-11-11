"""Route QMessageBox statics into the main window's page stack."""

from __future__ import annotations

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
    original = QMessageBox.information
    if host is None or not hasattr(host, "run_dialog"):
        # Fallback for standalone widget tests.
        return QMessageBox.StandardButton.Ok
    box = QMessageBox(parent)
    box.setWindowTitle(str(title))
    box.setIcon(icon)
    box.setText(str(text))
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    result = host.run_dialog(box, str(title))
    try:
        return QMessageBox.StandardButton(int(result))
    except (TypeError, ValueError):
        return QMessageBox.StandardButton.Ok


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
        self.path_edit = QLineEdit(str(self.path))
        row.addWidget(self.path_edit, 1)
        up = QPushButton("上级")
        up.setObjectName("Ghost")
        up.clicked.connect(self._up)
        row.addWidget(up)
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
        select = QPushButton("选择此文件夹" if directory else "打开")
        select.setObjectName("Primary")
        select.clicked.connect(self._select)
        controls.addWidget(select)
        self.page.body.addLayout(controls)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page)
        self._reload()

    def _reload(self) -> None:
        self.path_edit.setText(str(self.path))
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

    def _up(self) -> None:
        self.path = self.path.parent
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


def _ask_file(host, title: str, start: str, filters: str, directory: bool):
    page = _FilePage(host, title, start, filters, directory)
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

    QFileDialog.getOpenFileName = staticmethod(get_open)
    QFileDialog.getExistingDirectory = staticmethod(get_existing)


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
