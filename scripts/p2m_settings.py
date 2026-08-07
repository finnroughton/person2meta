"""
person2meta - shared machine-specific settings.

Loaded by launch_picker.py, blender_startup.py, conform_to_metahuman.py,
and bake_texture.py alike. Previously each of those scripts hardcoded its
own copy of the same paths (Blender install, Unreal project/engine, work
directory), kept in sync by hand via "EDIT ONCE: must match ..." comments
scattered across files -- exactly the kind of thing that quietly drifts
(this project hit a couple of bugs already that traced back to that). This
is the one place to edit them now.

Edit person2meta_settings.json directly (it's created next to this file,
with the defaults below, the first time any script runs after this
existed). Delete it to regenerate the defaults.
"""

import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "person2meta_settings.json")

DEFAULTS = {
    # Blender has two relevant entry points: blender-launcher.exe (normal
    # GUI launch, used by launch_picker.py) and blender.exe (used directly
    # for --background runs, e.g. conform_to_metahuman.py's texture bake
    # subprocess -- the launcher wrapper isn't needed/wanted there).
    "blender_launcher_exe": r"C:\Program Files\Blender Foundation\Blender 5.2\blender-launcher.exe",
    "blender_exe": r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    "unreal_project_path": r"C:\Users\BrianBurritt\Documents\Unreal Projects\MyProject2\MyProject2.uproject",
    "unreal_editor_exe": r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe",
    # Where per-head FBX/texture/config/log files get written and read from.
    "work_dir": r"C:\Users\BrianBurritt\Downloads",
    # bake_texture.py needs scipy, which lives in the SYSTEM Python's user
    # site-packages -- Blender's bundled interpreter doesn't add that to
    # sys.path on its own.
    "python_user_site_packages": r"C:\Users\BrianBurritt\AppData\Roaming\Python\Python313\site-packages",
}


def load_settings() -> dict:
    """Returns the settings dict, creating person2meta_settings.json with
    the defaults above if it doesn't exist yet. Any key missing from an
    existing (e.g. older) settings file falls back to its default rather
    than raising, so adding a new setting here never breaks a file that
    predates it."""
    settings = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                on_disk = json.load(f)
            settings.update({k: v for k, v in on_disk.items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
    else:
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(settings, f, indent=2)
        except OSError:
            pass
    return settings
