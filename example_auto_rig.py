import unreal

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
    auto_rigging_request = unreal.MetaHumanCharacterAutoRiggingRequestParams()
    # Required for running in batch
    auto_rigging_request.blocking = True
    auto_rigging_request.report_progress = False
    # Rig Types are JOINTS_ONLY or JOINTS_AND_BLENDSHAPES
    auto_rigging_request.rig_type = unreal.MetaHumanRigType.JOINTS_ONLY
    metahuman_subsystem.request_auto_rigging(character, auto_rigging_request)

finally:
    # Finish Editing
    if metahuman_subsystem.is_object_added_for_editing(character):
        metahuman_subsystem.remove_object_to_edit(character)