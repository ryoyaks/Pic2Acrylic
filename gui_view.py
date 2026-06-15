"""Switch the 3D viewport to Rendered shading once the GUI is up.

Used when opening a built standee:  blender file.blend --python gui_view.py
Rendered shading is what makes the transparent acrylic actually show through to
the pieces behind it (Solid / Material-preview shading don't).
"""

import bpy


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


# run once the window/areas exist
bpy.app.timers.register(_to_rendered, first_interval=0.4)
