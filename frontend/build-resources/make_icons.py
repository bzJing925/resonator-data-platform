#!/usr/bin/env python3
"""Generate app icons for Electron builder."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
SIZE = 1024

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# dark rounded square background
radius = SIZE // 6
bg = (24, 27, 33, 255)
draw.rounded_rectangle((0, 0, SIZE, SIZE), radius=radius, fill=bg)

# subtle gradient ring
for i in range(6):
    offset = 40 + i * 18
    alpha = 40 - i * 6
    draw.ellipse(
        (offset, offset, SIZE - offset, SIZE - offset),
        outline=(95, 168, 211, alpha),
        width=4,
    )

# central Σ mark
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", SIZE // 2)
except Exception:
    font = ImageFont.load_default()

text = "Σ"
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (SIZE - tw) // 2 - bbox[0]
y = (SIZE - th) // 2 - bbox[1]
draw.text((x, y), text, font=font, fill=(95, 168, 211, 255))

# Save PNG
img.save(OUT / "icon.png")

# Save ICO
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
img.convert("RGBA").save(OUT / "icon.ico", sizes=[(s, s) for s in ico_sizes])

# Save ICNS
img.save(OUT / "icon.icns")

print("Icons generated:", OUT / "icon.{png,ico,icns}")
