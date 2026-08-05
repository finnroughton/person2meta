"""
person2meta - import FBX, conform to MetaHuman Character, and apply a
properly UV-mapped face texture baked from the source scan.
Run this INSIDE Unreal Engine 5.8's editor, via the Python console or
File > Execute Python Script -- AFTER run_pipeline.py has finished and
written person2meta_config.json.

Reads all paths from the config file, so this script never needs editing
between different heads -- just re-run run_pipeline.py for a new head,
then re-run this script.

Full flow:
  1. Import the scan FBX as a static mesh, extract + re-center its vertex data
     onto MetaHuman's own coordinate space (see the "Re-centering" comment below).
  2. Render a synthetic portrait of the scan from a camera we fully control and
     run face-landmark detection on it (see "Camera/landmark term" below), for
     an accurate (non-guessed) Align/ICP correspondence term.
  3. Conform the MetaHumanCharacter to the scan (manual solve settings tuned
     for likeness -- see the "Named pipeline presets" comment in main()).
  4. Export the conformed head's own geometry (MetaHuman's UV layout) to FBX.
  5. Bake the scan's diffuse texture onto that FBX's UVs via a real 3D surface
     projection in Blender (bake_texture.py, run as a subprocess) -- NOT a
     naive UV copy, which produces a scrambled result since the scan and the
     conformed mesh have completely different UV layouts.
  6. Apply the baked texture as a face Basecolor override and save.

Note: Epic account login happens later, at the auto-rig step (or if you use
Epic's own AI texture synthesis instead of step 5 above) -- not here.

Camera/landmark term: earlier versions of this script used a GUESSED real-world
camera (position/rotation/FOV) paired with face-landmark detection run on the
actual portrait photo, to drive Align's rigid scale/rotation solve. That guessed
camera produced measurably worse results (disfigured AND undersized) than not
using it at all, because the guessed extrinsics don't match the real, unknown
phone camera that took the portrait. Instead, this script now renders its OWN
synthetic portrait of the imported scan from a camera it fully controls (see
`capture_synthetic_portrait_and_track_landmarks` below), then runs the same
landmark detector on that synthetic image. Since the camera parameters are
then *exactly* known (not guessed), Align's correspondence search works
correctly with zero calibration uncertainty.
"""

import json
import os
import subprocess
import time
import unreal

# ---- EDIT ONCE: where run_pipeline.py wrote the config ----
CONFIG_PATH = r"C:\Users\BrianBurritt\Downloads\person2meta_config.json"
# --------------------------------------------------------------

# ---- EDIT ONCE: where this script itself lives, and where Blender is ----
SCRIPTS_DIR = r"C:\Users\BrianBurritt\Documents\person2meta\scripts"
BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
BAKE_SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "bake_texture.py")
# --------------------------------------------------------------

LOG_FILE_PATH = os.path.join(os.path.dirname(CONFIG_PATH), "person2meta_conform_log.txt")


