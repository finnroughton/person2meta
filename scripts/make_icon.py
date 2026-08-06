"""
person2meta - generates the desktop-shortcut icon.

Draws an ORIGINAL, simplified composition (not a traced copy of either
real logo): an orange "chomp + tail" swirl in the upper-left standing in
for Blender, a black hexagon with a crossed white ribbon in the lower-right
standing in for MetaHuman, and a curved arrow sweeping through the top-right
corner connecting the two -- visually evokes "Blender feeds into
MetaHuman" without reproducing either brand's actual artwork.

Run with regular system Python (needs Pillow: pip install Pillow).
Outputs person2meta_icon.ico next to this script, sized 16/32/48/64/128/256.
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw

SIZE = 1024  # supersample, then downscale each ICO frame for smooth edges
OUT_PATH = Path(__file__).parent / "person2meta_icon.ico"

ORANGE = (235, 111, 19, 255)
NAVY = (34, 74, 110, 255)
WHITE = (255, 255, 255, 255)
BLACK = (18, 18, 20, 255)
TRANSPARENT = (0, 0, 0, 0)


def draw_blender_glyph(img: Image.Image, cx: float, cy: float, r: float) -> None:
    """Orange comma-shaped body (round head + tapering swept wing) with a
    navy/white center dot -- same general silhouette family as Blender's
    mark, drawn from scratch as its own polygon math, not traced."""
    d = ImageDraw.Draw(img)

    # Head: a circle with a wedge bitten out where the wing attaches.
    bbox = [cx - r, cy - r, cx + r, cy + r]
    d.pieslice(bbox, start=15, end=195, fill=ORANGE)

    # Wing: a tapering crescent swept from the mouth opening down and
    # around to a sharp point, built as an outer-arc / inner-arc polygon
    # (outer and inner radii converge to the same point at the tip).
    a0, a1 = 195, 330  # sweep angle range (degrees)
    outer_r0, outer_r1 = r * 1.0, r * 1.18
    inner_r0, inner_r1 = r * 0.55, r * 1.18
    steps = 40
    outer_pts = []
    inner_pts = []
    for i in range(steps + 1):
        t = i / steps
        ang = math.radians(a0 + (a1 - a0) * t)
        # ease the taper so most of the narrowing happens near the tip
        ease = t ** 1.6
        outer_r = outer_r0 + (outer_r1 - outer_r0) * t
        inner_r = inner_r0 + (inner_r1 - inner_r0) * ease
        outer_pts.append((cx + outer_r * math.cos(ang), cy + outer_r * math.sin(ang)))
        inner_pts.append((cx + inner_r * math.cos(ang), cy + inner_r * math.sin(ang)))
    d.polygon(outer_pts + list(reversed(inner_pts)), fill=ORANGE)

    # Center "eye": white ring + navy dot, slightly toward the mouth side.
    ex, ey = cx - r * 0.02, cy - r * 0.08
    er = r * 0.42
    d.ellipse([ex - er, ey - er, ex + er, ey + er], fill=WHITE)
    ir = r * 0.26
    d.ellipse([ex - ir, ey - ir, ex + ir, ey + ir], fill=NAVY)


def hexagon_points(cx: float, cy: float, r: float, rotation_deg: float = 90):
    pts = []
    for i in range(6):
        ang = math.radians(rotation_deg + 60 * i)
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def draw_metahuman_glyph(img: Image.Image, cx: float, cy: float, r: float) -> None:
    """Black hexagon with a crossed white ribbon -- same general silhouette
    family as the MetaHuman mark, drawn from scratch (not traced)."""
    d = ImageDraw.Draw(img)
    d.polygon(hexagon_points(cx, cy, r), fill=BLACK)

    # Crossed ribbon: two parallelogram "bowtie" arms meeting in the middle,
    # approximating the interlocked-strand look with straight segments. Kept
    # well inside the hexagon's own radius so the black hexagon body still
    # reads clearly as a hexagon around it, not just floating shards.
    arm = r * 0.62
    half_w = r * 0.13

    def arm_polygon(dx1, dy1, dx2, dy2):
        # A thick diagonal band from (cx+dx1,cy+dy1) to (cx+dx2,cy+dy2).
        ax, ay = dx2 - dx1, dy2 - dy1
        length = math.hypot(ax, ay)
        nx, ny = -ay / length * half_w, ax / length * half_w
        p1 = (cx + dx1 + nx, cy + dy1 + ny)
        p2 = (cx + dx2 + nx, cy + dy2 + ny)
        p3 = (cx + dx2 - nx, cy + dy2 - ny)
        p4 = (cx + dx1 - nx, cy + dy1 - ny)
        return [p1, p2, p3, p4]

    d.polygon(arm_polygon(-arm, -arm * 0.55, arm, arm * 0.55), fill=WHITE)
    d.polygon(arm_polygon(-arm, arm * 0.55, arm, -arm * 0.55), fill=WHITE)

    # Small black diamond where the arms cross, so they read as woven
    # rather than just overlapping flat.
    dia = r * 0.14
    d.polygon([(cx, cy - dia), (cx + dia, cy), (cx, cy + dia), (cx - dia, cy)], fill=BLACK)

    # Thin white ring near the hexagon's edge so the black frame reads as a
    # deliberate hexagonal border rather than solid corners.
    ring_pts = hexagon_points(cx, cy, r * 0.86)
    d.line(ring_pts + [ring_pts[0]], fill=WHITE, width=max(2, int(r * 0.05)), joint="curve")


def quad_bezier(p0, p1, p2, steps=60):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def draw_curved_arrow(img: Image.Image, p0, p1, p2, width: float, color) -> None:
    """Curved arrow (quadratic bezier through p0->p1->p2) with a triangular
    arrowhead at the end, oriented along the curve's final tangent."""
    d = ImageDraw.Draw(img)
    pts = quad_bezier(p0, p1, p2, steps=80)

    # Stroke the curve using overlapping circles along the path (PIL has no
    # native variable-width curved stroke) for a smooth, rounded line.
    for x, y in pts:
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=color)

    # Arrowhead at the very end, aligned to the last segment's direction.
    (ex, ey) = pts[-1]
    (px, py) = pts[-6]
    ang = math.atan2(ey - py, ex - px)
    head_len = width * 3.4
    head_w = width * 2.4
    left = (ex - head_len * math.cos(ang - math.radians(28)),
            ey - head_len * math.sin(ang - math.radians(28)))
    right = (ex - head_len * math.cos(ang + math.radians(28)),
             ey - head_len * math.sin(ang + math.radians(28)))
    tip = (ex + head_len * 0.35 * math.cos(ang), ey + head_len * 0.35 * math.sin(ang))
    d.polygon([tip, left, right], fill=color)


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), TRANSPARENT)

    blender_c = (SIZE * 0.34, SIZE * 0.34)
    blender_r = SIZE * 0.24

    meta_c = (SIZE * 0.68, SIZE * 0.68)
    meta_r = SIZE * 0.20

    # Arrow drawn first so both glyphs sit cleanly on top of its endpoints.
    p0 = (blender_c[0] + blender_r * 0.55, blender_c[1] - blender_r * 0.75)
    p1 = (SIZE * 0.92, SIZE * 0.08)
    p2 = (meta_c[0] + meta_r * 0.15, meta_c[1] - meta_r * 1.25)
    draw_curved_arrow(img, p0, p1, p2, width=SIZE * 0.028, color=(70, 70, 74, 255))

    draw_blender_glyph(img, *blender_c, blender_r)
    draw_metahuman_glyph(img, *meta_c, meta_r)

    return img


def main():
    img = build()
    sizes = [16, 32, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(
        OUT_PATH,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
