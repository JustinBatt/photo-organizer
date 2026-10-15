"""The album hierarchy as a tree (used by Browse, the Albums tab and the album dialog)."""
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView

from .photo_grid import PHOTO_MIME

ALBUM_ROLE = Qt.ItemDataRole.UserRole  # (album id, full name)


class AlbumTree(QTreeWidget):
    # photos dragged from the grid and dropped on an album: (album id, full name, paths)
    photos_dropped = Signal(int, str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setIndentation(16)
        self._drop_item: Optional[QTreeWidgetItem] = None
        self._collapsed: set[str] = set()
        self.itemCollapsed.connect(lambda it: self._collapsed.add(self._name(it).lower()))
        self.itemExpanded.connect(lambda it: self._collapsed.discard(self._name(it).lower()))

    @staticmethod
    def _name(item: QTreeWidgetItem) -> str:
        return item.data(0, ALBUM_ROLE)[1]

    def current_album(self) -> Optional[tuple[int, str]]:
        item = self.currentItem()
        return item.data(0, ALBUM_ROLE) if item is not None else None

    def load(self, albums: list[dict], text_filter: str = ""):
        """(Re)build the tree, keeping the selection and which branches were collapsed."""
        keep = self.current_album()
        needle = text_filter.strip().lower()
        self.blockSignals(True)
        self.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        select_item = None
        matches = {a["name"].lower() for a in albums if not needle or needle in a["name"].lower()}
        # keep the parents of every match so the hierarchy stays visible while filtering
        visible = set(matches)
        for name in matches:
            parts = name.split("/")
            visible.update("/".join(parts[:i]) for i in range(1, len(parts)))
        for a in albums:
            key = a["name"].lower()
            if key not in visible:
                continue
            leaf = a["name"].rpartition("/")[2]
            item = QTreeWidgetItem([f"{leaf}   ({a['total_count']:,})"])
            item.setData(0, ALBUM_ROLE, (a["id"], a["name"]))
            direct = a["photo_count"]
            item.setToolTip(0, f"{a['name']}\n{direct:,} photo(s) directly in it, "
                               f"{a['total_count']:,} including sub-albums")
            parent = nodes.get(key.rpartition("/")[0])
            if parent is not None:
                parent.addChild(item)
            else:
                self.addTopLevelItem(item)
            nodes[key] = item
            if keep and a["id"] == keep[0]:
                select_item = item
        for key, item in nodes.items():
            item.setExpanded(key not in self._collapsed or bool(needle))
        if select_item is not None:
            self.setCurrentItem(select_item)
        self.blockSignals(False)
        return select_item is not None

    # --- drag & drop target -------------------------------------------------

    def _accepts(self, event) -> bool:
        return event.mimeData().hasFormat(PHOTO_MIME)

    def _set_drop_item(self, item):
        if item is self._drop_item:
            return
        if self._drop_item is not None:
            self._drop_item.setBackground(0, QBrush())
        self._drop_item = item
        if item is not None:
            item.setBackground(0, QBrush(QColor(46, 134, 193, 120)))

    def dragEnterEvent(self, event):
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if self._accepts(event) and item is not None:
            self._set_drop_item(item)
            event.acceptProposedAction()
        else:
            self._set_drop_item(None)
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_item(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        self._set_drop_item(None)
        if item is None or not self._accepts(event):
            event.ignore()
            return
        raw = bytes(event.mimeData().data(PHOTO_MIME)).decode("utf-8")
        paths = [p for p in raw.split("\n") if p]
        album_id, name = item.data(0, ALBUM_ROLE)
        event.acceptProposedAction()
        if paths:
            self.photos_dropped.emit(album_id, name, paths)
