"""Stage 2: assemble traced parts into transparent printed-acrylic pieces in Blender.

Run (GUI, keep the file open to arrange/stack pieces yourself):
  blender --python build_acrylic.py -- <prep>\\manifest.json [out.blend]
Run (headless, just write the .blend):
  blender --background --python build_acrylic.py -- <prep>\\manifest.json out.blend

What it does, per the spec's "matching" principle:
  1. Build a full-canvas reference rectangle, import it, and MEASURE the
     pixel -> world mapping the SVG importer actually used (version-independent).
  2. For each part: import its mask SVG, fill it to a solid mesh, and set UVs by
     normalising each vertex's world XY back into [0,1] of that reference frame.
     -> the raster texture lands 1:1 on the cut-shape, every part shares one frame
        so the pieces are mutually aligned.
  3. Apply a transparent-acrylic + alpha-gated printed material, give each piece
     real thickness (Solidify), stand the group upright and drop it onto z=0.

Only the cut-shape is vector; the artwork stays raster, so no detail is lost.
"""

import json
import math
import os
import sys
import tempfile

import bpy
import mathutils

# ---- tunables (env vars let the web UI / .bat override without editing) ----
THICKNESS_MM = float(os.environ.get("ACRYLIC_THICKNESS_MM", 3.0))  # sheet thickness
HEIGHT_MM = float(os.environ.get("ACRYLIC_HEIGHT_MM", 150.0))      # canvas height -> this
GAP_MM = float(os.environ.get("ACRYLIC_GAP_MM", 4.0))             # gap between sheets (depth)
FLIP_V = os.environ.get("ACRYLIC_FLIP_V", "0").lower() in ("1", "true", "yes")
# ---------------------------------------------------------------------------


def argv_after_dashes():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def ensure_svg_addon():
    """The SVG importer is a bundled add-on that may not be enabled by default."""
    if hasattr(bpy.ops.import_curve, "svg"):
        try:
            # touch it; if it errors we still try to enable below
            return
        except Exception:
            pass
    for module in ("io_curve_svg", "bl_ext.blender_org.io_curve_svg"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            return
        except Exception:
            continue


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.curves, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def import_svg(path):
    """Import an SVG and return the list of newly-created objects."""
    before = set(bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def world_bbox_xy(objs):
    """Axis-aligned world-space XY bounding box over the given objects."""
    xs, ys = [], []
    for o in objs:
        for corner in o.bound_box:
            w = o.matrix_world @ mathutils.Vector(corner)
            xs.append(w.x)
            ys.append(w.y)
    return min(xs), min(ys), max(xs), max(ys)


def measure_reference_frame(width_px, height_px):
    """Import a full-canvas rectangle and measure its world XY box, then delete it.

    Gives the linear pixel->world mapping the importer used, which we invert for UVs.
    """
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width_px}" height="{height_px}" '
        f'viewBox="0 0 {width_px} {height_px}">'
        f'<path d="M 0 0 L {width_px} 0 L {width_px} {height_px} '
        f'L 0 {height_px} Z" fill="black"/></svg>'
    )
    fd, tmp = tempfile.mkstemp(suffix="_frame.svg")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(svg)
    try:
        objs = import_svg(tmp)
        frame = world_bbox_xy(objs)
        with bpy.context.temp_override(selected_objects=objs):
            bpy.ops.object.delete()
    finally:
        os.remove(tmp)
    return frame


def curves_to_mesh(objs, name):
    """Join curve objects, fill them, and convert to a single solid mesh object."""
    curves = [o for o in objs if o.type == "CURVE"]
    if not curves:
        raise RuntimeError(f"{name}: SVG produced no curves")

    target = curves[0]
    bpy.context.view_layer.objects.active = target
    if len(curves) > 1:
        with bpy.context.temp_override(active_object=target,
                                       selected_objects=curves,
                                       selected_editable_objects=curves):
            bpy.ops.object.join()

    target.data.dimensions = "2D"
    target.data.fill_mode = "BOTH"
    target.name = name

    with bpy.context.temp_override(active_object=target,
                                   selected_objects=[target],
                                   selected_editable_objects=[target]):
        bpy.ops.object.convert(target="MESH")
    return target


def assign_uv(obj, frame):
    """UV = each loop vertex's world XY normalised into the reference frame [0,1]."""
    x0, y0, x1, y1 = frame
    dx = (x1 - x0) or 1.0
    dy = (y1 - y0) or 1.0
    me = obj.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    uv = me.uv_layers.active.data
    mw = obj.matrix_world
    for poly in me.polygons:
        for li in poly.loop_indices:
            co = mw @ me.vertices[me.loops[li].vertex_index].co
            u = (co.x - x0) / dx
            v = (co.y - y0) / dy
            uv[li].uv = (u, 1.0 - v if FLIP_V else v)


