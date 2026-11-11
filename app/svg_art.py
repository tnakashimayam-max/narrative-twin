"""AI が描いた挿絵（SVG）を、画面に出してよい形にする。

役割：手紙の挿絵は、ツイン（AI）が SVG の文で描く（app/self_letters.py）。SVG はスクリプトや外のファイルを含められるので、
     そのまま出さない。使ってよい要素と属性だけを残して組み直し、だめなものが入っていたら絵ごと使わない。
4つの極：特定の極に属さない土台。
守る原則：人に渡る紙に、確かめていないものを載せない。

決まり
- 形と色とにじみ（フィルター）の要素だけを通す。text、image、use、style、script、foreignObject、a、アニメーションは通さない。
- 属性も決めたものだけ。on で始まる属性、href、名前空間の付いた属性は通さない。
- 参照は url(#id) だけ。外を指す URL は通さない。
- 画面には data: の URL にして img で出す。img の中の SVG はスクリプトも外の読み込みも動かない。ここで組み直すのは、その上の守り。

関連するテスト：tests/test_svg_art.py
"""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
# 受け取る SVG の文字数の上限。答えの型（app/schemas.py の LetterArt）と同じ
MAX_CHARS = 40000

ALLOWED_TAGS = frozenset(
    {
        "svg", "g", "defs", "title", "desc",
        "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
        "linearGradient", "radialGradient", "stop",
        "clipPath", "mask",
        "filter", "feTurbulence", "feDisplacementMap", "feGaussianBlur", "feColorMatrix", "feBlend",
        "feComposite", "feOffset", "feMerge", "feMergeNode", "feFlood", "feMorphology",
    }
)  # fmt: skip

ALLOWED_ATTRS = frozenset(
    {
        "viewBox", "width", "height", "preserveAspectRatio", "id", "transform", "opacity", "style",
        "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "points",
        "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
        "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-miterlimit",
        "filter", "clip-path", "clip-rule", "mask", "mix-blend-mode",
        "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "fx", "fy", "fr", "spreadMethod",
        "filterUnits", "primitiveUnits", "maskUnits", "maskContentUnits", "clipPathUnits", "color-interpolation-filters",
        "type", "baseFrequency", "numOctaves", "seed", "stitchTiles", "result", "in", "in2", "scale",
        "xChannelSelector", "yChannelSelector", "stdDeviation", "values", "mode", "operator",
        "k1", "k2", "k3", "k4", "dx", "dy", "flood-color", "flood-opacity", "radius",
    }
)  # fmt: skip

# 文の中にあってはいけないもの。実体の定義（大きく膨らませる攻撃）と、外の読み込みの指定
_FORBIDDEN_TEXT = re.compile(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", re.IGNORECASE)
# 参照は url(#id) だけ
_URL = re.compile(r"url\(([^)]*)\)", re.IGNORECASE)
_LOCAL_REF = re.compile(r"\s*['\"]?#[A-Za-z_][\w.\-]*['\"]?\s*")
# style の中で通さない書き方
_BAD_STYLE = re.compile(r"@|expression|javascript|\\|behavior|-moz-binding", re.IGNORECASE)


class UnsafeSvg(ValueError):
    """使えない SVG。理由を持つ。"""


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        if namespace != SVG_NS:
            raise UnsafeSvg(f"SVG のほかの要素があります：{tag}")
        return name
    return tag


def _check_value(name: str, value: str) -> None:
    for target in _URL.findall(value):
        if not _LOCAL_REF.fullmatch(target):
            raise UnsafeSvg(f"外を指す URL があります：{name}")
    if name == "style" and _BAD_STYLE.search(value):
        raise UnsafeSvg("style に使えない書き方があります")


def _clean(node: ET.Element) -> ET.Element:
    name = _local_name(node.tag)
    if name not in ALLOWED_TAGS:
        raise UnsafeSvg(f"使えない要素があります：{name}")
    clean = ET.Element(f"{{{SVG_NS}}}{name}")
    for key, value in node.attrib.items():
        if key.startswith("{") or ":" in key:
            raise UnsafeSvg(f"名前空間の付いた属性があります：{key}")
        if key not in ALLOWED_ATTRS:
            raise UnsafeSvg(f"使えない属性があります：{key}")
        _check_value(key, value)
        clean.set(key, value)
    # 字は描かせない。title と desc の中の字だけは、読み上げ用に残す
    if name in ("title", "desc"):
        clean.text = (node.text or "").strip()[:80]
    for child in node:
        clean.append(_clean(child))
    return clean


def clean_svg(svg: str) -> str:
    """使ってよい要素と属性だけで組み直した SVG の文を返す。だめなものがあれば UnsafeSvg。

    関連するテスト：tests/test_svg_art.py
    """
    text = svg.strip()
    if not text or len(text) > MAX_CHARS:
        raise UnsafeSvg("SVG が空か、長すぎます")
    if _FORBIDDEN_TEXT.search(text):
        raise UnsafeSvg("SVG に使えない宣言があります")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise UnsafeSvg(f"SVG として読めません：{error}") from error
    if _local_name(root.tag) != "svg":
        raise UnsafeSvg("いちばん外が svg ではありません")
    if not root.get("viewBox"):
        raise UnsafeSvg("viewBox がありません")
    clean = _clean(root)
    ET.register_namespace("", SVG_NS)
    return ET.tostring(clean, encoding="unicode")


def data_url(svg: str) -> str:
    """組み直した SVG を、img に入れる data: の URL にする。"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
