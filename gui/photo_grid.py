"""The thumbnail grid shared by the Browse and Albums tabs."""
from PySide6.QtCore import Qt, QSize, QRect, QPointF, QAbstractListModel, QModelIndex, QMimeData, Signal
from PySide6.QtGui import QPixmap, QColor, QPainter, QPolygonF, QCursor
from PySide6.QtWidgets import (
    QApplication, QListView, QAbstractItemView, QStyledItemDelegate, QStyle,
    QLineEdit, QAbstractSpinBox, QComboBox,
)
import math

from .thumbnails import ThumbnailLoader

PATH_ROLE = Qt.ItemDataRole.UserRole
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1
RATING_ROLE = Qt.ItemDataRole.UserRole + 2

# Dragging photos (to an album in the sidebar) carries their paths in this format.
PHOTO_MIME = "application/x-photoorganizer-paths"

THUMB = 150
CELL_W, CELL_H = 180, 222

STAR_SIZE = 24                      # width/height of one star in the hover bar
STAR_GOLD = QColor(255, 196, 0)
STAR_EMPTY = QColor(210, 210, 210)


def star_bar_rect(cell_rect: QRect) -> QRect:
    """The strip along the bottom of the thumbnail where the 5 hover stars sit."""
    inner = cell_rect.adjusted(4, 4, -4, -4)
    width = STAR_SIZE * 5
    return QRect(inner.x() + (inner.width() - width) // 2, inner.y() + THUMB - STAR_SIZE - 2,
                 width, STAR_SIZE)