def log(message: str) -> None:
    """Prints to the console AND appends to a file, so progress survives
    even if the editor freezes and has to be force-quit."""
    print(message)
    try:
        with open(LOG_FILE_PATH, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass  # don't let logging failures break the actual script


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


def capture_synthetic_portrait_and_track_landmarks(metahuman_subsystem, target_mesh, mesh_world_location):
    """Spawns target_mesh at mesh_world_location in the current editor level,
    captures a synthetic portrait of it from a camera we fully control, runs
    face landmark detection on that render, and returns everything needed to
    feed an accurate (non-guessed) camera/landmark term into the conform:
    (curve_tracking, camera_location, camera_rotation, fov_deg, image_size).

    mesh_world_location must be the SAME point the re-centered
    conform_target_mesh vertices are expressed relative to, so the returned
    camera_location is directly usable as ConformTargetParams.camera_view_info.
    """
    world = unreal.EditorLevelLibrary.get_editor_world()
    spawned = []
    try:
        mesh_actor = unreal.EditorLevelLibrary.spawn_actor_from_object(target_mesh, mesh_world_location)
        spawned.append(mesh_actor)

        light_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.DirectionalLight, mesh_world_location + unreal.Vector(0, 0, 100)
        )
        spawned.append(light_actor)
        light_actor.set_actor_rotation(unreal.Rotator(pitch=-40, yaw=-60, roll=0), False)
        light_comp = light_actor.get_component_by_class(unreal.DirectionalLightComponent)
        light_comp.set_intensity(6.0)

        sky_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SkyLight, mesh_world_location)
        spawned.append(sky_actor)
        sky_comp = sky_actor.get_component_by_class(unreal.SkyLightComponent)
        sky_comp.set_intensity(1.0)
        sky_comp.recapture_sky()

        # Frame tightly around the actual mesh size instead of a fixed guessed
        # distance, so the face fills most of the frame regardless of scan
        # size -- more pixels on facial features means more precise 2D
        # landmark points and therefore more precise 3D correspondences.
        mesh_extent = target_mesh.get_bounds().box_extent
        half_height = max(mesh_extent.z, mesh_extent.x) * 1.15  # margin so chin/crown aren't clipped
        fov_deg = 35.0
        import math
        distance = half_height / math.tan(math.radians(fov_deg / 2.0))

        camera_location = mesh_world_location + unreal.Vector(0.0, distance, mesh_extent.z * 0.15)
        camera_rotation = unreal.Rotator(pitch=0.0, yaw=-90.0, roll=0.0)
        img_w, img_h = 1024, 1024

        capture_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SceneCapture2D, camera_location)
        spawned.append(capture_actor)
        capture_actor.set_actor_rotation(camera_rotation, False)
        capture_comp = capture_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
        capture_comp.fov_angle = fov_deg
        capture_comp.capture_source = unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR
        capture_comp.composite_mode = unreal.SceneCaptureCompositeMode.SCCM_OVERWRITE

        render_target = unreal.RenderingLibrary.create_render_target2d(
            world, img_w, img_h, unreal.TextureRenderTargetFormat.RTF_RGBA8
        )
        capture_comp.texture_target = render_target
        capture_comp.capture_scene()

        pixels = unreal.RenderingLibrary.read_render_target(world, render_target, normalize=False)

        result = metahuman_subsystem.track_face_landmarks_from_image(pixels, img_w, img_h)
        if isinstance(result, tuple) and len(result) == 1:
            result = result[0]
        curve_tracking = result if (result and hasattr(result, "items")) else None
        if not curve_tracking:
            raise RuntimeError("No face landmarks detected on synthetic render -- check camera framing")

        image_size = unreal.IntPoint(img_w, img_h)
        return curve_tracking, camera_location, camera_rotation, fov_deg, image_size

    finally:
        for actor in spawned:
            unreal.EditorLevelLibrary.destroy_actor(actor)


def export_conformed_geometry_fbx(metahuman_subsystem, character, export_project_path, fbx_out_path):
    """Duplicates character's current (conformed) face mesh out to its own
    SkeletalMesh asset, then exports that to an FBX file on disk. This mesh
    uses MetaHuman's own UV layout -- it's the bake target for
    run_blender_texture_bake, which projects the scan's texture onto it."""
    if not metahuman_subsystem.try_add_object_to_edit(character):
        raise RuntimeError("Unable to edit asset for geometry export, is it already open for edit?")
    try:
        params = unreal.MetaHumanGeometryExportParams()
        params.project_path = export_project_path
        params.head_skeletal_mesh = True
        params.body_skeletal_mesh = False
        params.full_body_skeletal_mesh = False
        params.overwrite_existing_assets = True
        unreal.MetaHumanCharacterExportBlueprintLibrary.export_geometry(character, params)

        expected_path = f"{export_project_path}/{character.get_name()}_Head"
        head_mesh = unreal.load_asset(expected_path)
        if head_mesh is None:
            raise RuntimeError(f"Geometry export did not produce an asset at {expected_path}")
        log(f"[person2meta] Exported conformed head geometry: {head_mesh.get_path_name()}")

        os.makedirs(os.path.dirname(fbx_out_path), exist_ok=True)
        task = unreal.AssetExportTask()
        task.object = head_mesh
        task.filename = fbx_out_path
        task.automated = True
        task.replace_identical = True
        task.prompt = False
        if not unreal.Exporter.run_asset_export_task(task) or not os.path.exists(fbx_out_path):
            raise RuntimeError(f"FBX export failed or did not produce {fbx_out_path}")
        log(f"[person2meta] Exported conformed head FBX: {fbx_out_path}")
    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)


