"""Shared visual tokens for Steam 统译 v3."""

from __future__ import annotations

# Surfaces
BG = "#0d1117"
SURFACE = "#141b24"
SURFACE_2 = "#1a2330"
SURFACE_3 = "#202c3a"
BORDER = "#263444"
BORDER_2 = "#324256"

# Text
TEXT = "#dbe7f3"
MUTED = "#8798aa"
DIM = "#5d6e80"

# Brand and states
PRIMARY = "#2f80b7"
PRIMARY_HOVER = "#3a91cc"
PRIMARY_PRESSED = "#2a6f9f"
ACCENT = "#5db6e8"
SUCCESS = "#59c38a"
WARNING = "#e0a84a"
DANGER = "#d66a6a"
SELECTION = "#1d3a50"

# Spacing
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24

# Shape
RADIUS_CARD = 10
RADIUS_CONTROL = 6
ROW_HEIGHT = 34
HEADER_HEIGHT = 56

FONT = "Microsoft YaHei UI"
FONT_SMALL = (FONT, 8)
FONT_BODY = (FONT, 9)
FONT_TITLE = (FONT, 11, "bold")
FONT_DISPLAY = (FONT, 15, "bold")

# Backwards-compatible aliases for existing modules.
PANEL = SURFACE
PANEL2 = SURFACE_2
FG = TEXT
