"""
person2meta - convert an OBJ to FBX for Unreal import
Called automatically by run_pipeline.py -- you shouldn't need to run this
directly, but if you do:

    "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background --python convert_obj_to_fbx.py -- INPUT.obj OUTPUT.fbx
"""

import sys
import bpy

# Blender passes its own args before "--"; everything after is ours.
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) != 2:
    raise SystemExit(
        "[person2meta] Usage: blender --background --python convert_obj_to_fbx.py -- INPUT.obj OUTPUT.fbx"
    )
INPUT_OBJ_PATH, OUTPUT_FBX_PATH = argv

# Clear the default scene (cube/camera/light) so only our imported mesh exports.
bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.wm.obj_import(filepath=INPUT_OBJ_PATH)

bpy.ops.export_scene.fbx(
    filepath=OUTPUT_FBX_PATH,
    use_selection=False,  # export everything in the scene
)

print(f"[person2meta] Exported FBX to {OUTPUT_FBX_PATH}")
