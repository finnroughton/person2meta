"""
person2meta - Blender startup script
Runs INSIDE Blender (launched via `blender --python blender_startup.py`,
GUI mode -- NOT --background, since a human needs to see and adjust the
result afterward).

Reads the image paths passed from launch_picker.py, removes the default
scene cube, creates a new FaceBuilder head, loads the selected images into
it as views, selects/activates the head object, opens the N-panel on the
FaceBuilder tab, and clicks the first loaded view to enter pin mode on it.
Leaves Blender open with everything ready to tweak immediately.

FIXED (previously broken): image loading was calling
`keentools_fb.open_multiple_filebrowser_exec`, which is only a thin
wrapper that pops the interactive file-browser UI (`INVOKE_DEFAULT`) --
it doesn't accept `directory`/`files` at all, so passing them raised a
TypeError. The actual file-loading logic lives on
`keentools_fb.open_multiple_filebrowser` itself (a bpy_extras.ImportHelper
operator); calling THAT operator directly with `directory`/`files` set
(not through `INVOKE_DEFAULT`) runs its `execute()` immediately and skips
the file browser entirely -- the same trick used to script any Blender
"File > Import" operator headlessly. Verified end-to-end in
--background mode: creates real FaceBuilder cameras with real images
attached, no dialog ever opens.
"""

import bpy
import os
import time
from collections import defaultdict

LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "blender_startup_log.txt")