def run_blender_texture_bake(source_scan_fbx, conformed_head_fbx, output_texture_path):
    """Runs bake_texture.py in Blender (a separate process) to project the
    scan's diffuse texture onto the conformed head's own UV layout via a real
    3D surface bake -- NOT a naive UV copy, which produces a scrambled result
    since the scan and the conformed mesh have completely different UV
    layouts. Raises if Blender exits non-zero or doesn't produce the output."""
    log("[person2meta] Baking scan texture onto conformed head's UV layout (Blender)...")
    result = subprocess.run(
        [BLENDER_EXE, "--background", "--python", BAKE_SCRIPT_PATH,
         "--", source_scan_fbx, conformed_head_fbx, output_texture_path],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("[person2meta]"):
            log(line)
    if result.returncode != 0 or not os.path.exists(output_texture_path):
        log("[person2meta] Blender bake FAILED. stdout tail:")
        for line in result.stdout.splitlines()[-25:]:
            log(f"  {line}")
        log("[person2meta] stderr tail:")
        for line in result.stderr.splitlines()[-25:]:
            log(f"  {line}")
        raise RuntimeError(f"Blender texture bake failed (exit code {result.returncode})")
    log(f"[person2meta] Baked texture written to {output_texture_path}")


def apply_baked_face_texture(metahuman_subsystem, character, asset_path, import_destination_path, baked_texture_path):
    """Imports baked_texture_path and wires it in as the face Basecolor
    texture override on character, then saves. Requires character to NOT
    already be added for editing (this function manages its own edit
    session)."""
    log(f"[person2meta] Importing {baked_texture_path} as a texture asset...")
    task = unreal.AssetImportTask()
    task.filename = baked_texture_path
    task.destination_path = import_destination_path
    task.destination_name = "T_" + os.path.splitext(os.path.basename(baked_texture_path))[0]
    task.replace_existing = True
    task.automated = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    texture_path = f"{import_destination_path}/{task.destination_name}"
    face_texture = unreal.load_asset(texture_path)
    if face_texture is None:
        raise RuntimeError(f"Texture import did not produce an asset at {texture_path}")
    log(f"[person2meta] Imported texture: {face_texture.get_path_name()}")

    if not metahuman_subsystem.try_add_object_to_edit(character):
        raise RuntimeError("Unable to edit asset for texture override, is it already open for edit?")
    try:
        skin_settings = character.skin_settings
        skin_settings.texture_material_overrides.enable_texture_overrides = True
        skin_settings.texture_material_overrides.texture_overrides.face = {
            unreal.FaceTextureType.BASECOLOR: face_texture
        }
        # First commit on a brand-new character takes ApplySkinSettings' AI-texture-
        # synthesis branch (CharacterData->SkinSettings isn't set yet), which ignores
        # our face override. That first call seeds CharacterData->SkinSettings, so a
        # second identical commit takes the "use provided textures" branch instead,
        # which actually applies our override to the face material.
        metahuman_subsystem.commit_skin_settings(character, skin_settings)
        metahuman_subsystem.commit_skin_settings(character, skin_settings)
        unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=True)
        log(f"[person2meta] Applied face Basecolor texture override and saved {asset_path}")
    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)


