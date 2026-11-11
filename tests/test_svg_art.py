"""AI が描いた挿絵（SVG）を組み直すテスト。

役割：形と色とにじみの要素だけが通り、スクリプト、外の読み込み、字、名前空間の付いた属性が入った絵は使わないことを確かめる。
4つの極：特定の極に属さない土台。
守る原則：人に渡る紙に、確かめていないものを載せない。
"""

from __future__ import annotations

import base64

import pytest

from app import svg_art

GOOD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 320">'
    '<defs><filter id="wash"><feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="3" result="n"/>'
    '<feDisplacementMap in="SourceGraphic" in2="n" scale="12"/><feGaussianBlur stdDeviation="1.5"/></filter>'
    '<radialGradient id="g"><stop offset="0" stop-color="#e9c46a" stop-opacity="0.6"/></radialGradient></defs>'
    '<title>雨の日の梅</title>'
    '<g filter="url(#wash)" opacity="0.8"><circle cx="200" cy="150" r="40" fill="url(#g)"/>'
    '<path d="M10 300 Q 240 260 470 300" stroke="#8aa36b" stroke-width="3" fill="none"/></g></svg>'
)


def test_形と色とにじみの要素は通り_組み直した文になる():
    clean = svg_art.clean_svg(GOOD)
    assert clean.startswith("<svg")
    assert 'xmlns="http://www.w3.org/2000/svg"' in clean
    assert "feTurbulence" in clean and "url(#wash)" in clean and "雨の日の梅" in clean
    url = svg_art.data_url(clean)
    assert base64.b64decode(url.split(",", 1)[1]).decode("utf-8") == clean


@pytest.mark.parametrize(
    "bad",
    [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="alert(1)"></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><foreignObject/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>手紙</text></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><image href="https://example.com/a.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
        '<rect xlink:href="#a" width="1" height="1"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect fill="url(https://example.com/x)" width="1" height="1"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect style="fill:red;behavior:url(#x)" width="1" height="1"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect style="background:url(\'https://e.com\')" width="1" height="1"/></svg>',
        '<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>',
        '<div>svg ではない</div>',
        "<svg",
        "",
    ],
)
def test_スクリプトや外の読み込みや字が入った絵は使わない(bad):
    with pytest.raises(svg_art.UnsafeSvg):
        svg_art.clean_svg(bad)