def log(message: str) -> None:
    """Prints AND appends to a file next to this script -- blender-launcher.exe
    opens its own detached console, so print() alone isn't visible to
    whatever launched this (e.g. a script driving Blender non-interactively)."""
    line = f"{time.strftime('%H:%M:%S')} {message}"
    print(line)
    try:
        with open(LOG_FILE_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def get_image_paths() -> list[str]:
    raw = os.environ.get("P2M_IMAGE_PATHS", "")
    if not raw:
        log("[person2meta] No P2M_IMAGE_PATHS env var found — nothing to load.")
        return []
    return raw.split("||")


def ensure_facebuilder_enabled() -> None:
    if hasattr(bpy.ops, "keentools_fb"):
        return
    log("[person2meta] keentools_fb not registered yet — trying to enable the addon...")
    for module_name in ("bl_ext.user_default.keentools", "keentools"):
        try:
            bpy.ops.preferences.addon_enable(module=module_name)
            if hasattr(bpy.ops, "keentools_fb"):
                log(f"[person2meta] Enabled KeenTools addon ({module_name})")
                return
        except Exception:
            pass
    raise RuntimeError(
        "KeenTools FaceBuilder addon isn't enabled and couldn't be enabled "
        "automatically. Check Edit > Preferences > Add-ons in Blender."
    )


def delete_default_cube() -> None:
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        return
    bpy.data.objects.remove(cube, do_unlink=True)
    log("[person2meta] Removed default 'Cube' object.")


def select_and_activate(obj) -> None:
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    log(f"[person2meta] Selected and activated '{obj.name}' — ready to tweak.")


def _try_focus_facebuilder_tab_and_select_first_view(headnum: int) -> bool:
    """One attempt at: opening the N-panel, switching it to the FaceBuilder
    tab, and clicking the first loaded view (camera 0) to enter pin mode --
    mirrors a human pressing N, clicking the FaceBuilder tab, then clicking
    the first image thumbnail. Returns True once it actually stuck, False if
    it should be retried.

    Blender's own startup splash screen is a modal popup that holds the
    window's focus for as long as it stays open (i.e. until the user clicks
    it away) -- while it's up, setting active_panel_category gets silently
    discarded instead of erroring, so a single attempt right after startup
    isn't reliable. This is meant to be called repeatedly via a timer until
    it returns True."""
    if bpy.app.background:
        return True  # nothing to do, and nothing to retry

    try:
        wm = bpy.context.window_manager
        if not wm.windows:
            log("[person2meta] [tab-diag] no windows yet")
            return False

        window = wm.windows[0]
        log(f"[person2meta] [tab-diag] window.screen={window.screen.name!r} "
            f"areas={[a.type for a in window.screen.areas]}")
        view3d_area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
        if view3d_area is None:
            log("[person2meta] [tab-diag] no VIEW_3D area on this screen")
            return False

        view3d_area.spaces.active.show_region_ui = True  # same as pressing N

        ui_region = next((r for r in view3d_area.regions if r.type == 'UI'), None)
        if ui_region is None:
            log("[person2meta] [tab-diag] VIEW_3D area has no UI region")
            return False

        before = ui_region.active_panel_category
        ui_region.active_panel_category = 'FaceBuilder'
        after = ui_region.active_panel_category
        log(f"[person2meta] [tab-diag] active_panel_category before={before!r} "
            f"after-set={after!r}")
        if after != 'FaceBuilder':
            return False  # discarded (splash screen likely still has modal focus) -- retry later

        window_region = next((r for r in view3d_area.regions if r.type == 'WINDOW'), None)
        with bpy.context.temp_override(window=window, area=view3d_area, region=window_region):
            result = bpy.ops.keentools_fb.select_camera(headnum=headnum, camnum=0)
        log(f"[person2meta] FaceBuilder tab active, clicked first view "
            f"-> select_camera(headnum={headnum}, camnum=0) = {result}")
        return True
    except Exception as e:
        log(f"[person2meta] [tab-diag] EXCEPTION: {type(e).__name__}: {e}")
        return False


def schedule_facebuilder_tab_and_pin_click(headnum: int, interval: float = 0.5,
                                           max_attempts: int = 20) -> None:
    attempts_left = [max_attempts]

    def _attempt():
        if _try_focus_facebuilder_tab_and_select_first_view(headnum):
            return None  # success, stop retrying
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            log("[person2meta] Gave up switching to the FaceBuilder tab "
                  "automatically (splash screen may still be open) — click "
                  "the FaceBuilder tab in the N-panel manually.")
            return None
        return interval

    bpy.app.timers.register(_attempt, first_interval=interval)


def create_facebuilder_head_and_load_images(image_paths: list[str]) -> int:
    ensure_facebuilder_enabled()
    delete_default_cube()

    add_result = bpy.ops.keentools_fb.add_head()
    if add_result != {'FINISHED'}:
        raise RuntimeError(f"keentools_fb.add_head() did not finish: {add_result}")
    log("[person2meta] Created new FaceBuilder head.")

    # Try to read the real head index/object back from FaceBuilder's own
    # settings instead of assuming 0, in case a head already existed in
    # this session.
    headnum = 0
    settings = None
    try:
        try:
            from bl_ext.user_default.keentools.addon_config import fb_settings
        except ImportError:
            from keentools.addon_config import fb_settings
        settings = fb_settings()
        headnum = settings.current_headnum
    except Exception as e:
        log(f"[person2meta] Could not read current_headnum ({e}), assuming 0.")

    # open_multiple_filebrowser only takes ONE shared `directory` string per
    # call, so group images by folder in case the picker returned paths from
    # more than one location.
    by_directory: dict[str, list[str]] = defaultdict(list)
    for path in image_paths:
        by_directory[os.path.dirname(path) + os.sep].append(os.path.basename(path))

    total_before = 0
    for directory, filenames in by_directory.items():
        files = [{"name": name} for name in filenames]
        log(f"[person2meta] Loading {len(files)} image(s) from {directory}")
        # Calling this directly (no 'INVOKE_DEFAULT') runs execute() straight
        # away with the directory/files we pass in, instead of popping the
        # interactive file browser.
        result = bpy.ops.keentools_fb.open_multiple_filebrowser(
            headnum=headnum, directory=directory, files=files
        )
        if result != {'FINISHED'}:
            raise RuntimeError(
                f"keentools_fb.open_multiple_filebrowser() did not finish "
                f"for {directory}: {result}"
            )
        total_before += len(files)

    log(f"[person2meta] Loaded {total_before} image(s) into head {headnum}. "
          f"Open the FaceBuilder tab (press N in the 3D viewport) to review/adjust.")

    if settings is not None:
        head = settings.get_head(headnum)
        if head is not None and head.headobj is not None:
            select_and_activate(head.headobj)

    return headnum


def main():
    image_paths = get_image_paths()
    if not image_paths:
        return
    headnum = create_facebuilder_head_and_load_images(image_paths)
    schedule_facebuilder_tab_and_pin_click(headnum)


if __name__ == "__main__":
    main()
