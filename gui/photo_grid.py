"""The thumbnail grid shared by the Browse and Albums tabs."""
from PySide6.QtCore import Qt, QSize, QAbstractListModel, QModelIndex
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListView, QAbstractItemView, QStyledItemDelegate, QStyle

from .thumbnails import ThumbnailLoader

PATH_ROLE = Qt.ItemDataRole.UserRole
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1

THUMB = 150
CELL_W, CELL_H = 180, 222


class PhotoListModel(QAbstractListModel):
    """Holds only (path, filename, folder) per photo; thumbnails come from the
    loader on demand, so 65,000+ rows cost almost nothing until scrolled to."""

    def __init__(self, loader: ThumbnailLoader, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._rows: list[tuple[str, str, str]] = []
        self._index: dict[str, int] = {}
        loader.loaded.connect(self._on_thumbnail_loaded)

    def rows(self) -> list[tuple[str, str, str]]:
        return self._rows

    def set_rows(self, db_rows):
        self.beginResetModel()
        self._rows = [(r["path"], r["filename"], r["folder"]) for r in db_rows]
        self._index = {p: i for i, (p, _, _) in enumerate(self._rows)}
        self.endResetModel()

    def remove_where(self, predicate) -> int:
        before = len(self._rows)
        self.beginResetModel()
        self._rows = [r for r in self._rows if not predicate(r)]
        self._index = {p: i for i, (p, _, _) in enumerate(self._rows)}
        self.endResetModel()
        return before - len(self._rows)

    def has_path(self, path: str) -> bool:
        return path in self._index

    def row_data(self, row: int) -> tuple[str, str, str]:
        return self._rows[row]

    # --- Qt model interface ------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        path, filename, folder = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return filename
        if role == Qt.ItemDataRole.DecorationRole:
            return self._loader.pixmap_or_request(path)
        if role == Qt.ItemDataRole.ToolTipRole:
            return path
        if role == PATH_ROLE:
            return path
        if role == FOLDER_ROLE:
            return folder
        return None

    def _on_thumbnail_loaded(self, path: str):
        row = self._index.get(path)
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class PhotoDelegate(QStyledItemDelegate):
    """Draws a centred thumbnail, the file name, and the (tail of the) folder path."""

    def sizeHint(self, option, index):
        return QSize(CELL_W, CELL_H)

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())

        rect = option.rect.adjusted(4, 4, -4, -4)
        pm = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            x = rect.x() + (rect.width() - pm.width()) // 2
            y = rect.y() + (THUMB - pm.height()) // 2
            painter.drawPixmap(x, y, pm)

        text_color = option.palette.highlightedText().color() if selected else option.palette.text().color()
        fm = painter.fontMetrics()
        line_h = fm.height()
        top = rect.y() + THUMB + 6

        painter.setPen(text_color)
        name = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect.x(), top, rect.width(), line_h, Qt.AlignmentFlag.AlignCenter, name)

        folder_color = text_color
        folder_color.setAlpha(170)
        painter.setPen(folder_color)
        folder = fm.elidedText(index.data(FOLDER_ROLE) or "", Qt.TextElideMode.ElideLeft, rect.width())
        painter.drawText(rect.x(), top + line_h + 2, rect.width(), line_h, Qt.AlignmentFlag.AlignCenter, folder)
        painter.restore()


def make_photo_view(model, parent=None) -> QListView:
    """A virtualized, multi-select thumbnail grid for a PhotoListModel."""
    view = QListView(parent)
    view.setModel(model)
    view.setItemDelegate(PhotoDelegate(view))
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setMovement(QListView.Movement.Static)
    view.setUniformItemSizes(True)
    view.setGridSize(QSize(CELL_W, CELL_H))
    view.setSpacing(0)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setMouseTracking(True)
    return view


def selected_paths(view: QListView) -> list[str]:
    """Paths of the selected photos, in grid order."""
    rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()})
    model = view.model()
    return [model.row_data(r)[0] for r in rows]
