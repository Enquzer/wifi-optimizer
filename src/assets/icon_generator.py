"""
WiFi Optimizer - Icon Generator
Creates application icons (ICO + PNG) programmatically using Pillow.
"""

import os
from PIL import Image, ImageDraw, ImageFont
import math


def _draw_wifi_icon(draw: ImageDraw.ImageDraw, size: int, signal: float = 1.0,
                    color_top=(0, 180, 255), color_bg=(15, 20, 40)):
    """Draw a WiFi signal icon with optional signal level (0.0-1.0)."""
    cx = size // 2
    # Draw arcs (WiFi bands)
    arc_color = color_top
    arcs = [
        (size * 0.12, size * 0.55, size * 0.85),  # outer
        (size * 0.25, size * 0.60, size * 0.72),  # middle
        (size * 0.40, size * 0.65, size * 0.60),  # inner
    ]
    dot_r = max(3, int(size * 0.06))

    levels = 3
    active_levels = max(1, round(signal * levels))

    for idx, (pad, y_top, arc_size) in enumerate(arcs):
        lv = levels - idx  # outer=3, middle=2, inner=1
        alpha = 255 if lv <= active_levels else 60
        r, g, b = arc_color
        c = (r, g, b, alpha)

        x0 = cx - arc_size // 2
        y0 = y_top
        x1 = cx + arc_size // 2
        y1 = y_top + arc_size
        thick = max(2, int(size * 0.055))
        for t in range(thick):
            draw.arc([x0 - t, y0 - t, x1 + t, y1 + t], start=200, end=340, fill=c)

    # Center dot
    dot_y = int(size * 0.78)
    draw.ellipse([cx - dot_r, dot_y - dot_r, cx + dot_r, dot_y + dot_r],
                 fill=(*color_top, 255))


def create_app_icon(output_path: str, size: int = 256):
    """Create the main application ICO file."""
    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for s in sizes:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background circle
        margin = max(1, s // 12)
        draw.ellipse([margin, margin, s - margin, s - margin],
                     fill=(15, 25, 50, 255))

        # WiFi arcs
        _draw_wifi_icon(draw, s, signal=1.0, color_top=(0, 180, 255))
        images.append(img)

    # Save as ICO with all sizes
    images[0].save(output_path, format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Saved app icon: {output_path}")


def create_tray_icons(assets_dir: str):
    """Create system tray icons for different signal levels."""
    levels = {
        "full": 1.0,
        "high": 0.75,
        "medium": 0.5,
        "low": 0.25,
        "none": 0.0,
        "optimizing": -1.0,  # special animated-style
    }

    for name, level in levels.items():
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if level < 0:
            # "Optimizing" icon — lightning bolt style
            cx, cy = size // 2, size // 2
            draw.ellipse([2, 2, size - 2, size - 2], fill=(20, 180, 80, 220))
            # Lightning bolt
            pts = [
                (cx + 4, cy - 18),
                (cx - 4, cy + 2),
                (cx + 2, cy + 2),
                (cx - 6, cy + 18),
                (cx + 4, cy - 2),
                (cx - 2, cy - 2),
            ]
            draw.polygon(pts, fill=(255, 255, 150, 255))
        else:
            _draw_wifi_icon(draw, size, signal=level,
                            color_top=(0, 200, 255),
                            color_bg=(10, 15, 35))

        path = os.path.join(assets_dir, f"tray_{name}.png")
        img.save(path, "PNG")
        print(f"Saved tray icon: {path}")

    return {name: os.path.join(assets_dir, f"tray_{name}.png")
            for name in levels}


def get_tray_icon_for_signal(signal_percent: int, assets_dir: str) -> str:
    """Return the appropriate tray icon path for the given signal level."""
    if signal_percent >= 80:
        name = "full"
    elif signal_percent >= 60:
        name = "high"
    elif signal_percent >= 40:
        name = "medium"
    elif signal_percent > 0:
        name = "low"
    else:
        name = "none"
    return os.path.join(assets_dir, f"tray_{name}.png")


def ensure_assets(assets_dir: str):
    """Create assets directory and generate all icons if missing."""
    os.makedirs(assets_dir, exist_ok=True)
    ico_path = os.path.join(assets_dir, "app_icon.ico")
    if not os.path.exists(ico_path):
        create_app_icon(ico_path)
    tray_full = os.path.join(assets_dir, "tray_full.png")
    if not os.path.exists(tray_full):
        create_tray_icons(assets_dir)
    return ico_path
