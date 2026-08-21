#!/usr/bin/env python3
"""Generate deterministic Sooner Racing Telemetry PNG and Windows icon assets."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "packaging" / "assets"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/impact.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
)
BACKGROUND = "#050505"
OUTLINE = "#d71920"
FOREGROUND = "#ffffff"


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("A supported bold Windows font was not found.")


def render_logo(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)
    stroke = max(2, round(size * 0.023))
    font_size = round(size * 0.52)
    font = load_font(font_size)
    text = "SRT"
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    x = (size - text_width) / 2 - bounds[0]
    y = (size - text_height) / 2 - bounds[1] - size * 0.012
    draw.text(
        (x, y),
        text,
        font=font,
        fill=FOREGROUND,
        stroke_width=stroke,
        stroke_fill=OUTLINE,
    )
    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    logo = render_logo(1024)
    png_path = ASSET_DIR / "SoonerRacingTelemetry.png"
    ico_path = ASSET_DIR / "SoonerRacingTelemetry.ico"
    logo.save(png_path, format="PNG", optimize=True)
    logo.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(png_path)
    print(ico_path)


if __name__ == "__main__":
    main()
