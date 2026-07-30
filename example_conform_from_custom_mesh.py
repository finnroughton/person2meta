"""End-to-end conform of a MetaHumanCharacter to a custom static or skeletal
mesh, using face landmark tracking on a pre-rendered portrait to drive the
2D landmark term of the solve.

Pipeline:
    1. Load the portrait image into a TArray<FColor>.
    2. Run TrackFaceLandmarksFromImage to get 2D facial contour curves.
    3. Load the target mesh and extract topology (verts + tri indices) via
       GetMeshDataForConforming.
    4. Create a fresh MetaHumanCharacter asset.
    5. Build FConformTargetParams: body mesh data + curve tracking + camera info.
    6. Run ConformToTargetMeshes synchronously to fit body + face state.
    7. Export the conformed posed DNA (combined head+body) for downstream tools.
    8. Commit the posed state as the A pose so the saved asset is canonical.
    9. Save the character asset.

The portrait must be pre-rendered (this example deliberately does NOT cover
dynamic scene capture — that's a separate concern). The camera intrinsics
below MUST match the camera that produced the image, since the 2D landmarks
get back-projected into 3D during the solve.
"""
import unreal

# Inputs ----------------------------------------------------------------------

# A pre-rendered front-facing portrait of TARGET_MESH on disk. PNG/JPEG accepted.
PORTRAIT_IMAGE_PATH = r"D:\Path\To\Your\FacePortrait.png"

# The custom static or skeletal mesh to conform to.
TARGET_MESH_PATH = "/Game/MyContent/MyTargetMesh"

# The character asset that will receive the conform.
OUTPUT_PACKAGE_PATH = "/Game/MyContent"
OUTPUT_ASSET_NAME = "MHC_FromPortrait"

# Camera intrinsics + extrinsics that produced PORTRAIT_IMAGE_PATH.
# Edit these to match how your portrait was rendered.
CAMERA_LOCATION = unreal.Vector(0.0, 100.0, 165.0)
CAMERA_ROTATION = unreal.Rotator(pitch=0.0, yaw=-90.0, roll=0.0)
CAMERA_FOV_DEG = 40.0


# Pipeline --------------------------------------------------------------------
metahuman_subsystem = unreal.get_editor_subsystem(
    unreal.MetaHumanCharacterEditorSubsystem
)

# 1. Load the portrait into BGRA8 pixels.
image_size, pixels = unreal.PromotedFrameUtils.get_promoted_frame_as_pixel_array_from_disk(
    PORTRAIT_IMAGE_PATH
)
if image_size.x <= 0 or image_size.y <= 0:
    raise RuntimeError(f"Failed to load portrait image at {PORTRAIT_IMAGE_PATH}")

# 2. Run face landmark detection. Returns TMap<FString, FTrackingPoints>.
# UE Python wraps bool-return + out-param as either a 1-tuple or the out-param
# directly — handle both shapes.
result = metahuman_subsystem.track_face_landmarks_from_image(
    pixels, image_size.x, image_size.y
)
if isinstance(result, tuple) and len(result) == 1:
    result = result[0]
curve_tracking = result if (result and hasattr(result, "items")) else None
if not curve_tracking:
    raise RuntimeError(
        "No face landmarks detected. The tracker needs a clear, well-lit, "
        "front-facing face image with eyes open."
    )
unreal.log(f"Detected {len(curve_tracking)} contour curves on portrait.")

# 3. Load target mesh and extract topology.
target_mesh = unreal.load_asset(TARGET_MESH_PATH)
if target_mesh is None:
    raise RuntimeError(f"Failed to load target mesh: {TARGET_MESH_PATH}")
# UE Python wraps `bool Func(In, &OutA, &OutB)` so the bool success is consumed
# by the binding and the call returns (verts, indices). Trailing *_ is defensive
# in case a future binding shape ever surfaces additional values.
body_vertices, body_indices, *_ = metahuman_subsystem.get_mesh_data_for_conforming(target_mesh)
unreal.log(f"Target mesh: {len(body_vertices)} verts, {len(body_indices) // 3} tris")

