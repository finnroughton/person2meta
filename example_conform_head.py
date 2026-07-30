import unreal
from pathlib import Path

# Load the asset
character = unreal.load_asset("/Game/Characters/MetaHumans/Jeff.Jeff")

# Get the MetaHuman Subsystem
metahuman_subsystem = unreal.get_editor_subsystem(
    unreal.MetaHumanCharacterEditorSubsystem
)

# Try to edit the character
if not metahuman_subsystem.try_add_object_to_edit(character):
    raise RuntimeError("Unable to edit asset, is it already open for edit?")
try:
    # Conform from DNA
    dna_file_path = Path("Path/To/Your/DNA.dna")
    import_params = unreal.ImportFromDNAParams()
    import_params.import_whole_rig = False
    import_params.alignment_options = unreal.MetaHumanAlignmentOptions.SCALING_ROTATION_TRANSLATION

    metahuman_subsystem.import_from_face_dna(
        character=character,
        dna_file_path=dna_file_path.as_posix(),
        import_params=import_params
    )

    # Conform from template
    skeletal_mesh = unreal.load_asset(
        "/Game/Characters/MetaHumans/SKM_Jeff_Face.SKM_Jeff_Face"
    )
    import_params = unreal.ImportFromTemplateParams()
    import_params.use_eye_meshes = True
    import_params.use_teeth_mesh = True
    import_params.alignment_options = unreal.MetaHumanAlignmentOptions.SCALING_ROTATION_TRANSLATION
                
    result = metahuman_subsystem.import_from_template(
        character,
        skeletal_mesh,
        None, 
        None, 
        None, 
        import_params
    )

    if result != unreal.ImportErrorCode.SUCCESS:
        unreal.log_error("Failed to import MetaHuman Character asset")
        raise RuntimeError

    # Fit from vertices
    fit_options = unreal.FitToTargetOptions()
    fit_options.alignment_options = unreal.MetaHumanAlignmentOptions.SCALING_ROTATION_TRANSLATION
    fit_options.adapt_neck = True
    fit_options.disable_high_frequency_delta = True

    fit_params = unreal.MetaHumanCharacterFitToVerticesParams()
    fit_params.options = fit_options

    # Fill in the vertices for head, teeth and eyes. 
    # Expects an array of unreal.Vector for each

    # fit_params.head_vertices = ...
    # fit_params.teeth_vertices = ...
    # fit_params.left_eye_vertices = ...
    # fit_params.right_eye_vertices = ...
                
    metahuman_subsystem.fit_state_to_target_vertices(
        character=character,
        params=fit_params
    )
    metahuman_subsystem.commit_face_state(character)

finally:
    # Finish Editing
    if metahuman_subsystem.is_object_added_for_editing(character):
        metahuman_subsystem.remove_object_to_edit(character)