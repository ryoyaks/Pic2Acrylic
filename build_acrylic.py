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

# ---- tunables in CENTIMETRES (env vars let the web UI / .bat override) ----
# After build, 1 Blender unit == 1 cm.
THICKNESS_CM = float(os.environ.get("ACRYLIC_THICKNESS_CM", 0.3))  # sheet thickness (0.3cm = 3mm)
HEIGHT_CM = float(os.environ.get("ACRYLIC_HEIGHT_CM", 15.0))       # real height of the TALLEST piece
GAP_CM = float(os.environ.get("ACRYLIC_GAP_CM", 0.4))             # gap between sheets (depth)
FLIP_V = os.environ.get("ACRYLIC_FLIP_V", "0").lower() in ("1", "true", "yes")
# When on, use an auto-detected <part>_back.png for the back face of each piece.
DOUBLE_SIDED = os.environ.get("ACRYLIC_DOUBLE_SIDED", "0").lower() in ("1", "true", "yes")
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


def acrylic_material():
    """One shared clear-acrylic material reused by every piece, so the user can
    tweak the look once (colour/roughness/tint) and it applies to all sheets."""
    existing = bpy.data.materials.get("Acrylic")
    if existing:
        return existing
    mat = bpy.data.materials.new(name="Acrylic")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    set_principled(bsdf, "Base Color", (0.90, 0.94, 1.0, 1.0))   # faint cool tint
    set_principled(bsdf, "Roughness", 0.06)
    set_principled(bsdf, "IOR", 1.49)
    # Alpha-blended translucency (not raytraced glass): this is see-through in every
    # shading mode AND lets the print behind it show -- EEVEE's raytraced transmission
    # would hide the alpha-blended print. Tweak this one shared material to taste.
    set_principled(bsdf, "Alpha", 0.16)
    for attr, val in (("blend_method", "BLEND"),
                      ("surface_render_method", "BLENDED"),
                      ("show_transparent_back", True),
                      ("use_backface_culling", False)):
        try:
            setattr(mat, attr, val)
        except Exception:
            pass
    return mat


def _image_node(nt, path, flip_u=False):
    """An Image Texture node; flip_u mirrors horizontally (for back-side art so it
    reads correctly when viewed from behind the sheet)."""
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(path), check_existing=True)
    tex.interpolation = "Cubic"
    if flip_u:
        uv = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (-1.0, 1.0, 1.0)
        mp.inputs["Location"].default_value = (1.0, 0.0, 0.0)
        nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], tex.inputs["Vector"])
    return tex


def print_material(name, image_path, back_path=None):
    """Per-part printed-ink layer: textured, transparent where the art is clear.

    If back_path is given (double-sided), the front texture shows on the front face
    and the back texture on the back face (chosen via geometry backfacing).
    """
    mat = bpy.data.materials.new(name=f"{name}_print")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_principled(bsdf, "Roughness", 0.45)

    front = _image_node(nt, image_path)
    if back_path:
        back = _image_node(nt, back_path, flip_u=True)
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        mix_c = nt.nodes.new("ShaderNodeMix"); mix_c.data_type = "RGBA"
        mix_a = nt.nodes.new("ShaderNodeMix"); mix_a.data_type = "FLOAT"
        nt.links.new(geo.outputs["Backfacing"], mix_c.inputs[0])   # Factor
        nt.links.new(front.outputs["Color"], mix_c.inputs[6])      # A (color)
        nt.links.new(back.outputs["Color"], mix_c.inputs[7])       # B (color)
        nt.links.new(geo.outputs["Backfacing"], mix_a.inputs[0])
        nt.links.new(front.outputs["Alpha"], mix_a.inputs[2])      # A (float)
        nt.links.new(back.outputs["Alpha"], mix_a.inputs[3])       # B (float)
        color_out, alpha_out = mix_c.outputs[2], mix_a.outputs[0]
    else:
        color_out, alpha_out = front.outputs["Color"], front.outputs["Alpha"]

    nt.links.new(color_out, bsdf.inputs["Base Color"])
    nt.links.new(alpha_out, bsdf.inputs["Alpha"])
    # A little self-emission so the ink reads from BOTH sides (the back face is
    # otherwise unlit by the front sun and goes dark behind the clear acrylic).
    if "Emission Color" in bsdf.inputs:
        nt.links.new(color_out, bsdf.inputs["Emission Color"])
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = 0.4
    # The INK must be opaque (only the clear acrylic is see-through). Use alpha
    # clip / dithered, not blend: it writes depth and is order-independent, so the
    # print isn't see-through where there's ink, yet is cut out where the art is
    # clear. (Blend mode made the ink look translucent against the acrylic.)
    for attr, val in (("blend_method", "CLIP"),
                      ("alpha_threshold", 0.5),
                      ("surface_render_method", "DITHERED"),
                      ("show_transparent_back", False),
                      ("use_backface_culling", False)):
        try:
            setattr(mat, attr, val)
        except Exception:
            pass
    return mat