def set_principled(bsdf, name, value):
    """Set a Principled input by name if it exists (version-tolerant)."""
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def make_material(name, image_path):
    """Transparent acrylic where the texture is clear; opaque print where it isn't."""
    mat = bpy.data.materials.new(name=f"{name}_acrylic")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    glass = nt.nodes.new("ShaderNodeBsdfPrincipled")    # clear acrylic
    printed = nt.nodes.new("ShaderNodeBsdfPrincipled")  # printed ink
    tex = nt.nodes.new("ShaderNodeTexImage")

    img = bpy.data.images.load(str(image_path), check_existing=True)
    tex.image = img
    tex.interpolation = "Cubic"

    set_principled(glass, "Roughness", 0.03)
    set_principled(glass, "IOR", 1.49)
    # Transmission input was renamed across versions.
    if "Transmission Weight" in glass.inputs:       # Blender 4.x / 5.x
        glass.inputs["Transmission Weight"].default_value = 1.0
    elif "Transmission" in glass.inputs:            # older
        glass.inputs["Transmission"].default_value = 1.0

    set_principled(printed, "Roughness", 0.45)

    nt.links.new(tex.outputs["Color"], printed.inputs["Base Color"])
    nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    nt.links.new(glass.outputs["BSDF"], mix.inputs[1])    # Fac=0 -> clear acrylic
    nt.links.new(printed.outputs["BSDF"], mix.inputs[2])  # Fac=1 -> printed
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat


def resolve_texture(part, manifest_dir):
    """Texture path: src_dir may be absolute or relative to the manifest."""
    src_dir = part.get("src_dir", "")
    cand = os.path.join(src_dir, part["texture"])
    if os.path.isabs(cand) and os.path.exists(cand):
        return cand
    rel = os.path.join(manifest_dir, src_dir, part["texture"])
    if os.path.exists(rel):
        return rel
    # last resort: texture sitting next to the manifest
    return os.path.join(manifest_dir, part["texture"])


def apply_transform(objs, location=False, rotation=False, scale=False):
    """object.transform_apply on each object individually (headless-safe)."""
    for obj in objs:
        with bpy.context.temp_override(active_object=obj,
                                       selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            bpy.ops.object.transform_apply(location=location,
                                           rotation=rotation, scale=scale)


def build(manifest_path):
    manifest_path = os.path.abspath(manifest_path)
    manifest_dir = os.path.dirname(manifest_path)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    parts = manifest["parts"]
    if not parts:
        raise RuntimeError("manifest has no parts")

    ensure_svg_addon()
    clear_scene()

    w = parts[0]["width_px"]
    h = parts[0]["height_px"]
    frame = measure_reference_frame(w, h)
    print(f"[frame] world bbox {frame}")

    pieces = []
    for part in parts:
        svg_path = os.path.join(manifest_dir, part["svg"])
        objs = import_svg(svg_path)
        obj = curves_to_mesh(objs, part["name"])
        assign_uv(obj, frame)
        tex_path = resolve_texture(part, manifest_dir)
        if not os.path.exists(tex_path):
            print(f"  WARNING: texture not found for {part['name']}: {tex_path}")
        # The SVG importer seeds slot 0 with its own 'SVGMat'; clear it so our
        # acrylic material lands in slot 0 and the faces actually use it.
        obj.data.materials.clear()
        obj.data.materials.append(make_material(part["name"], tex_path))

        solid = obj.modifiers.new("Solidify", "SOLIDIFY")
        solid.thickness = THICKNESS_MM
        solid.offset = 0.0
        pieces.append(obj)
        print(f"  built {part['name']}")

    # Every piece shares the SVG importer's origin, so applying the same scale and
    # rotation to each one (no parent) keeps them mutually aligned -- and leaves the
    # pieces independent so you can grab and rotate any one without dragging the rest.

    # Scale so 1 BU == 1 mm (canvas height -> HEIGHT_MM). Baking scale to 1.0 is what
    # makes Solidify's THICKNESS_MM read as real millimetres on the final-size mesh.
    frame_h = (frame[3] - frame[1]) or 1.0
    s = HEIGHT_MM / frame_h
    for obj in pieces:
        obj.scale = (s, s, s)
    apply_transform(pieces, scale=True)

    # Stand upright (+X tilt), about the shared origin so alignment is preserved.
    for obj in pieces:
        obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    apply_transform(pieces, rotation=True)

    # Drop the whole set onto the floor (min Z -> 0) and centre it in X, shifting
    # every piece by the SAME amount so they stay registered to each other.
    bpy.context.view_layer.update()
    zs, xs = [], []
    for obj in pieces:
        for corner in obj.bound_box:
            w = obj.matrix_world @ mathutils.Vector(corner)
            zs.append(w.z)
            xs.append(w.x)
    dz, dx = -min(zs), -(min(xs) + max(xs)) / 2.0
    for obj in pieces:
        obj.location.x += dx
        obj.location.z += dz

    # Give each piece its own origin at its geometry centre, so it rotates about
    # itself instead of some shared far-away point.
    bpy.context.view_layer.update()
    for obj in pieces:
        with bpy.context.temp_override(active_object=obj,
                                       selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

    # Fan the pieces apart in depth (Y) so they don't overlap, keeping the front
    # view aligned. Centre the spread around Y=0.
    pitch = THICKNESS_MM + GAP_MM
    n = len(pieces)
    for i, obj in enumerate(pieces):
        obj.location.y += (i - (n - 1) / 2.0) * pitch

    print(f"[done] {len(pieces)} piece(s)")
    return pieces


def main():
    args = argv_after_dashes()
    if not args:
        sys.exit("usage: blender ... -- <manifest.json> [out.blend]")
    manifest_path = args[0]
    out_blend = args[1] if len(args) > 1 else None

    build(manifest_path)

    if out_blend:
        out_blend = os.path.abspath(out_blend)
        bpy.ops.wm.save_as_mainfile(filepath=out_blend)
        print(f"[saved] {out_blend}")


if __name__ == "__main__":
    main()
