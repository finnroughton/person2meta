"""
person2meta - Blender startup script
Runs INSIDE Blender (launched via `blender --python blender_startup.py`).

Reads the image paths passed from launch_picker.py, then should:
  1. Create a new FaceBuilder head
  2. Load the selected images into it as views
  3. Leave Blender open with the FaceBuilder panel visible for manual tweaking

The FaceBuilder-specific operator calls below are PLACEHOLDERS.
See the "HOW TO FIND THE REAL OPERATOR NAMES" section at the bottom
before running this for real.
"""

import bpy
import os

def get_image_paths() -> list[str]:
    raw = os.environ.get("P2M_IMAGE_PATHS", "")
    if not raw:
        print("[person2meta] No P2M_IMAGE_PATHS env var found — nothing to load.")
        return []
    return raw.split("||")


def create_facebuilder_head_and_load_images(image_paths: list[str]) -> None:
    """
    PLACEHOLDER — replace the two bpy.ops calls below with FaceBuilder's
    actual operator IDs once you've found them (see instructions below).
    """
    print(f"[person2meta] Would create a new FaceBuilder head here.")
    print(f"[person2meta] Would load {len(image_paths)} image(s) into it:")
    for p in image_paths:
        print(f"    - {p}")

    # --- Example shape of what this will probably look like ---
    # bpy.ops.object.some_facebuilder_create_head_operator()
    #
    # for path in image_paths:
    #     bpy.ops.some_facebuilder_add_image_operator(filepath=path)
    # -----------------------------------------------------------

    print("[person2meta] PLACEHOLDER ONLY — no real FaceBuilder calls made yet. "
          "See blender_startup.py header for how to find the real operator IDs.")


def main():
    image_paths = get_image_paths()
    create_facebuilder_head_and_load_images(image_paths)


if __name__ == "__main__":
    main()


# =====================================================================
# HOW TO FIND THE REAL OPERATOR NAMES (do this once, takes ~1 minute):
#
# 1. In Blender, go to Edit > Preferences > Interface, and enable
#    "Developer Extras" (near the bottom, under Display).
# 2. Open the FaceBuilder panel (press N in the 3D viewport, find the
#    FaceBuilder tab), and manually click "Create New Head".
# 3. Right-click that same button — you'll see an option like
#    "Copy Python Command" or "Online Manual". Click "Copy Python Command".
# 4. Paste it somewhere (a text file, or Blender's own Python Console,
#    Window > Toggle System Console on Windows to see printed output) —
#    this gives you the exact bpy.ops.XXXX(...) call Blender just ran.
# 5. Repeat for "Add Images".
# 6. Drop those exact calls into create_facebuilder_head_and_load_images()
#    above, replacing the placeholder print statements.
#
# Alternative: open Blender's own Python Console (a viewport editor type),
# and watch its output as you click each FaceBuilder button manually —
# operator calls made through the UI are echoed there by default.
# =====================================================================
