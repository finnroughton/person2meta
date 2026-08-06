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
  7. Auto-rig the character (joints + blendshapes) via Epic's cloud
     auto-rigging service, so the result is animation-ready, not just a
     static conformed mesh. Requires being logged into an Epic account in
     the editor -- the request blocks until the service responds and, on a
     first-ever run, may prompt an Epic sign-in the first time it's called.
  8. If an audio file was picked (Blender's Audio/Video prompt -- see
     blender_startup.py), run it through MetaHuman's built-in AI Audio
     Driven Animation (adapted from Epic's own
     process_audio_performance.py / export_performance.py examples) and
     export the result as an AnimSequence on the shared MetaHuman face
     skeleton. Video input is NOT handled here yet -- Epic's own pipeline
     for that expects footage from the LiveLink Face capture app, not an
     arbitrary video file, which is a bigger separate effort.

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
import sys
import time
import unreal

# Unreal's -ExecutePythonScript doesn't reliably add this script's own
# directory to sys.path -- add it explicitly so `import p2m_settings` works.
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import p2m_settings

# All machine-specific paths now live in one place -- see p2m_settings.py
# (and person2meta_settings.json, created next to it) to edit them.
_settings = p2m_settings.load_settings()

# Fallback default; blender_startup.py normally overrides this per-head via
# the P2M_CONFIG_PATH env var -- see that script's _try_export_and_build for
# why: a single shared config file gets clobbered when exporting a second
# head in a multi-head session before the first head's Unreal instance has
# even finished starting up and reading it.
CONFIG_PATH = os.environ.get("P2M_CONFIG_PATH") or os.path.join(
    _settings["work_dir"], "person2meta_config.json"
)
BLENDER_EXE = _settings["blender_exe"]
BAKE_SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "bake_texture.py")

# Testing whether the synthetic-camera/landmark term (Align's rigid
# scale/rotation solve) is distorting the conform result -- suspected of
# shrinking/reshaping the face region relative to the head, which would
# explain the "smaller face with a visible seam" symptom in the baked
# texture as a conform-geometry issue, not a texture-bake issue. The old
# KeenTools-cloud pipeline's own history (see README) found this term made
# results WORSE, not better, for the same reason -- worth re-testing here.
USE_CAMERA_LANDMARK_TERM = False
# --------------------------------------------------------------

LOG_FILE_PATH = (
    os.environ.get("P2M_CONFORM_LOG_PATH")
    or os.path.join(os.path.dirname(CONFIG_PATH), "person2meta_conform_log.txt")
)


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
    # baked_texture_path is always named "baked_face_texture.png" (only the
    # containing per-head folder differs), so basing the asset name on the
    # file's own basename produced the SAME asset name ("T_baked_face_texture")
    # every single run. replace_existing=True should overwrite it correctly,
    # but Unreal's asset registry/editor can hang onto stale cached texture
    # data for an already-loaded/previously-viewed asset of the same name --
    # naming it after the actual character instead guarantees a fresh, unique
    # asset every run.
    task.destination_name = f"T_{os.path.basename(asset_path)}_face_texture"
    task.replace_existing = True
    task.automated = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    texture_path = f"{import_destination_path}/{task.destination_name}"
    face_texture = unreal.load_asset(texture_path)
    if face_texture is None:
        raise RuntimeError(f"Texture import did not produce an asset at {texture_path}")
    log(f"[person2meta] Imported texture: {face_texture.get_path_name()}")

    _apply_face_texture_override(metahuman_subsystem, character, asset_path, face_texture)


def _apply_face_texture_override(metahuman_subsystem, character, asset_path, face_texture):
    """Shared by apply_baked_face_texture and apply_synthesized_texture_override
    -- wires face_texture in as the face Basecolor texture override on
    character and saves. Requires character to NOT already be added for
    editing (this function manages its own edit session)."""
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
        log(f"[person2meta] Applied face Basecolor texture override "
            f"({face_texture.get_path_name()}) and saved {asset_path}")
    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)