def main():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"Config not found at {CONFIG_PATH}. Run run_pipeline.py first, "
            f"or check CONFIG_PATH points at the right place."
        )
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    fbx_path = config["fbx_path"]
    import_destination_path = config["import_destination_path"]
    imported_mesh_name = config["imported_mesh_name"]
    output_package_path = config["output_package_path"]
    output_asset_name = config["output_asset_name"]

    metahuman_subsystem = unreal.get_editor_subsystem(
        unreal.MetaHumanCharacterEditorSubsystem
    )

    log(f"[person2meta] Importing FBX for '{config['head_name']}'...")
    target_mesh = import_fbx_as_static_mesh(
        fbx_path, import_destination_path, imported_mesh_name
    )
    log(f"[person2meta] Imported mesh: {target_mesh.get_path_name()}")

    log("[person2meta] Extracting target mesh topology...")
    body_vertices, body_indices, *_ = metahuman_subsystem.get_mesh_data_for_conforming(target_mesh)
    log(f"[person2meta] Target mesh: {len(body_vertices)} verts, {len(body_indices) // 3} tris")

    # Our imported scan lands wherever its own OBJ/FBX local origin puts it (near
    # world origin), but the solver's camera/landmark correspondence search and
    # ICP fitting expect the target mesh to sit in MetaHuman's own coordinate
    # space, where the head is ~150-165cm up (see /MetaHumanCharacter/Face/SKM_Face).
    # Without this, ray-mesh intersection for landmark triangulation misses
    # entirely ("insufficient correspondences") and ICP has nothing nearby to
    # latch onto. Re-center our vertices onto the archetype head's position.
    archetype_face = unreal.load_asset("/MetaHumanCharacter/Face/SKM_Face")
    if archetype_face is None:
        raise RuntimeError("Could not load archetype /MetaHumanCharacter/Face/SKM_Face for alignment reference")
    archetype_origin = archetype_face.get_bounds().origin
    target_origin = target_mesh.get_bounds().origin
    offset = unreal.Vector(
        archetype_origin.x - target_origin.x,
        archetype_origin.y - target_origin.y,
        archetype_origin.z - target_origin.z,
    )
    log(f"[person2meta] Re-centering target mesh onto MetaHuman head-space by {offset}")
    body_vertices = [unreal.Vector3f(v.x + offset.x, v.y + offset.y, v.z + offset.z) for v in body_vertices]

    log("[person2meta] Capturing synthetic portrait and running face landmark detection...")
    curve_tracking, camera_location, camera_rotation, fov_deg, image_size = (
        capture_synthetic_portrait_and_track_landmarks(metahuman_subsystem, target_mesh, offset)
    )
    log(f"[person2meta] Detected {len(curve_tracking)} contour curves on synthetic render.")

    log("[person2meta] Creating MetaHumanCharacter asset...")
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
        log("[person2meta] Assembling conform parameters...")
        conform_params = unreal.ConformTargetParams()
        conform_params.conform_target_mesh.target_parts_type = unreal.TargetPartsType.HEAD_ONLY
        conform_params.conform_target_mesh.head_vertices = body_vertices
        conform_params.conform_target_mesh.head_vertex_indices = body_indices

        # Named pipeline presets (e.g. "head_only") use FIXED weights baked into
        # pipeline_presets.json and can't be tuned per-call. Those defaults regularize
        # fairly aggressively toward the generic archetype shape, which is why the
        # result drifts away from the actual scan's likeness. Going manual
        # (auto_solve=False) exposes every weight directly so we can loosen
        # regularization and increase ICP fidelity to keep more of the true scan
        # shape. Note the manual path skips the Align step entirely (it goes straight
        # to ICP fitting) -- fine here since our vertices are already correctly
        # positioned via the re-centering fix above.
        conform_params.auto_solve = False
        settings = conform_params.body_conform_solve_settings

        def weight_schedule(start, end=None, curve=unreal.WeightScheduleCurve.STATIC):
            ws = unreal.WeightSchedule()
            ws.start = start
            ws.end = start if end is None else end
            ws.curve = curve
            return ws

        settings.solve_pose = True
        settings.iterations = 20
        settings.icp_geometry_weight = weight_schedule(100.0)
        settings.icp_search_tolerance = weight_schedule(50.0)
        settings.icp_normal_compatibility = weight_schedule(0.8)
        settings.icp_key_point_weight = weight_schedule(1.0)
        settings.icp_landmarks_weight = weight_schedule(0.557, 0.0, unreal.WeightScheduleCurve.LINEAR)
        # Regularization: loosened from the head_only preset's defaults (~0.68/1.0/1.0)
        # so the solve can deviate further from the generic archetype toward the scan.
        settings.regularization_global_controls = weight_schedule(0.3)
        settings.regularization_local_controls = weight_schedule(0.5)
        settings.regularization_proportions = weight_schedule(0.3)
        settings.regularization_pose = weight_schedule(2.0, 0.5, unreal.WeightScheduleCurve.LINEAR)
        settings.curve_resampling = 5
        settings.face_iterations = 15
        settings.face_icp_weight = weight_schedule(60.0)  # up from preset's 30/50
        settings.face_icp_search_tolerance = weight_schedule(20.0)
        settings.face_normal_compatibility = weight_schedule(0.8)
        settings.face_keypoint_weight = weight_schedule(10.0)
        settings.face_landmark2d_weight = weight_schedule(0.2)
        # Model regularization: the single biggest lever for likeness -- "higher = less
        # face deformation" -- lowered from the preset's 10 so the face patches can
        # actually deform to match the scan instead of snapping back toward archetype.
        settings.model_regularization = weight_schedule(3.0)
        settings.patch_smoothness = weight_schedule(1.0)
        settings.landmark_damping = 0.01
        settings.apply_neck_seam_smoothing = True
        settings.seam_iterations = 3
        settings.seam_laplacian = 1.5
        settings.seam_rings = 12

        view_info = unreal.MinimalViewInfo()
        view_info.location = camera_location
        view_info.rotation = camera_rotation
        view_info.fov = fov_deg
        view_info.aspect_ratio = float(image_size.x) / float(image_size.y)
        view_info.projection_mode = unreal.CameraProjectionMode.PERSPECTIVE
        conform_params.curve_tracking_points = curve_tracking
        conform_params.camera_view_info = view_info
        conform_params.image_size = image_size

        target_mesh_key = unreal.MetaHumanCharacterTargetMeshKey()
        target_mesh_key.head_mesh = target_mesh

        log(f"[person2meta] Running conform ({len(curve_tracking)} face curves)...")
        if not metahuman_subsystem.conform_to_target_meshes(character, target_mesh_key, conform_params):
            raise RuntimeError("conform_to_target_meshes failed")

        metahuman_subsystem.commit_posed_state_as_a_pose(character, target_mesh_key)
        unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=True)
        log(f"[person2meta] Conform complete -- saved {asset_path}")
        conform_succeeded = True

    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)
        if not conform_succeeded:
            unreal.log_warning(
                f"Conform did not succeed -- {asset_path} may need manual cleanup."
            )

    if conform_succeeded:
        work_dir = os.path.join(os.path.dirname(CONFIG_PATH), f"{config['head_name']}_bake_work")
        conformed_head_fbx = os.path.join(work_dir, "conformed_head.fbx")
        baked_texture_path = os.path.join(work_dir, "baked_face_texture.png")

        export_conformed_geometry_fbx(
            metahuman_subsystem, character, f"{import_destination_path}/export", conformed_head_fbx
        )
        run_blender_texture_bake(fbx_path, conformed_head_fbx, baked_texture_path)
        apply_baked_face_texture(
            metahuman_subsystem, character, asset_path, import_destination_path, baked_texture_path
        )


if __name__ == "__main__":
    main()
