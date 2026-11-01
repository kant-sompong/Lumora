"""Line icons drawn with ``QPainter`` for the Qt (Windows) frontend.

The Linux build uses the desktop's symbolic icon theme, which does not exist on
Windows.  Drawing the same shapes here keeps the two frontends visually
identical and removes any dependency on image files or an icon theme.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

#: Every icon is authored on this square grid and scaled from it.
VIEWBOX = 24.0


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _grid(p: QPainter) -> None:
    for x, y in ((4, 4), (13.5, 4), (4, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(QRectF(x, y, 6.5, 6.5), 1.4, 1.4)


def _library(p: QPainter) -> None:
    # A gamepad: rounded body with two grips.
    body = QPainterPath()
    body.moveTo(8, 8)
    body.cubicTo(12, 6.4, 13, 9.2, 16, 9.6)
    body.cubicTo(19.4, 10.1, 20.6, 12.4, 20.2, 15.1)
    body.cubicTo(19.8, 17.8, 18.2, 18.6, 16.4, 17.4)
    body.cubicTo(14.9, 16.4, 14.2, 15.4, 12, 15.4)
    body.cubicTo(9.8, 15.4, 9.1, 16.4, 7.6, 17.4)
    body.cubicTo(5.8, 18.6, 4.2, 17.8, 3.8, 15.1)
    body.cubicTo(3.4, 12.4, 4.6, 10.1, 8, 9.6)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(8.2, 12.4), QPointF(8.2, 14.2))
    p.drawLine(QPointF(7.3, 13.3), QPointF(9.1, 13.3))
    p.drawEllipse(QPointF(15.8, 12.7), 0.9, 0.9)
    p.drawEllipse(QPointF(17.4, 14.2), 0.9, 0.9)


def _downloads(p: QPainter) -> None:
    p.drawLine(QPointF(12, 3.5), QPointF(12, 14.5))
    p.drawLine(QPointF(7.5, 10.2), QPointF(12, 14.7))
    p.drawLine(QPointF(16.5, 10.2), QPointF(12, 14.7))
    p.drawLine(QPointF(4, 19.5), QPointF(20, 19.5))


def _media(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 6.5, 17, 13.5), 2.2, 2.2)
    p.drawLine(QPointF(3.8, 11), QPointF(20.2, 11))
    p.drawLine(QPointF(7.5, 6.4), QPointF(9.4, 3.4))
    p.drawLine(QPointF(12, 6.4), QPointF(13.9, 3.4))
    p.drawLine(QPointF(16.5, 6.4), QPointF(18.4, 3.4))
    p.drawLine(QPointF(8.6, 11), QPointF(8.6, 20))
    p.drawLine(QPointF(15.4, 11), QPointF(15.4, 20))


def _settings(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 3.0, 3.0)
    for angle in range(0, 360, 45):
        p.save()
        p.translate(12, 12)
        p.rotate(angle)
        p.drawLine(QPointF(0, -6.2), QPointF(0, -8.4))
        p.restore()


def _about(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.4))
    p.drawEllipse(QPointF(12, 7.9), 0.5, 0.5)


def _search(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.5, 10.5), 6.2, 6.2)
    p.drawLine(QPointF(15.2, 15.2), QPointF(20, 20))


def _refresh(p: QPainter) -> None:
    path = QPainterPath()
    path.arcMoveTo(QRectF(4, 4, 16, 16), 55)
    path.arcTo(QRectF(4, 4, 16, 16), 55, 285)
    p.drawPath(path)
    arrow = QPainterPath()
    arrow.moveTo(19.6, 3.6)
    arrow.lineTo(19.6, 8.4)
    arrow.lineTo(14.8, 8.4)
    p.drawPath(arrow)


def _menu(p: QPainter) -> None:
    for y in (7, 12, 17):
        p.drawLine(QPointF(4, y), QPointF(20, y))


def _back(p: QPainter) -> None:
    p.drawLine(QPointF(14.5, 5), QPointF(8, 12))
    p.drawLine(QPointF(8, 12), QPointF(14.5, 19))


def _forward(p: QPainter) -> None:
    p.drawLine(QPointF(9.5, 5), QPointF(16, 12))
    p.drawLine(QPointF(16, 12), QPointF(9.5, 19))


def _close(p: QPainter) -> None:
    p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
    p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))


def _star(p: QPainter) -> None:
    path = QPainterPath()
    for index in range(10):
        angle = -90 + index * 36
        radius = 8.6 if index % 2 == 0 else 3.6
        point = QPointF(12 + radius * _cos(angle), 12 + radius * _sin(angle))
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    p.drawPath(path)


def _chevron_down(p: QPainter) -> None:
    p.drawLine(QPointF(6.5, 9.5), QPointF(12, 15))
    p.drawLine(QPointF(12, 15), QPointF(17.5, 9.5))


def _chevron_left(p: QPainter) -> None:
    p.drawLine(QPointF(14.5, 6.5), QPointF(9, 12))
    p.drawLine(QPointF(9, 12), QPointF(14.5, 17.5))


def _chevron_right(p: QPainter) -> None:
    p.drawLine(QPointF(9.5, 6.5), QPointF(15, 12))
    p.drawLine(QPointF(15, 12), QPointF(9.5, 17.5))


def _list(p: QPainter) -> None:
    for y in (6.5, 12, 17.5):
        p.drawLine(QPointF(4, y), QPointF(4.2, y))
        p.drawLine(QPointF(8, y), QPointF(20, y))


def _check(p: QPainter) -> None:
    p.drawLine(QPointF(5.5, 12.5), QPointF(10, 17))
    p.drawLine(QPointF(10, 17), QPointF(18.5, 7.5))


def _play(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(7.5, 4.8)
    path.lineTo(19, 12)
    path.lineTo(7.5, 19.2)
    path.closeSubpath()
    p.drawPath(path)


def _plus(p: QPainter) -> None:
    p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
    p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))


def _minus(p: QPainter) -> None:
    p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))


def _external(p: QPainter) -> None:
    p.drawLine(QPointF(13.5, 10.5), QPointF(20, 4))
    p.drawLine(QPointF(14.4, 4), QPointF(20, 4))
    p.drawLine(QPointF(20, 4), QPointF(20, 9.6))
    path = QPainterPath()
    path.moveTo(18, 14.5)
    path.lineTo(18, 19)
    path.lineTo(5, 19)
    path.lineTo(5, 6)
    path.lineTo(9.5, 6)
    p.drawPath(path)


def _dot(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 3.6, 3.6)


def _cos(degrees: float) -> float:
    import math

    return math.cos(math.radians(degrees))


def _sin(degrees: float) -> float:
    import math

    return math.sin(math.radians(degrees))


_PAINTERS = {
    "grid": _grid,
    "library": _library,
    "downloads": _downloads,
    "media": _media,
    "settings": _settings,
    "about": _about,
    "search": _search,
    "refresh": _refresh,
    "menu": _menu,
    "back": _back,
    "forward": _forward,
    "close": _close,
    "star": _star,
    "chevron-down": _chevron_down,
    "chevron-left": _chevron_left,
    "chevron-right": _chevron_right,
    "list": _list,
    "check": _check,
    "play": _play,
    "plus": _plus,
    "minus": _minus,
    "external": _external,
    "dot": _dot,
}

#: Icons whose shapes read better as a filled glyph at small sizes.
_SOLID = {"play", "dot", "check", "star"}

NAMES = tuple(_PAINTERS)


def paint_icon(
    painter: QPainter,
    name: str,
    rect: QRectF,
    color: QColor,
    width: float = 1.7,
    *,
    filled: bool | None = None,
) -> None:
    """Draw ``name`` into ``rect`` using ``color`` and a ``24 x 24`` grid."""
    draw = _PAINTERS.get(name)
    if draw is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.topLeft())
    scale = min(rect.width(), rect.height()) / VIEWBOX
    painter.scale(scale, scale)
    solid = name in _SOLID if filled is None else filled
    if solid:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
    else:
        painter.setPen(_pen(color, width / max(scale, 0.001)))
        painter.setBrush(Qt.NoBrush)
    draw(painter)
    painter.restore()


def draw_icon(
    painter: QPainter,
    name: str,
    x: float,
    y: float,
    size: float,
    color: QColor,
    width: float = 1.7,
    *,
    filled: bool | None = None,
) -> None:
    paint_icon(painter, name, QRectF(x, y, size, size), color, width, filled=filled)


def icon_pixmap(name: str, size: int, color: QColor, width: float = 1.7) -> QPixmap:
    """A 2x pixmap that is crisp on a high-DPI display.

    The device pixel ratio is set *before* painting, so the painter works in
    logical pixels and the icon is drawn at double resolution instead of being
    scaled up afterwards.
    """
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2.0)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    paint_icon(painter, name, QRectF(0, 0, size, size), color, width)
    painter.end()
    return pix


def icon(name: str, size: int, color: QColor | str, width: float = 1.7) -> QIcon:
    return QIcon(icon_pixmap(name, size, QColor(color), width))


__all__ = ["NAMES", "draw_icon", "icon", "icon_pixmap", "paint_icon"]
