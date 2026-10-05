r"""icon.svg → 앱 아이콘 PNG 만들기 (PyMuPDF로 그림을 그린다). 아이콘을 바꾸면 다시 실행:
    ..\..\.venv\Scripts\python.exe make_icons.py
maskable: 안드로이드가 동그랗게·둥근 사각형으로 잘라도 그림이 잘리지 않게 가장자리 여백을 둔 판
"""
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
svg = (HERE / "icon.svg").read_bytes()
# maskable: 바탕은 그대로, 그림만 80% 크기로 가운데에 (안전 영역)
maskable = svg.replace(b'<path d="M150', b'<g transform="translate(51.2 51.2) scale(0.8)"><path d="M150', 1) \
              .replace(b"</svg>", b"</g></svg>")


def render(src, size, name):
    doc = pymupdf.open(stream=src, filetype="svg")
    page = doc[0]
    zoom = size / page.rect.width
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(HERE / name)
    print(name, size)


for size in (192, 512):
    render(svg, size, f"icon-{size}.png")
render(maskable, 512, "icon-maskable-512.png")
render(svg, 180, "apple-touch-icon.png")
render(svg, 32, "favicon-32.png")