def star_at(cell_rect: QRect, pos) -> int:
    """1-5 if `pos` is over that star of the hover bar, otherwise 0."""
    bar = star_bar_rect(cell_rect)
    if not bar.contains(pos):
        return 0
    return min(5, (pos.x() - bar.x()) // STAR_SIZE + 1)


def _star_polygon(cx: float, cy: float, r: float) -> QPolygonF:
    pts = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.45
        ang = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
    return QPolygonF(pts)


def draw_stars(painter: QPainter, x: float, y: float, size: float, filled: int, total: int = 5):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for i in range(total):
        poly = _star_polygon(x + size * i + size / 2, y + size / 2, size * 0.42)
        painter.setPen(QColor(120, 90, 0) if i < filled else QColor(150, 150, 150))
        painter.setBrush(STAR_GOLD if i < filled else STAR_EMPTY)
        painter.drawPolygon(poly)


class PhotoListModel(QAbstractListModel):
    """Holds only (path, filename, folder) per photo; thumbnails come from the
    loader on demand, so 65,000+ rows cost almost nothing until scrolled to."""

    def __init__(self, loader: ThumbnailLoader, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._rows: list[tuple[str, str, str]] = []
        self._index: dict[str, int] = {}
        self._ratings: dict[str, int] = {}
        loader.loaded.connect(self._on_thumbnail_loaded)

    def rows(self) -> list[tuple[str, str, str]]:
        return self._rows

    def set_rows(self, db_rows):
        self.beginResetModel()
        db_rows = list(db_rows)
        self._rows = [(r["path"], r["filename"], r["folder"]) for r in db_rows]
        self._index = {p: i for i, (p, _, _) in enumerate(self._rows)}
        self._ratings = {}
        if db_rows and "rating" in db_rows[0].keys():
            self._ratings = {r["path"]: r["rating"] for r in db_rows if r["rating"]}
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

    def rating_of(self, path: str) -> int:
        return self._ratings.get(path, 0)

    def set_rating(self, path: str, rating: int):
        if rating:
            self._ratings[path] = rating
        else:
            self._ratings.pop(path, None)
        row = self._index.get(path)
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [RATING_ROLE])

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
        if role == RATING_ROLE:
            return self._ratings.get(path, 0)
        return None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base

    def mimeTypes(self):
        return [PHOTO_MIME]

    def mimeData(self, indexes):
        paths = [self._rows[i.row()][0] for i in sorted(indexes, key=lambda i: i.row())
                 if i.isValid()]
        mime = QMimeData()
        mime.setData(PHOTO_MIME, "\n".join(paths).encode("utf-8"))
        return mime
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

        self._paint_stars(painter, option, index)
        painter.restore()

    def _paint_stars(self, painter, option, index):
        view = option.widget
        if view is None or not getattr(view, "rating_enabled", False):
            return
        rating = index.data(RATING_ROLE) or 0
        hovered = getattr(view, "hover_row", -1) == index.row()
        if hovered:
            # The pop-up: a dark strip of 5 stars; the one under the mouse (and those
            # before it) light up as a preview of the rating a click would give.
            bar = star_bar_rect(option.rect)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(bar.adjusted(-4, -1, 4, 1), 6, 6)
            shown = view.hover_star or rating
            draw_stars(painter, bar.x(), bar.y(), STAR_SIZE, shown)
        elif rating:
            inner = option.rect.adjusted(4, 4, -4, -4)
            small = 14
            draw_stars(painter, inner.x() + 3, inner.y() + THUMB - small - 3, small, rating, total=rating)


class PhotoView(QListView):
    """The thumbnail grid. Hovering a photo pops up 5 stars; click one, or press
    1-5 (0 clears), to rate the photo under the mouse."""

    # (paths, rating) - the owning tab saves it and updates the model
    rating_requested = Signal(list, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rating_enabled = True
        self.hover_row = -1
        self.hover_star = 0
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._refresh_hover_from_cursor())

    # --- hover tracking ------------------------------------------------------

    def _set_hover(self, row: int, star: int):
        if row == self.hover_row and star == self.hover_star:
            return
        for r in {self.hover_row, row}:
            if r >= 0:
                self.viewport().update(self.visualRect(self.model().index(r)))
        self.hover_row, self.hover_star = row, star

    def _update_hover_at(self, pos):
        index = self.indexAt(pos)
        if index.isValid():
            self._set_hover(index.row(), star_at(self.visualRect(index), pos))
        else:
            self._set_hover(-1, 0)

    def _refresh_hover_from_cursor(self):
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(pos) and self.viewport().underMouse():
            self._update_hover_at(pos)

    def mouseMoveEvent(self, event):
        self._update_hover_at(event.position().toPoint())
        # Typing 1-5 should work as soon as the mouse is over the grid - unless
        # the user is in the middle of typing into a text box.
        if not isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox, QComboBox)):
            if not self.hasFocus():
                self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._set_hover(-1, 0)
        super().leaveEvent(event)

    # --- rating ----------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rating_enabled:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                star = star_at(self.visualRect(index), event.position().toPoint())
                if star:
                    path = self.model().row_data(index.row())[0]
                    # clicking the star the photo already has clears the rating
                    new = 0 if self.model().rating_of(path) == star else star
                    self.rating_requested.emit([path], new)
                    event.accept()
                    return  # a star click is not a click on the photo
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.rating_enabled and star_at(
            self.visualRect(self.indexAt(event.position().toPoint())), event.position().toPoint()
        ):
            self.mousePressEvent(event)   # a quick second click on a star is just another rating
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if (self.rating_enabled and Qt.Key.Key_0 <= key <= Qt.Key.Key_5
                and not (event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier)):
            if self.hover_row >= 0:
                paths = [self.model().row_data(self.hover_row)[0]]
            else:
                paths = selected_paths(self)
            if paths:
                self.rating_requested.emit(paths, key - Qt.Key.Key_0)
            event.accept()
            return
        super().keyPressEvent(event)


def make_photo_view(model, parent=None) -> PhotoView:
    """A virtualized, multi-select thumbnail grid for a PhotoListModel."""
    view = PhotoView(parent)
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
    view.setDragEnabled(True)
    view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
    view.setDefaultDropAction(Qt.DropAction.CopyAction)
    return view


def selected_paths(view: QListView) -> list[str]:
    """Paths of the selected photos, in grid order."""
    rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()})
    model = view.model()
    return [model.row_data(r)[0] for r in rows]
