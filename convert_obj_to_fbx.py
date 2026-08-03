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

# --- Normalize to a realistic real-world size. ---
# KeenTools' reconstruction isn't calibrated to true physical scale (there's
# no reference object in an ordinary photo to derive that from), so the raw
# mesh can come out at an arbitrary size -- we saw ~3.75m tall in one test,
# when a head+neck+shoulders bust should realistically be ~0.35-0.45m tall.
# Rather than trust KeenTools' raw units, measure the actual bounding box
# and rescale to a target height ourselves.
TARGET_HEIGHT_METERS = 0.4  # reasonable placeholder for a head+neck+shoulders bust

bpy.ops.object.select_all(action='SELECT')
# Compute the combined bounding box across all selected objects, in world space.
min_z = min(
    (obj.matrix_world @ v.co).z
    for obj in bpy.context.selected_objects if obj.type == 'MESH'
    for v in obj.data.vertices
)
max_z = max(
    (obj.matrix_world @ v.co).z
    for obj in bpy.context.selected_objects if obj.type == 'MESH'
    for v in obj.data.vertices
)
current_height = max_z - min_z
if current_height <= 0:
    fail(f"Computed a non-positive height ({current_height}) -- mesh data looks broken.")

scale_factor = TARGET_HEIGHT_METERS / current_height
print(f"[person2meta] Raw height: {current_height:.4f} units. "
      f"Scaling by {scale_factor:.6f} to reach target height {TARGET_HEIGHT_METERS}m.")

bpy.ops.transform.resize(value=(scale_factor, scale_factor, scale_factor))

# --- Bake any object-level transform (scale/rotation from the OBJ import)
# into the actual vertex data, so it can't interact unpredictably with the
# FBX export's own scale/axis settings below. ---
try:
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
except Exception as e:
    fail(f"Failed to apply object transforms before export: {e}")

# --- Export ---
try:
    bpy.ops.export_scene.fbx(
        filepath=OUTPUT_FBX_PATH,
        use_selection=False,  # export everything in the scene
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options='FBX_SCALE_ALL',  # bakes any object scale into the actual vertex data
        axis_forward='-Z',  # standard Blender->Unreal convention
        axis_up='Y',        # standard Blender->Unreal convention
    )
except Exception as e:
    fail(f"Failed to export FBX to '{OUTPUT_FBX_PATH}': {e}")

if not os.path.isfile(OUTPUT_FBX_PATH):
    fail(f"Export reported success but no file was found at {OUTPUT_FBX_PATH}.")

file_size_kb = os.path.getsize(OUTPUT_FBX_PATH) / 1024
print(f"[person2meta] Exported FBX to {OUTPUT_FBX_PATH} ({file_size_kb:.1f} KB)")
print("[person2meta] Done.")
