"""
person2meta - convert an OBJ to FBX for Unreal import
Called automatically by run_pipeline.py -- you shouldn't need to run this
directly, but if you do:

    "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background --python convert_obj_to_fbx.py -- INPUT.obj OUTPUT.fbx
"""

import os
import sys
import bpy


def fail(message: str) -> None:
    """Print a clear error and exit with a non-zero code, so any calling
    script (like run_pipeline.py) can detect the failure instead of
    silently continuing."""
    print(f"[person2meta] ERROR: {message}")
    sys.exit(1)


# Blender passes its own args before "--"; everything after is ours.
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) != 2:
    fail(
        "Usage: blender --background --python convert_obj_to_fbx.py -- INPUT.obj OUTPUT.fbx\n"
        f"       Got {len(argv)} argument(s) after '--': {argv}"
    )

INPUT_OBJ_PATH, OUTPUT_FBX_PATH = argv

# --- Validate input before touching Blender's state at all ---
if not os.path.isfile(INPUT_OBJ_PATH):
    fail(f"Input OBJ file does not exist: {INPUT_OBJ_PATH}")

if not INPUT_OBJ_PATH.lower().endswith(".obj"):
    print(f"[person2meta] WARNING: input file doesn't end in .obj "
          f"({INPUT_OBJ_PATH}) -- continuing anyway, but double-check this "
          f"is really the mesh file, not a folder or a different file type.")

output_dir = os.path.dirname(OUTPUT_FBX_PATH)
if output_dir and not os.path.isdir(output_dir):
    try:
        os.makedirs(output_dir, exist_ok=True)
        print(f"[person2meta] Created output folder: {output_dir}")
    except OSError as e:
        fail(f"Could not create output folder {output_dir}: {e}")

# --- Clear the default scene (cube/camera/light) so only our mesh exports ---
try:
    bpy.ops.wm.read_factory_settings(use_empty=True)
except Exception as e:
    fail(f"Failed to reset Blender scene: {e}")

# --- Import ---
try:
    bpy.ops.wm.obj_import(filepath=INPUT_OBJ_PATH)
except Exception as e:
    fail(f"Failed to import OBJ '{INPUT_OBJ_PATH}': {e}")

if not bpy.context.scene.objects:
    fail(f"OBJ import reported success but the scene has no objects. "
         f"'{INPUT_OBJ_PATH}' may be empty or corrupt.")

imported_count = len(bpy.context.scene.objects)
print(f"[person2meta] Imported {imported_count} object(s) from OBJ.")

# --- Export ---
try:
    bpy.ops.export_scene.fbx(
        filepath=OUTPUT_FBX_PATH,
        use_selection=False,  # export everything in the scene
    )
except Exception as e:
    fail(f"Failed to export FBX to '{OUTPUT_FBX_PATH}': {e}")

if not os.path.isfile(OUTPUT_FBX_PATH):
    fail(f"Export reported success but no file was found at {OUTPUT_FBX_PATH}.")

file_size_kb = os.path.getsize(OUTPUT_FBX_PATH) / 1024
print(f"[person2meta] Exported FBX to {OUTPUT_FBX_PATH} ({file_size_kb:.1f} KB)")
print("[person2meta] Done.")
