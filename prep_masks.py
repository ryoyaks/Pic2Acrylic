"""Stage 1: trace part masks into SVG cut-shapes + emit a manifest for Blender.

For each <part>.png in the source folder this produces:
  - <part>_mask.svg   single outer contour, viewBox = original pixel size
  - <part>_check.png  original image with the red cut-line drawn on top
  - manifest.json     list consumed by stage 2 (build_acrylic.py)

Only the mask is vectorised; the printed image stays raster so no detail is lost.
All parts cut from the same canvas share the same viewBox, which is what lets the
pieces auto-align in Blender (see README "matching").

Run:
  python prep_masks.py <parts_dir> -o <out_dir>
        [--bleed-px N] [--simplify F] [--alpha-threshold N]
"""

import argparse
import json
import pathlib
import sys

import cv2
import numpy as np
from PIL import Image

ALPHA_THRESHOLD_DEFAULT = 10
# approxPolyDP tolerance in PIXELS (absolute, so it doesn't scale up on big canvases
# and flatten curves). Small = smoother curves; 0 = keep the raw contour.
# Default tuned for maximum smoothness (just enough to clean pixel-staircase noise).
SIMPLIFY_PX_DEFAULT = 1.0


def load_rgba(path):
    """Load an image as an HxWx4 uint8 RGBA array."""
    return np.array(Image.open(path).convert("RGBA"))


def opaque_from_mask(mask_rgba, alpha_threshold):
    """Keep-region from a mask image, auto-detecting which channel holds the shape.

    Masks come in two common flavours and the spec's "white OR opaque" rule breaks
    on both unless we pick the right channel:
      - shape in the ALPHA channel (RGB often a flat colour, e.g. white everywhere)
      - shape in RGB luminance on a fully-opaque image (no usable alpha)
    If alpha actually varies (some transparent, some opaque) we trust alpha;
    otherwise we fall back to RGB brightness.

    Returns a float32 array in {0.0, 1.0}.
    """
    alpha = mask_rgba[..., 3]
    alpha_varies = bool((alpha < alpha_threshold).any() and (alpha >= alpha_threshold).any())
    if alpha_varies:
        keep = alpha >= alpha_threshold
    else:
        keep = mask_rgba[..., :3].max(axis=2) >= 128
    return keep.astype("float32"), ("alpha" if alpha_varies else "rgb")


def opaque_from_alpha(img_rgba, alpha_threshold):
    """Keep-region from the main image alpha channel."""
    return (img_rgba[..., 3] >= alpha_threshold).astype("float32")


def trace_to_svg(opaque, dilate_px, simplify_px):
    """Trace the keep-region: outer outline(s) AND internal holes (rebates/cut-outs).

    `simplify_px` is an absolute pixel tolerance: a small value keeps curves smooth
    (points stay dense along curves, sparse along straight edges); 0 keeps every point.

    Returns (svg_path_d, contours) or (None, None) if nothing was found. The path uses
    even-odd fill so nested hole contours are cut out of the shape.
    """
    m = (opaque * 255).astype("uint8")
    if dilate_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1,) * 2)
        m = cv2.dilate(m, k)
    cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts or hier is None:
        return None, None
    hier = hier[0]                       # [next, prev, first_child, parent]
    areas = [cv2.contourArea(c) for c in cnts]
    max_area = max(areas) or 1.0

    subpaths, contours = [], []
    for i, c in enumerate(cnts):
        is_hole = hier[i][3] != -1
        # keep significant outer pieces and real holes; drop speckle noise
        thresh = 0.004 * max_area if not is_hole else max(80.0, 0.00008 * max_area)
        if areas[i] < thresh:
            continue
        cc = cv2.approxPolyDP(c, simplify_px, True) if simplify_px > 0 else c
        cc = cc.reshape(-1, 2)
        if len(cc) < 3:
            continue
        subpaths.append("M " + " L ".join(f"{x} {y}" for x, y in cc) + " Z")
        contours.append(cc)
    if not subpaths:
        return None, None
    return " ".join(subpaths), contours