def apply_synthesized_texture_override(metahuman_subsystem, character, asset_path, texture_asset_path):
    """Loads MetaHuman's OWN auto-synthesized texture (already sitting in the
    content folder after conform/commit_skin_settings first ran, named
    "<head_name>_texture") and applies it as an EXPLICIT face Basecolor
    override, instead of leaving it as an implicit default. Returns True if
    found and applied, False (non-fatal, logs a warning) if no texture
    exists at that path yet."""
    face_texture = unreal.load_asset(texture_asset_path)
    if face_texture is None:
        log(f"[person2meta] WARNING: No synthesized texture found at "
            f"{texture_asset_path} -- skipping explicit override.")
        return False
    log(f"[person2meta] Found synthesized texture: {face_texture.get_path_name()}")
    _apply_face_texture_override(metahuman_subsystem, character, asset_path, face_texture)
    return True


def export_synced_level_sequence(metahuman_subsystem, character, anim_sequence, sound_wave,
                                 output_package_path: str, head_name: str):
    """Builds a Level Sequence combining a real MetaHuman actor (playing
    anim_sequence on its Face component) with an Audio Track for
    sound_wave, so opening ONE asset and pressing play lets you see the
    facial animation AND hear the audio that drove it, together, in sync --
    neither the AnimSequence nor the SoundWave alone plays back the other.

    Epic's own export_performance.py (run_meta_human_level_sequence_export)
    does something similar, but needs an existing MetaHuman BLUEPRINT asset
    as a target -- this pipeline builds a MetaHumanCharacter directly and
    never creates a placeable Blueprint (that's a separate manual "Create
    MetaHuman" step in the normal workflow). Spawning a real actor straight
    from the character via MetaHumanCharacterEditorSubsystem's
    spawn_meta_human_actor (confirmed UFUNCTION(BlueprintCallable), so
    Python-exposed) sidesteps needing that Blueprint at all.

    Non-fatal on any failure (logs a warning and returns) -- this is a
    convenience preview; the actual character + AnimSequence are already
    saved by the time this runs regardless of whether it succeeds."""
    try:
        actor = metahuman_subsystem.spawn_meta_human_actor(character, False)
        if actor is None:
            log("[person2meta] WARNING: Could not spawn a MetaHuman actor -- "
                "skipping synced audio+animation preview.")
            return

        # MetaHuman actors have several skeletal mesh components (face, body,
        # etc) -- export_performance.py's own is_meta_human_binding() helper
        # confirms "Face" is the real component name to look for.
        components = actor.get_components_by_class(unreal.SkeletalMeshComponent)
        face_component = next((c for c in components if "face" in c.get_name().lower()), None)
        if face_component is None and components:
            face_component = components[0]
        if face_component is None:
            log("[person2meta] WARNING: Spawned MetaHuman actor has no skeletal "
                "mesh component -- skipping synced audio+animation preview.")
            return

        length_seconds = sound_wave.get_editor_property("duration") or 5.0

        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        sequence = asset_tools.create_asset(
            asset_name=f"LS_{head_name}_Synced", package_path=output_package_path,
            asset_class=unreal.LevelSequence, factory=unreal.LevelSequenceFactoryNew(),
        )
        if sequence is None:
            log("[person2meta] WARNING: Could not create Level Sequence asset -- "
                "skipping synced audio+animation preview.")
            return

        actor_binding = sequence.add_possessable(actor)
        face_binding = sequence.add_possessable(face_component)
        face_binding.set_parent(actor_binding)

        anim_track = face_binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
        anim_section = anim_track.add_section()
        anim_section.params.animation = anim_sequence
        anim_section.set_start_frame_seconds(0)
        anim_section.set_end_frame_seconds(length_seconds)

        audio_track = sequence.add_track(unreal.MovieSceneAudioTrack)
        audio_section = audio_track.add_section()
        audio_section.set_sound(sound_wave)
        audio_section.set_start_frame_seconds(0)
        audio_section.set_end_frame_seconds(length_seconds)

        sequence.set_playback_start_seconds(0)
        sequence.set_playback_end_seconds(length_seconds)

        unreal.EditorAssetLibrary.save_asset(sequence.get_path_name(), only_if_is_dirty=False)
        log(f"[person2meta] Synced audio+animation preview: {sequence.get_path_name()} "
            f"(open it and press play to see and hear the result together).")
    except Exception as e:
        log(f"[person2meta] WARNING: Could not build synced level sequence: "
            f"{type(e).__name__}: {e}")