# 4. Create the character.
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
asset_path = f"{OUTPUT_PACKAGE_PATH}/{OUTPUT_ASSET_NAME}"
character = asset_tools.create_asset(
    asset_name=OUTPUT_ASSET_NAME,
    package_path=OUTPUT_PACKAGE_PATH,
    asset_class=unreal.MetaHumanCharacter,
    factory=unreal.new_object(type=unreal.MetaHumanCharacterFactoryNew),
)
if character is None:
    raise RuntimeError(f"Failed to create character asset at {asset_path}")

if not metahuman_subsystem.try_add_object_to_edit(character):
    # Roll back the just-created asset so we don't leave an orphan in the
    # content browser if the editor lock is held elsewhere.
    if not unreal.EditorAssetLibrary.delete_asset(asset_path):
        unreal.log_warning(f"Failed to delete orphaned asset at {asset_path} — manual cleanup may be required")
    raise RuntimeError("Unable to edit asset, is it already open for edit?")

# Track whether the conform finished successfully so we can roll back the
# just-created asset if anything inside the `try` block raises or fails.
conform_succeeded = False
try:
    # 5. Assemble conform params.
    conform_params = unreal.ConformTargetParams()
    conform_params.conform_target_mesh.target_parts_type = unreal.TargetPartsType.COMBINED
    conform_params.conform_target_mesh.body_vertices = body_vertices
    conform_params.conform_target_mesh.body_vertex_indices = body_indices
    conform_params.estimate_body_joints_from_mesh = True
    conform_params.auto_solve = True
    conform_params.body_conform_solve_settings.pipeline_name = "combined"

    # 2D face tracking term — landmarks + the camera that produced them.
    view_info = unreal.MinimalViewInfo()
    view_info.location = CAMERA_LOCATION
    view_info.rotation = CAMERA_ROTATION
    view_info.fov = CAMERA_FOV_DEG
    view_info.aspect_ratio = float(image_size.x) / float(image_size.y)
    view_info.projection_mode = unreal.CameraProjectionMode.PERSPECTIVE
    conform_params.curve_tracking_points = curve_tracking
    conform_params.camera_view_info = view_info
    conform_params.image_size = image_size

    # Optional A — per-vertex keypoint pin constraints for the SOLVER.
    # FConformTargetParams.KeyPointTargets is the only place ConformToTargetMeshes
    # reads keypoints from. Map: MH vertex index → target 3D position. Uncomment:
    #
    # conform_params.key_point_targets = {
    #     100: unreal.Vector3f(0.0, 0.0, 100.0),  # MH vertex 100 → target position
    #     200: unreal.Vector3f(50.0, 0.0, 100.0),
    # }

    # Optional B — persist the same keypoints onto the character ASSET so they
    # show up in the editor's keypoint mechanic when you re-open the import tool.
    # This is purely editor-UI state; it is NOT read by the solver. The solver
    # only sees Optional A (KeyPointTargets) above. Use this in addition to A
    # if you want the keypoints visible/editable in the UI later.
    #
    # Gotchas worth knowing:
    #   * The TargetMeshKey slot you use (combined_mesh / body_mesh / head_mesh)
    #     must match the "Single Mesh" vs "Mesh Parts" mode the import tool will
    #     be in when you re-open the asset. For target_parts_type=COMBINED, use
    #     combined_mesh — the import tool's "Single Mesh" mode keys lookups
    #     under CombinedMesh.
    #   * The import tool restores its mesh selection from
    #     UMetaHumanCharacter::LastTargetMeshKey, which is currently
    #     UPROPERTY(VisibleAnywhere) and not Python-settable. After running
    #     this script you'll need to manually re-select the same mesh in the
    #     import tool's mesh slot for the committed keypoints to appear.
    #   * Only EKeyPointType::Custom vertex indexes are persisted — PresetMH
    #     entries are filtered out (they're regenerated via GetPresetBodyKeyPoints).
    #
    # target_mesh_key = unreal.MetaHumanCharacterTargetMeshKey()
    # target_mesh_key.combined_mesh = target_mesh  # match conform_target_mesh.target_parts_type
    #
    # asset_keypoints = unreal.MetaHumanCharacterTargetKeyPoints()
    # kp_types = {}
    # for name, mh_vertex_index, target_pos in (
    #     ("KP_LeftWrist",  100, unreal.Vector3f(0.0, 0.0, 100.0)),
    #     ("KP_RightWrist", 200, unreal.Vector3f(50.0, 0.0, 100.0)),
    # ):
    #     asset_keypoints.character_body_vertex_indexes[name] = mh_vertex_index
    #     asset_keypoints.target_body_positions[name] = target_pos
    #     kp_types[name] = unreal.KeyPointType.CUSTOM
    #
    # metahuman_subsystem.commit_target_mesh_keypoints(
    #     character, target_mesh_key, asset_keypoints, kp_types,
    # )

    # 6. Run the synchronous combined body+face conform. The TargetMeshKey
    # identifies which mesh slot this conform applies to; per-mesh state
    # (target-pose data, tracking results) is stored under this key on the
    # character, so different conforms against different target meshes don't
    # overwrite each other's state.
    target_mesh_key = unreal.MetaHumanCharacterTargetMeshKey()
    target_mesh_key.combined_mesh = target_mesh  # match conform_target_mesh.target_parts_type=COMBINED
    unreal.log(f"Running conform ({len(curve_tracking)} face curves)...")
    if not metahuman_subsystem.conform_to_target_meshes(character, target_mesh_key, conform_params):
        raise RuntimeError("conform_to_target_meshes failed")

    # Export the posed DNA BEFORE flipping the live state back to A pose — the
    # export reads the conformed posed state stored under target_mesh_key and
    # bakes it (combined head + body) into a single DNA asset. Downstream tools
    # (material baking, etc.) consume this DNA.
    posed_dna_export = unreal.MetaHumanPosedDNAExportParams()
    posed_dna_export.target_mesh_key = target_mesh_key
    # ProjectPath / ExternalPath are FOLDERS. AssetName is the asset/file name
    # stem (without extension); falls back to "<CharacterName>_Posed" when empty.
    # Set ExternalPath to also write a .dna file alongside the project asset.
    posed_dna_export.project_path = OUTPUT_PACKAGE_PATH
    # posed_dna_export.external_path = r"D:\Path\To\OutputFolder"
    # posed_dna_export.asset_name = "MyCustomPosedDNA"
    posed_dna_export.overwrite_existing_assets = True
    unreal.MetaHumanCharacterExportBlueprintLibrary.export_posed_dna(character, posed_dna_export)

    # Re-evaluate the body in MetaHuman A pose and propagate to the face so the saved
    # asset is in the canonical A pose for downstream consumers. The interactive Mesh
    # Import tool does this on shutdown.
    metahuman_subsystem.commit_posed_state_as_a_pose(character, target_mesh_key)

    unreal.EditorAssetLibrary.save_asset(asset_path, only_if_is_dirty=True)
    unreal.log(f"Conform complete — saved {asset_path}")
    conform_succeeded = True

finally:
    if metahuman_subsystem.is_object_added_for_editing(character):
        metahuman_subsystem.remove_object_to_edit(character)
    if not conform_succeeded:
        # Roll back the just-created asset so we don't leave an orphan or a
        # partially-conformed character in the content browser on failure.
        if not unreal.EditorAssetLibrary.delete_asset(asset_path):
            unreal.log_warning(f"Failed to delete orphaned asset at {asset_path} — manual cleanup may be required")
