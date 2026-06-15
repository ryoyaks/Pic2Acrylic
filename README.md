# acrylic-standee

Preview layered artwork as **printed transparent-acrylic standee pieces** in Blender,
*before* sending anything to an acrylic print shop. It only does **conversion +
matching** — how the pieces stack/assemble is up to you, inside Blender.

- Each part's **mask** is vectorised into an SVG cut-shape.
- The artwork itself stays **raster** (texture) — no detail is lost.
- Every piece shares one coordinate frame, so the pieces **auto-align** with each
  other, and each piece's printed image lands **1:1** on its cut-shape.

## Pipeline

Two stages, because stage 1 needs OpenCV (which Blender's bundled Python lacks):

| Stage | Script | Runtime |
|---|---|---|
| 1. trace masks -> SVG + manifest | `prep_masks.py` | system Python 3 |
| 2. assemble acrylic in Blender   | `build_acrylic.py` | Blender's Python (`bpy`) |
| one-click wrapper                | `build-acrylic.bat` | Windows |

## Input contract

All parts cut from the same canvas **must share the same pixel size** (this is what
makes matching work).

| File | Required | Meaning |
|---|---|---|
| `<part>.png` | yes | printed artwork, RGBA, transparent background |
| `<part>_mask.png` | optional | the acrylic keep-region; if absent, the artwork's alpha is used |
| `<part>_bleed.png` | optional | used as the texture instead of `<part>.png` (print-to-bleed) |

Masks may encode the shape in **alpha** (e.g. flat-white RGB + alpha shape) **or** in
**RGB luminance** (opaque white-on-black). `prep_masks.py` auto-detects which channel
carries the shape.

## Usage

### Web UI (simplest)
Run **`web-ui.bat`** (installs deps on first run, opens http://127.0.0.1:5000).
Drag your part PNGs into the page, set the **thickness (mm)**, and click *Build* — it
traces the masks and opens Blender with the standee assembled. Or start it manually:

```bat
python web_ui.py
```

### One-click batch (Windows)
**Drag a parts folder onto `build-acrylic.bat`**. It runs stage 1, finds Blender, then
runs stage 2, writing `<folder>_prep\acrylic.blend`.

Or run the stages manually:

```bat
REM Stage 1
python prep_masks.py <parts_dir> -o <out_dir> [--bleed-px N] [--simplify F] [--alpha-threshold N]

REM Stage 2 (GUI - keep open to arrange/stack the pieces)
blender --python build_acrylic.py -- <out_dir>\manifest.json <out_dir>\acrylic.blend

REM Stage 2 (headless - just write the .blend)
blender --background --python build_acrylic.py -- <out_dir>\manifest.json <out_dir>\acrylic.blend
```

### Stage 1 outputs
- `<part>_mask.svg` — single outer contour, `viewBox = "0 0 W H"` (= source pixel size).
- `<part>_check.png` — artwork with the red cut-line drawn on top (eyeball the fit).
- `manifest.json` — consumed by stage 2.

### Stage 2 tunables (top of `build_acrylic.py`, or env vars)
- `THICKNESS_MM` (default 3.0) — acrylic sheet thickness. Env: `ACRYLIC_THICKNESS_MM`.
- `HEIGHT_MM` (default 150.0) — final standee height; the canvas height maps to this.
  Env: `ACRYLIC_HEIGHT_MM`.
- `FLIP_V` (default False) — flip if the printed texture comes out upside-down.
  Env: `ACRYLIC_FLIP_V=1`.

The web UI passes thickness through `ACRYLIC_THICKNESS_MM`.

### Finding Blender from the .bat
Search order: `BLENDER_PATH` env var -> `where blender` -> `Program Files\Blender
Foundation\*` -> common Steam install. If none are found, set it yourself:

```bat
set "BLENDER_PATH=D:\path\to\blender.exe"
```

## How matching works

1. All part SVGs share the same `viewBox` and are imported with the same operator, so
   Blender applies the **same pixel->world mapping** to each -> pieces auto-align.
2. That mapping's exact scale and Y-flip are importer internals and version-dependent.
   Rather than guess, stage 2 imports a **full-canvas reference rectangle** and
   measures its world bounding box `(x0,y0,x1,y1)`.
3. Each mesh vertex's world XY is normalised back into `[0,1]` of that frame and used
   as its UV -> the raster texture maps 1:1 onto the cut-shape. Only the mask is
   vectorised; the artwork never loses detail.

## Testing without real assets

```bat
python tests\make_fixture.py        REM writes parts\char.png + parts\char_mask.png
python prep_masks.py parts -o parts_prep
```

## Notes / known limitations

- Verified on **Blender 5.0.1** (Windows). Stage 2 enables the bundled `io_curve_svg`
  add-on automatically and uses headless-safe context overrides for `join`/`convert`.
- The Principled BSDF transmission input is `Transmission Weight` on Blender 4.x/5.x
  (older builds used `Transmission`); both are handled.
- The SVG importer seeds each curve with its own `SVGMat`; stage 2 clears that slot so
  the acrylic material is the one actually applied to the faces.
- Only the **largest outer contour** is traced — internal cut-outs (holes) are not
  handled yet. For holes, switch to `RETR_CCOMP` and even-odd fill.
- Transparent acrylic reads best in **Cycles** (or EEVEE with raytracing enabled);
  the printed regions show in any engine.
