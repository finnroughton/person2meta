"""
person2meta - request MetaHuman-generated textures
Run this INSIDE Unreal Engine 5.8's editor, via the Python tab, AFTER
conform_to_metahuman.py has successfully produced a working
MetaHumanCharacter asset.

Adapted from Epic's own example_download_textures.py.

IMPORTANT: this call requires being signed into an Epic account with
MetaHuman Cloud access. If you're not already signed in, the editor should
prompt a sign-in flow the first time this runs. This is the login step
we've discussed throughout the project -- it can't be automated away.

Reads the character asset path from person2meta_config.json, so no editing
needed between different heads -- just re-run for whichever head you last
processed.
"""

import json
import os
import unreal

# ---- EDIT ONCE: where run_pipeline.py wrote the config ----
CONFIG_PATH = r"C:\Users\BrianBurritt\Downloads\person2meta_config.json"
# --------------------------------------------------------------


def main():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"Config not found at {CONFIG_PATH}. Run run_pipeline.py first, "
            f"or check CONFIG_PATH points at the right place."
        )
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    asset_path = f"{config['output_package_path']}/{config['output_asset_name']}"

    print(f"[person2meta] Loading character asset: {asset_path}")
    character = unreal.load_asset(asset_path)
    if character is None:
        raise RuntimeError(
            f"No asset found at {asset_path}. Has conform_to_metahuman.py "
            f"been run successfully for '{config['head_name']}' yet?"
        )

    metahuman_subsystem = unreal.get_editor_subsystem(
        unreal.MetaHumanCharacterEditorSubsystem
    )

    if not metahuman_subsystem.try_add_object_to_edit(character):
        raise RuntimeError("Unable to edit asset, is it already open for edit?")

    try:
        print("[person2meta] Requesting texture sources (this needs Epic "
              "Cloud login -- watch for a sign-in prompt if this is your "
              "first time)...")
        texture_request = unreal.MetaHumanCharacterTextureRequestParams()
        texture_request.blocking = True  # waits for completion instead of running async
        texture_request.report_progress = False
        metahuman_subsystem.request_texture_sources(character, texture_request)
        print(f"[person2meta] Texture sources requested for {asset_path}.")
        print("[person2meta] Check the asset's Head & Body panel in the "
              "MetaHuman Character editor -- 'Texture Sources' status should "
              "no longer say 'Needs download'.")
    finally:
        if metahuman_subsystem.is_object_added_for_editing(character):
            metahuman_subsystem.remove_object_to_edit(character)


if __name__ == "__main__":
    main()