def write_svg(out_path, d, w, h):
    """Write an SVG whose viewBox equals the source pixel size (matching anchor)."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
        f'  <path d="{d}" fill="black" fill-rule="evenodd"/>\n'
        f"</svg>\n"
    )
    out_path.write_text(svg, encoding="utf-8")


def write_check(out_path, img_rgba, contours):
    """Save the source image with the red cut-lines (outline + holes) drawn over it."""
    bgr = cv2.cvtColor(img_rgba, cv2.COLOR_RGBA2BGR)
    # line thickness scales with image size so it stays visible on huge canvases
    t = max(2, round(max(img_rgba.shape[:2]) / 600))
    cv2.polylines(bgr, [c.reshape(-1, 1, 2) for c in contours], True, (0, 0, 255), t)
    cv2.imwrite(str(out_path), bgr)


def discover_parts(parts_dir):
    """Find <part>.png files (excluding _mask / _bleed / _back siblings)."""
    parts = []
    for png in sorted(parts_dir.glob("*.png")):
        stem = png.stem
        if stem.endswith("_mask") or stem.endswith("_bleed") or stem.endswith("_back"):
            continue
        parts.append(stem)
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trace part masks to SVG + manifest.")
    ap.add_argument("parts_dir", help="folder containing <part>.png (+ optional _mask/_bleed)")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--bleed-px", type=int, default=0, help="dilate the cut-shape outward by N px")
    ap.add_argument("--simplify", type=float, default=SIMPLIFY_PX_DEFAULT,
                    help="approxPolyDP epsilon in pixels (lower = smoother curves; 0 = raw)")
    ap.add_argument("--alpha-threshold", type=int, default=ALPHA_THRESHOLD_DEFAULT,
                    help="alpha value at/above which a pixel counts as kept")
    args = ap.parse_args(argv)

    parts_dir = pathlib.Path(args.parts_dir).resolve()
    out_dir = pathlib.Path(args.out).resolve()
    if not parts_dir.is_dir():
        sys.exit(f"parts_dir not found: {parts_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    names = discover_parts(parts_dir)
    if not names:
        sys.exit(f"no <part>.png files found in {parts_dir}")

    manifest = {"parts": []}
    canvas = None  # (w, h) shared-canvas check

    for name in names:
        main_png = parts_dir / f"{name}.png"
        mask_png = parts_dir / f"{name}_mask.png"
        bleed_png = parts_dir / f"{name}_bleed.png"
        back_png = parts_dir / f"{name}_back.png"   # optional back-side artwork

        img_rgba = load_rgba(main_png)
        h, w = img_rgba.shape[:2]
        if canvas is None:
            canvas = (w, h)
        elif (w, h) != canvas:
            print(f"  WARNING: {name} is {w}x{h}, expected {canvas[0]}x{canvas[1]} "
                  f"-- parts will NOT align", file=sys.stderr)

        if mask_png.exists():
            opaque, chan = opaque_from_mask(load_rgba(mask_png), args.alpha_threshold)
            src = f"mask:{chan}"
        else:
            opaque = opaque_from_alpha(img_rgba, args.alpha_threshold)
            src = "image-alpha"

        d, contours = trace_to_svg(opaque, args.bleed_px, args.simplify)
        if d is None:
            print(f"  SKIP {name}: empty mask", file=sys.stderr)
            continue

        svg_name = f"{name}_mask.svg"
        write_svg(out_dir / svg_name, d, w, h)
        write_check(out_dir / f"{name}_check.png", img_rgba, contours)

        texture = bleed_png.name if bleed_png.exists() else main_png.name
        entry = {
            "name": name,
            "svg": svg_name,
            "texture": texture,
            "src_dir": str(parts_dir),
            "width_px": w,
            "height_px": h,
        }
        if back_png.exists():                       # auto-detected back-side art
            entry["texture_back"] = back_png.name
        manifest["parts"].append(entry)
        pts = sum(len(c) for c in contours)
        holes = max(0, len(contours) - 1)
        print(f"  {name}: {pts} pts, {len(contours)} contour(s) ({holes} hole/extra) "
              f"(from {src}) -> {svg_name}{' [+back]' if back_png.exists() else ''}")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"done: {len(manifest['parts'])} part(s) -> {out_dir}")


if __name__ == "__main__":
    main()
