"""Render the vector brand into a multiresolution Windows icon."""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def make_icon(source, destination):
    svg = QSvgRenderer(str(source))
    if not svg.isValid():
        raise ValueError(f"Invalid SVG: {source}")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    header = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    frames = bytearray()
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        with QPainter(image) as painter:
            svg.render(painter)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise ValueError("Could not encode the icon frame")
        png = bytes(buffer.data())
        header.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
        frames.extend(png)
        offset += len(png)
    destination.write_bytes(header + frames)


if __name__ == "__main__":
    make_icon(Path(sys.argv[1]), Path(sys.argv[2]))
