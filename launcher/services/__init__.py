"""Pure-Python services behind the 百宝箱 features.

Everything in this package is framework agnostic: modules never import PySide6
(except where Qt is genuinely the right tool, which stays in ``launcher.ui``),
so they can be exercised from unit tests and from a CLI.
"""