def run_audio_driven_animation(metahuman_subsystem, character, audio_path: str,
                               import_destination_path: str, output_package_path: str,
                               head_name: str):
    """Imports audio_path as a SoundWave, runs it through MetaHuman's
    built-in AI Audio Driven Animation (a MetaHumanPerformance asset with
    input_type=AUDIO), and exports the solved result as an AnimSequence on
    the shared MetaHuman face skeleton. Adapted from Epic's own
    process_audio_performance.py and export_performance.py examples
    (Engine/Plugins/MetaHuman/MetaHumanAnimator/Content/Python/) -- audio
    input doesn't need a per-character Identity/footage setup the way video
    does, so the animation solve itself is self-contained, not tied to a
    specific MetaHumanCharacter asset (metahuman_subsystem/character are
    only used afterward, to build a synced preview -- see
    export_synced_level_sequence). Non-fatal on failure (logs and returns)
    since the MetaHuman itself is already built by this point regardless."""
    log(f"[person2meta] Importing audio for animation: {audio_path}")
    task = unreal.AssetImportTask()
    task.filename = audio_path
    task.destination_path = import_destination_path
    task.destination_name = f"SW_{head_name}_audio"
    task.replace_existing = True
    task.automated = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    sound_wave_path = f"{import_destination_path}/{task.destination_name}"
    sound_wave = unreal.load_asset(sound_wave_path)
    if sound_wave is None:
        log(f"[person2meta] WARNING: Audio import did not produce an asset at "
            f"{sound_wave_path} -- skipping animation.")
        return

    log("[person2meta] Requesting audio-driven animation...")
    performance_name = f"{head_name}_Performance"
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    performance_asset = asset_tools.create_asset(
        asset_name=performance_name, package_path=output_package_path,
        asset_class=unreal.MetaHumanPerformance, factory=unreal.MetaHumanPerformanceFactoryNew(),
    )
    if performance_asset is None:
        log(f"[person2meta] WARNING: Could not create Performance asset "
            f"'{performance_name}' -- skipping animation.")
        return

    # set_editor_property (not plain attribute assignment) is required here --
    # it's what actually triggers the PostEditChangeProperty callback that
    # sets up the Performance asset internally (same note as in Epic's own
    # process_audio_performance.py).
    performance_asset.set_editor_property("input_type", unreal.DataInputType.AUDIO)
    performance_asset.set_editor_property("audio", sound_wave)

    solve_overrides = unreal.AudioDrivenAnimationSolveOverrides()
    solve_overrides.mood = unreal.AudioDrivenAnimationMood.AUTO_DETECT
    solve_overrides.mood_intensity = 1.0
    performance_asset.set_editor_property("audio_driven_animation_solve_overrides", solve_overrides)
    performance_asset.set_editor_property(
        "audio_driven_animation_output_controls", unreal.AudioDrivenAnimationOutputControls.FULL_FACE
    )
    performance_asset.set_editor_property(
        "head_movement_mode", unreal.PerformanceHeadMovementMode.CONTROL_RIG
    )

    performance_asset.set_blocking_processing(True)
    log("[person2meta] Running audio-driven animation solve (this can take a bit)...")
    start_pipeline_error = performance_asset.start_pipeline()
    if start_pipeline_error != unreal.StartPipelineErrorType.NONE:
        log(f"[person2meta] WARNING: Audio-driven animation pipeline failed to "
            f"start ({start_pipeline_error}) -- skipping export.")
        return

    log("[person2meta] Exporting animation sequence...")
    export_settings = unreal.MetaHumanPerformanceExportAnimationSettings()
    export_settings.show_export_dialog = False
    export_settings.package_path = output_package_path
    export_settings.asset_name = f"AS_{head_name}"
    # Same archetype skeleton conform_to_metahuman.py already references for
    # alignment (/MetaHumanCharacter/Face/SKM_Face) -- the plugin's own
    # content mount, not Epic's example's /Game/MetaHumans/Common/... path,
    # which only exists in projects that downloaded a MetaHuman via Bridge.
    target_skeleton = unreal.load_asset("/MetaHumanCharacter/Face/Face_Archetype_Skeleton")
    if target_skeleton is None:
        log("[person2meta] WARNING: Could not load Face_Archetype_Skeleton -- skipping export.")
        return
    export_settings.target_skeleton_or_skeletal_mesh = target_skeleton
    export_settings.enable_head_movement = True
    export_settings.export_range = unreal.PerformanceExportRange.PROCESSING_RANGE

    anim_sequence = unreal.MetaHumanPerformanceExportUtils.export_animation_sequence(
        performance_asset, export_settings
    )
    if anim_sequence is None:
        log("[person2meta] WARNING: Failed to export animation sequence.")
        return
    log(f"[person2meta] Audio-driven animation complete -- {anim_sequence.get_path_name()} "
        f"(apply it to the MetaHuman's Face skeletal mesh component to play it).")

    export_synced_level_sequence(
        metahuman_subsystem, character, anim_sequence, sound_wave, output_package_path, head_name
    )