def resolve_texture(fname, src_dir, manifest_dir):
    """Resolve a texture filename: src_dir may be absolute or relative to manifest."""
    cand = os.path.join(src_dir, fname)
    if os.path.isabs(cand) and os.path.exists(cand):
        return cand
    rel = os.path.join(manifest_dir, src_dir, fname)
    if os.path.exists(rel):
        return rel
    # last resort: sitting next to the manifest
    return os.path.join(manifest_dir, fname)


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

    frame = measure_reference_frame(parts[0]["width_px"], parts[0]["height_px"])
    print(f"[frame] world bbox {frame}")

    acrylic_mat = acrylic_material()
    scene_coll = bpy.context.scene.collection

    piece_objs = []   # [(acrylic_obj, print_obj), ...]
    all_objs = []
    for part in parts:
        name = part["name"]
        base = curves_to_mesh(import_svg(os.path.join(manifest_dir, part["svg"])), name)
        assign_uv(base, frame)

        # Print layer: the flat cut-shape carrying the textured (alpha-masked) ink.
        src_dir = part.get("src_dir", "")
        tex_path = resolve_texture(part["texture"], src_dir, manifest_dir)
        if not os.path.exists(tex_path):
            print(f"  WARNING: texture not found for {name}: {tex_path}")
        back_path = None
        if DOUBLE_SIDED and part.get("texture_back"):
            bp = resolve_texture(part["texture_back"], src_dir, manifest_dir)
            if os.path.exists(bp):
                back_path = bp
                print(f"  {name}: using back-side art {part['texture_back']}")
        prt = base
        prt.name = f"{name}_print"
        prt.data.materials.clear()                       # drop the importer's SVGMat
        prt.data.materials.append(print_material(name, tex_path, back_path))

        # Acrylic layer: a copy of the cut-shape, solidified, with the shared material.
        acr = prt.copy()
        acr.data = prt.data.copy()
        acr.name = f"{name}_acrylic"
        acr.data.materials.clear()
        acr.data.materials.append(acrylic_mat)
        solid = acr.modifiers.new("Solidify", "SOLIDIFY")
        solid.thickness = THICKNESS_CM
        solid.offset = 0.0

        # Group the two layers in their own collection.
        coll = bpy.data.collections.new(name)
        scene_coll.children.link(coll)
        for c in list(prt.users_collection):
            c.objects.unlink(prt)
        coll.objects.link(prt)
        coll.objects.link(acr)

        piece_objs.append((acr, prt))
        all_objs.extend((acr, prt))
        print(f"  built {name}")

    # --- real-world scale from the TALLEST piece, not the canvas ---
    # Users paint on arbitrarily large canvases, so pixel size can't imply real size.
    # Map the tallest piece's height to HEIGHT_CM; everything else follows that ratio,
    # and 1 BU becomes 1 cm (so THICKNESS_CM is a true thickness, no mesh shrinking).
    bpy.context.view_layer.update()

    def y_extent(o):
        ys = [(o.matrix_world @ mathutils.Vector(c)).y for c in o.bound_box]
        return max(ys) - min(ys)

    tallest = max(y_extent(p) for _, p in piece_objs) or 1.0
    s = HEIGHT_CM / tallest
    for o in all_objs:
        o.scale = (s, s, s)
    apply_transform(all_objs, scale=True)

    # Stand upright (+X tilt), about the shared origin so alignment is preserved.
    for o in all_objs:
        o.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    apply_transform(all_objs, rotation=True)

    # Drop onto the floor (min Z -> 0) and centre in X, same shift for every object.
    bpy.context.view_layer.update()
    zs, xs = [], []
    for o in all_objs:
        for c in o.bound_box:
            w = o.matrix_world @ mathutils.Vector(c)
            zs.append(w.z)
            xs.append(w.x)
    dz, dx = -min(zs), -(min(xs) + max(xs)) / 2.0
    for o in all_objs:
        o.location.x += dx
        o.location.z += dz

    # Per-object origin at its own geometry centre (intuitive rotation).
    bpy.context.view_layer.update()
    for o in all_objs:
        with bpy.context.temp_override(active_object=o, selected_objects=[o],
                                       selected_editable_objects=[o]):
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")

    # Fan pieces apart in depth (Y); both layers of a piece move together. The print
    # sits just in front of its acrylic sheet's front face so the art is clearly visible.
    pitch = THICKNESS_CM + GAP_CM
    n = len(piece_objs)
    front = THICKNESS_CM / 2.0 + 0.02
    for i, (acr, prt) in enumerate(piece_objs):
        dy = (i - (n - 1) / 2.0) * pitch
        acr.location.y += dy
        prt.location.y += dy - front

    # Remove the empty collections the SVG importer leaves behind (one per .svg).
    for c in list(bpy.data.collections):
        if not c.objects and not c.children:
            try:
                bpy.data.collections.remove(c)
            except Exception:
                pass

    # A sun lamp lighting the standee from the front-above.
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("Sun", sun_data)
    scene_coll.objects.link(sun)
    sun.rotation_euler = (math.radians(62), 0.0, math.radians(18))

    # Ambient world light so the print is also visible from the BACK (the sun only
    # lights the front face; without fill the reverse side reads as black).
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.38, 0.38, 0.42, 1.0)
        bg.inputs[1].default_value = 0.7

    # Make the clear acrylic actually read as transparent (needs EEVEE raytracing),
    # and open the viewport in Rendered shading so you can see THROUGH the acrylic
    # to the pieces behind (and the art from the back). Transmission/alpha don't
    # show in Solid / Material-preview shading.
    try:
        bpy.context.scene.eevee.use_raytracing = True
    except Exception:
        pass

    # Switch the 3D viewport to Rendered once the GUI exists (a timer, because at
    # script time during startup the window/areas aren't ready; in --background
    # there are no windows so this is a harmless no-op).
    def _to_rendered():
        for win in getattr(bpy.context.window_manager, "windows", []):
            scr = win.screen
            if not scr:
                continue
            for area in scr.areas:
                if area.type == "VIEW_3D":
                    for sp in area.spaces:
                        if sp.type == "VIEW_3D":
                            sp.shading.type = "RENDERED"
        return None
    try:
        bpy.app.timers.register(_to_rendered, first_interval=0.4)
    except Exception:
        pass

    print(f"[done] {len(piece_objs)} piece(s)")
    return all_objs


def main():
    args = argv_after_dashes()
    if not args:
        sys.exit("usage: blender ... -- <manifest.json> [out.blend]")
    manifest_path = args[0]
    out_blend = args[1] if len(args) > 1 else None

    build(manifest_path)

    # Optional: embed the textures so the .blend is portable (web UI sets this,
    # since its .blend gets copied into the user's folder away from the images).
    if os.environ.get("ACRYLIC_PACK", "0").lower() in ("1", "true", "yes"):
        try:
            bpy.ops.file.pack_all()
            print("[packed] textures embedded")
        except Exception as e:
            print(f"[pack] skipped: {e}")

    if out_blend:
        out_blend = os.path.abspath(out_blend)
        bpy.ops.wm.save_as_mainfile(filepath=out_blend)
        print(f"[saved] {out_blend}")


if __name__ == "__main__":
    main()
