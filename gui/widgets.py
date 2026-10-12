from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def wrappable_path(path: str) -> str:
    """Let a long path wrap inside a narrow label (adds invisible break points)."""
    return path.replace("\\", "\\​").replace("/", "/​")
