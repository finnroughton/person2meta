"""
person2meta - import FBX + conform to MetaHuman Character
Run this INSIDE Unreal Engine 5.8's editor, via the Python console or
File > Execute Python Script -- AFTER run_pipeline.py has finished and
written person2meta_config.json.

Reads all paths from the config file, so this script never needs editing
between different heads -- just re-run run_pipeline.py for a new head,
then re-run this script.

Two honest caveats carried over from the original example:
  1. CAMERA_LOCATION / CAMERA_ROTATION / CAMERA_FOV_DEG below are a
     REASONABLE GUESS for a typical front-facing portrait photo, not a
     real measured value. If the conform comes out visibly skewed, these
     are the first thing to adjust.
  2. Epic account login happens later, at the auto-rig / texture step,
     not here.
"""

import json
import os
import unreal

# ---- EDIT ONCE: where run_pipeline.py wrote the config ----
CONFIG_PATH = r"C:\Users\BrianBurritt\Downloads\person2meta_config.json"
# --------------------------------------------------------------

# Rough guess for a typical front-facing portrait -- not a measured value.
CAMERA_LOCATION = unreal.Vector(0.0, 100.0, 165.0)
CAMERA_ROTATION = unreal.Rotator(pitch=0.0, yaw=-90.0, roll=0.0)
CAMERA_FOV_DEG = 40.0


def import_fbx_as_static_mesh(fbx_path: str, destination_path: str, asset_name: str):
    task = unreal.AssetImportTask()
    task.filename = fbx_path
    task.destination_path = destination_path
    task.destination_name = asset_name
    task.replace_existing = True
    task.automated = True
    task.save = True

    options = unreal.FbxImportUI()
    options.import_mesh = True
    options.import_as_skeletal = False
    task.options = options

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    # Don't guess from imported_object_paths -- an FBX import creates several
    # sub-assets (textures, materials, etc.) and their order isn't reliable.
    # Load the actual static mesh directly by the exact name we requested.
    expected_path = f"{destination_path}/{asset_name}"
    mesh_asset = unreal.load_asset(expected_path)
    if mesh_asset is None:
        raise RuntimeError(
            f"Import ran, but no asset found at {expected_path}. "
            f"Check the FBX actually contains a mesh named '{asset_name}', "
            f"or inspect task.get_editor_property('imported_object_paths') "
            f"to see what was actually created."
        )
    if not isinstance(mesh_asset, unreal.StaticMesh):
        raise RuntimeError(
            f"Asset at {expected_path} exists but is a {type(mesh_asset).__name__}, "
            f"not a StaticMesh. Something about the import produced the wrong asset type."
        )
    return mesh_asset


def main():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"Config not found at {CONFIG_PATH}. Run run_pipeline.py first, "
            f"or check CONFIG_PATH points at the right place."
        )
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    fbx_path = config["fbx_path"]
    portrait_path = config["portrait_path"]
    import_destination_path = config["import_destination_path"]
    imported_mesh_name = config["imported_mesh_name"]
    output_package_path = config["output_package_path"]
    output_asset_name = config["output_asset_name"]

    metahuman_subsystem = unreal.get_editor_subsystem(
        unreal.MetaHumanCharacterEditorSubsystem
    )

    print(f"[person2meta] Importing FBX for '{config['head_name']}'...")
    target_mesh = import_fbx_as_static_mesh(
        fbx_path, import_destination_path, imported_mesh_name
    )
    print(f"[person2meta] Imported mesh: {target_mesh.get_path_name()}")

    print("[person2meta] Loading portrait and running face landmark detection...")
    image_size, pixels = unreal.PromotedFrameUtils.get_promoted_frame_as_pixel_array_from_disk(
        portrait_path
    )
    if image_size.x <= 0 or image_size.y <= 0:
        raise RuntimeError(f"Failed to load portrait image at {portrait_path}")

    result = metahuman_subsystem.track_face_landmarks_from_image(
        pixels, image_size.x, image_size.y
    )
    if isinstance(result, tuple) and len(result) == 1:
        result = result[0]
    curve_tracking = result if (result and hasattr(result, "items")) else None
    if not curve_tracking:
        raise RuntimeError(
            "No face landmarks detected. Try a clearer, well-lit, "
            "front-facing photo with eyes open."
        )
    print(f"[person2meta] Detected {len(curve_tracking)} contour curves.")

    print("[person2meta] Extracting target mesh topology...")
    body_vertices, body_indices, *_ = metahuman_subsystem.get_mesh_data_for_conforming(target_mesh)
    print(f"[person2meta] Target mesh: {len(body_vertices)} verts, {len(body_indices) // 3} tris")

    print("[person2meta] Creating MetaHumanCharacter asset...")
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    asset_path = f"{output_package_path}/{output_asset_name}"
    character = asset_tools.create_asset(
        asset_name=output_asset_name,
        package_path=output_package_path,
        asset_class=unreal.MetaHumanCharacter,
        factory=unreal.new_object(type=unreal.MetaHumanCharacterFactoryNew),
    )
    if character is None:
        raise RuntimeError(f"Failed to create character asset at {asset_path}")

    if not metahuman_subsystem.try_add_object_to_edit(character):
        raise RuntimeError("Unable to edit asset, is it already open for edit?")

    conform_succeeded = False
    try:
        print("[person2meta] Assembling conform parameters...")
        conform_params = unreal.ConformTargetParams()
        conform_params.conform_target_mesh.target_parts_type = unreal.TargetPartsType.HEAD_ONLY
        conform_params.conform_target_mesh.head_vertices = body_vertices
        conform_params.conform_target_mesh.head_vertex_indices = body_indices
        conform_params.auto_solve = True
        conform_params.body_conform_solve_settings.pipeline_name = "combined"

        view_info = unreal.MinimalViewInfo()
        view_info.location = CAMERA_LOCATION
        view_info.rotation = CAMERA_ROTATION
        view_info.fov = CAMERA_FOV_DEG
        view_info.aspect_ratio = float(image_size.x) / float(image_size.y)
        view_info.projection_mode = unreal.CameraProjectionMode.PERSPECTIVE
        conform_params.curve_tracking_points = curve_tracking
        conform_params.camera_view_info = view_info
        conform_params.image_size = image_size

        target_mesh_key = unreal.MetaHumanCharacterTargetMeshKey()
        target_mesh_key.head_mesh = target_mesh

        print(f"[person2meta] Running conform ({len(curve_tracking)} face curves)...")
        if not metahuman_subsystem.conform_to_target_meshes(character, target_mesh_key, conform_params):
            raise RuntimeError("conform_to_target_meshes failed")

        metahuman_subsystem.commit_posed_state_as_a_pose(character, target_mesh_key)
        unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=True)
        print(f"[person2meta] Conform complete -- saved {asset_path}")
        conform_succeeded = True

    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)
        if not conform_succeeded:
            unreal.log_warning(
                f"Conform did not succeed -- {asset_path} may need manual cleanup."
            )


if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
