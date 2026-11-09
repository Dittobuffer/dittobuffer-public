"""ANSI banner art for the Dittobuffer console."""

from __future__ import annotations

import os
import sys

#: 5-row block font, only the glyphs the wordmark needs.
FONT: dict[str, list[str]] = {
    "D": ["####.", "#...#", "#...#", "#...#", "####."],
    "I": ["#####", "..#..", "..#..", "..#..", "#####"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#.."],
    "O": [".###.", "#...#", "#...#", "#...#", ".###."],
    "B": ["####.", "#...#", "####.", "#...#", "####."],
    "U": ["#...#", "#...#", "#...#", "#...#", ".###."],
    "F": ["#####", "#....", "####.", "#....", "#...."],
    "E": ["#####", "#....", "####.", "#....", "#####"],
    "R": ["####.", "#...#", "####.", "#..#.", "#...#"],
}

WORDMARK = "DITTOBUFFER"

#: Cyan -> violet, one entry per glyph row.
GRADIENT = ((34, 211, 238), (56, 189, 248), (99, 102, 241), (139, 92, 246), (192, 132, 252))


def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _paint(text: str, rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    return f"\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m"


def wordmark(word: str = WORDMARK, colour: bool | None = None) -> str:
    """Render the banner as block letters, optionally in true-colour gradient."""

    if colour is None:
        colour = _supports_colour()
    rows = []
    for row in range(5):
        glyphs = [FONT[letter.upper()][row] for letter in word if letter.upper() in FONT]
        line = " ".join(glyphs).replace("#", "\u2588").replace(".", " ")
        rows.append(_paint(line, GRADIENT[row]) if colour else line)
    return "\n".join(rows)


def banner(version: str = "1.0.0", subtitle: str = "private family Pokemon GO server") -> str:
    """Full startup banner: wordmark, rule and tagline."""

    colour = _supports_colour()
    rule = "\u2500" * 58
    tagline = f"v{version}  \u00b7  {subtitle}"
    if colour:
        rule = _paint(rule, (99, 102, 241))
        tagline = _paint(tagline, (148, 163, 184))
    return f"{wordmark(colour=colour)}\n{rule}\n{tagline}\n"