def main():
    # -ExecutePythonScript auto-quits the editor one tick after this script
    # returns (EditorPythonExecuter.cpp: FExecuterTickable::Tick checks
    # UEditorPythonScriptingLibrary::GetKeepPythonScriptAlive() and calls
    # QUIT_EDITOR if it's false, which is the default) -- fine for a batch
    # run, but not when launched from the Blender "Export & Build MetaHuman"
    # button, where the whole point is to leave the result open to inspect.
    # Python-exposed name is EditorPythonScripting, NOT
    # EditorPythonScriptingLibrary -- the C++ class has
    # UCLASS(meta=(ScriptName="EditorPythonScripting")) overriding it.
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)

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

    curve_tracking = camera_location = camera_rotation = fov_deg = image_size = None
    if USE_CAMERA_LANDMARK_TERM:
        log("[person2meta] Capturing synthetic portrait and running face landmark detection...")
        curve_tracking, camera_location, camera_rotation, fov_deg, image_size = (
            capture_synthetic_portrait_and_track_landmarks(metahuman_subsystem, target_mesh, offset)
        )
        log(f"[person2meta] Detected {len(curve_tracking)} contour curves on synthetic render.")
    else:
        log("[person2meta] USE_CAMERA_LANDMARK_TERM=False -- skipping camera/landmark term, pure ICP fit.")

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

        if USE_CAMERA_LANDMARK_TERM:
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

        log(f"[person2meta] Running conform "
            f"({len(curve_tracking) if curve_tracking else 0} face curves)...")
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

    # Texture override: our own custom re-projected texture (T_MHC..., from
    # export_conformed_geometry_fbx + run_blender_texture_bake +
    # apply_baked_face_texture) was dropped after comparison -- MetaHuman's
    # own built-in AI texture synthesis (auto-generated the moment
    # commit_skin_settings/conform first runs on a brand-new character,
    # landing in the content folder named "<head_name>_texture") looked
    # properly fitted on its own. Rather than leave that as an implicit
    # default, explicitly wire it in as the face Basecolor override so it's
    # a visible, editable override going forward instead of a hidden
    # auto-generated default.
    if conform_succeeded:
        synthesized_texture_path = f"{import_destination_path}/{config['head_name']}_texture"
        apply_synthesized_texture_override(
            metahuman_subsystem, character, asset_path, synthesized_texture_path
        )

        log("[person2meta] Requesting auto-rig (joints + blendshapes for animation)...")
        if not metahuman_subsystem.try_add_object_to_edit(character):
            raise RuntimeError("Unable to edit asset for auto-rigging, is it already open for edit?")
        try:
            auto_rigging_request = unreal.MetaHumanCharacterAutoRiggingRequestParams()
            auto_rigging_request.blocking = True  # required to run unattended, per example_auto_rig.py
            auto_rigging_request.report_progress = False
            # JOINTS_AND_BLENDSHAPES (not JOINTS_ONLY) so the face gets the
            # blendshape controls actually needed to drive facial animation,
            # not just a skeleton for body/head movement.
            auto_rigging_request.rig_type = unreal.MetaHumanRigType.JOINTS_AND_BLEND_SHAPES
            metahuman_subsystem.request_auto_rigging(character, auto_rigging_request)
            unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=True)
            log(f"[person2meta] Auto-rig complete -- saved {asset_path}")
        finally:
            if metahuman_subsystem.is_object_added_for_editing(character):
                metahuman_subsystem.remove_object_to_edit(character)

        audio_path = config.get("audio_path")
        if audio_path:
            run_audio_driven_animation(
                metahuman_subsystem, character, audio_path,
                import_destination_path, output_package_path, config["head_name"]
            )


if __name__ == "__main__":
    main()
