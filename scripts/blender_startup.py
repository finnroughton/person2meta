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
import json
import subprocess
import sys
import time
from collections import defaultdict

# Blender's -ExecutePythonScript-style launch doesn't reliably add this
# script's own directory to sys.path -- add it explicitly so `import
# p2m_settings` (a plain sibling module, not a Blender addon) works.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p2m_settings

LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "blender_startup_log.txt")
# Polled by launch_picker.py's loading overlay (show_loading_overlay) --
# separate from LOG_FILE_PATH since this one gets parsed as JSON by another
# process while Blender is still running, not just appended to for humans.
STATUS_FILE_PATH = os.path.join(os.path.dirname(__file__), "blender_startup_status.json")

# All machine-specific paths now live in one place -- see p2m_settings.py
# (and person2meta_settings.json, created next to it) to edit them.
_settings = p2m_settings.load_settings()
# Only used here to derive work_dir (where per-head config/log/FBX files get
# written -- see _try_export_and_build); the actual config file each Unreal
# launch reads is per-head, passed via the P2M_CONFIG_PATH env var, not this
# exact filename.
CONFIG_PATH = os.path.join(_settings["work_dir"], "person2meta_config.json")
UNREAL_PROJECT_PATH = _settings["unreal_project_path"]
UNREAL_EDITOR_EXE = _settings["unreal_editor_exe"]
CONFORM_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "conform_to_metahuman.py")


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


def notify_completion(message: str) -> None:
    """Best-effort desktop notification (sound + a small always-on-top
    message box) for when a long, often-unattended Unreal build -- or a
    whole Export All Heads batch -- finishes. These can run for minutes
    with nobody watching, especially auto-rig's Epic sign-in step.

    Uses winsound + a PowerShell MessageBox rather than a real Windows
    toast: Blender's own bundled Python doesn't ship tkinter (see
    audio_video_prompt.py's docstring for why THAT runs as a separate
    system-Python process instead), and a proper toast needs either a
    registered AppUserModelID or the BurntToast module, neither available
    here. Non-fatal on failure either way -- this is pure convenience."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception as e:
        log(f"[person2meta] Could not play completion sound: {e}")

    try:
        safe_message = message.replace("'", "''")  # PowerShell single-quote escaping
        subprocess.Popen([
            "powershell", "-NoProfile", "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.MessageBox]::Show('{safe_message}', 'person2meta', "
            "'OK', 'Information') | Out-Null",
        ])
    except Exception as e:
        log(f"[person2meta] Could not show completion notification: {e}")


def write_status(message: str, done: bool = False) -> None:
    """Reports setup progress for launch_picker.py's loading overlay, which
    covers Blender's window until this says done=True -- the rapid
    auto-align pin/camera changes during setup were visible through
    Blender's own window and read as glitching rather than progress."""
    try:
        with open(STATUS_FILE_PATH, "w") as f:
            json.dump({"message": message, "done": done}, f)
    except OSError:
        pass


def get_image_paths() -> list[str]:
    raw = os.environ.get("P2M_IMAGE_PATHS", "")
    if not raw:
        log("[person2meta] No P2M_IMAGE_PATHS env var found — nothing to load.")
        return []
    return raw.split("||")


def get_multi_head_definitions() -> list:
    """Reads P2M_MULTI_HEADS (set by launch_picker.py's "Multiple Heads"
    flow), a JSON list of {"name": str, "images": [str, ...]} entries --
    one per person. Empty list if unset/unparseable (single-head mode)."""
    raw = os.environ.get("P2M_MULTI_HEADS", "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log(f"[person2meta] Could not parse P2M_MULTI_HEADS ({e}).")
        return []


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


def get_fb_settings():
    try:
        from bl_ext.user_default.keentools.addon_config import fb_settings
    except ImportError:
        from keentools.addon_config import fb_settings
    return fb_settings()


def resolve_active_headnum(context, settings) -> int:
    """Resolves which FaceBuilder head to act on from the currently
    active/selected Blender object, NOT settings.current_headnum --
    that only updates when a head is CREATED (head.py) or when the user
    explicitly enters FaceBuilder's own pin mode for it (pinmode.py), not
    on ordinary Blender object selection. In multi-head sessions this left
    current_headnum stuck on whichever head was created/pin-moded LAST, so
    selecting an earlier head and clicking Export still suggested/exported
    the WRONG one. settings.find_head_index(obj) (the same lookup the
    addon itself uses to map a clicked object back to its head) fixes
    that; falls back to current_headnum if the active object isn't a
    FaceBuilder head at all."""
    active_obj = context.view_layer.objects.active
    if active_obj is not None:
        idx = settings.find_head_index(active_obj)
        if idx >= 0:
            return idx
    return settings.current_headnum


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
            # Every camera already got auto-detected/pinned by
            # auto_detect_and_pin_all_cameras() right after loading, so this
            # just opens pin mode on camera 0 for visual review -- no need
            # for detect_face here too.
            result = bpy.ops.keentools_fb.select_camera(headnum=headnum, camnum=0)
        log(f"[person2meta] FaceBuilder tab active, clicked first view "
            f"-> select_camera(headnum={headnum}, camnum=0) = {result}")
        return True
    except Exception as e:
        log(f"[person2meta] [tab-diag] EXCEPTION: {type(e).__name__}: {e}")
        return False


def schedule_facebuilder_tab_and_pin_click(headnum: int, interval: float = 0.5,
                                           max_attempts: int = 20, on_finished=None) -> None:
    """on_finished, if given, is called once this finishes (success or
    give-up either way) -- create_multiple_heads uses this to hold off
    starting auto-detect until AFTER select_camera has actually run at
    least once. That call is what populates KeenTools' own cached
    "work area" reference (Viewport._work_area in utils/viewport.py --
    get_work_area() just returns whatever was last set, nothing
    re-discovers it on its own); without it, EVERY pickmode_starter call
    fails with a None-area AttributeError, not just early ones -- this
    isn't a "not ready yet" timing race like the retries elsewhere in this
    file, it's a real prerequisite that has to run once, first."""
    attempts_left = [max_attempts]

    def _attempt():
        if _try_focus_facebuilder_tab_and_select_first_view(headnum):
            if on_finished is not None:
                on_finished()
            return None  # success, stop retrying
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            log("[person2meta] Gave up switching to the FaceBuilder tab "
                  "automatically (splash screen may still be open) — click "
                  "the FaceBuilder tab in the N-panel manually.")
            if on_finished is not None:
                on_finished()
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
        settings = get_fb_settings()
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


def _get_detected_faces_rectangles():
    """The rectangles FaceBuilder's own detector found on whichever camera
    it last ran on (module-global state inside the addon, populated by
    keentools_fb.pickmode_starter right before it decides what to do with
    them) -- used below to pick a face ourselves when there's more than
    one, since that's the one case pickmode_starter(auto_detect_single=True)
    can't resolve on its own (see schedule_auto_detect_all_cameras)."""
    try:
        from bl_ext.user_default.keentools.utils.detect_faces import get_detected_faces_rectangles
    except ImportError:
        from keentools.utils.detect_faces import get_detected_faces_rectangles
    return get_detected_faces_rectangles()


def schedule_auto_detect_all_cameras(headnum: int, interval: float = 0.2, on_finished=None,
                                     status_prefix: str = "") -> None:
    """Runs FaceBuilder's face-detection on every loaded photo and places
    pins automatically -- the same detect+pin+solve "Auto Align" performs
    when clicked by hand (confirmed by reading the addon's own source,
    keentools/facebuilder/pick_operator.py's _add_pins_to_face, which both
    the manual button and keentools_fb.pickmode_starter below end up
    calling) -- so the head shape actually fits all the photos (not just a
    generic template) and every camera has pins for texture baking later
    (bake_tex only pulls color from cameras with pins).

    Uses keentools_fb.pickmode_starter's execute() path directly (its own
    comment marks it "only for integration testing") instead of the normal
    interactive route (select_camera -> INVOKE_DEFAULT pin mode -> click
    "Pick Face"), which is modal and needs a live event loop ticking over
    real time to ever complete -- not something a script can drive
    reliably. execute() runs the same detection+pinning with no modal state
    involved and doesn't require pin mode to be active.

    auto_detect_single=True handles the common one-face-per-photo case
    exactly like the real button, but silently skips a photo instead of
    pinning anything when it finds MORE than one face -- the real button
    would instead pop an interactive "click the right face" picker, which
    there's no human here to click. Falling back to auto-picking the
    LARGEST detected rectangle (the most likely intended subject) and
    driving keentools_fb.pickmode's own execute() path with that index
    directly -- same trick, calling the operator that a human's click
    would otherwise trigger -- covers that gap instead of leaving the
    photo unpinned.

    Driven one camera per timer tick rather than a single blocking loop --
    a cold first-time ONNX model load for face detection can be slow enough
    to stall the whole startup script if run synchronously, which was
    observed to prevent the FaceBuilder-tab timer below from ever getting
    registered at all.

    The initial head lookup ALSO goes through a retry timer instead of a
    single synchronous check right here in main() -- confirmed via
    blender_startup_log.txt that this whole function was silently never
    running: no "Auto-detect face on camera" lines and, tellingly, not
    even the except-branch's own log call, across many real sessions. That
    points at settings.get_head(headnum) returning None on the very first
    call (immediately after the head was just created via scripted
    execute() calls, same class of "state isn't ready yet" timing issue
    schedule_facebuilder_tab_and_pin_click already retries around), with
    nothing left to retry it -- so it just returned and never logged
    again. head_wait_attempts gives it the same kind of runway.

    on_finished, if given, is called once (whether auto-detect actually
    completed or gave up) -- used by create_multiple_heads to chain heads
    one at a time instead of running several face-detection timer chains
    concurrently, which would fight over the same Blender UI context (the
    same class of race documented above)."""
    log(f"[person2meta] Scheduling auto-detect for headnum={headnum}...")
    head_wait_attempts = [20]

    def _wait_for_head():
        try:
            settings = get_fb_settings()
            head = settings.get_head(headnum)
        except Exception as e:
            head = None
            log(f"[person2meta] Could not reach head for auto-detect ({e}).")

        if head is not None:
            _start_auto_detect(head)
            return None

        head_wait_attempts[0] -= 1
        if head_wait_attempts[0] <= 0:
            log("[person2meta] Gave up waiting for FaceBuilder head -- auto-detect skipped.")
            if on_finished is not None:
                on_finished()
            return None
        return interval

    def _start_auto_detect(head):
        remaining = list(enumerate(head.cameras))
        total = len(remaining)
        log(f"[person2meta] Auto-detect starting on {total} camera(s).")
        write_status(f"{status_prefix}Auto-aligning photo 1/{total}...")
        retry_counts = {}  # camera index -> attempts so far, for the area-not-ready retry
        max_retries = 15  # ~3s of retrying at the default 0.2s interval

        def _attempt():
            if not remaining:
                log("[person2meta] Finished auto-detecting faces on all cameras.")
                if on_finished is not None:
                    on_finished()
                return None
            i, cam = remaining.pop(0)

            # bpy.app.timers swallows exceptions raised inside the callback --
            # no traceback reaches our own log file, it just silently stops
            # rescheduling (this is exactly what made the head-fetch bug above
            # invisible). Confirmed via a real traceback: pickmode_starter's
            # _add_pins_to_face -> update_fb_viewport_shaders needs Blender's
            # VIEW_3D area to be fully ready (area.regions[-1] on a None area)
            # -- which isn't guaranteed yet this early in startup, the same
            # race schedule_facebuilder_tab_and_pin_click already retries
            # around for the tab-focus issue. Retry the SAME camera (put it
            # back at the front) instead of abandoning it, up to max_retries.
            try:
                result = bpy.ops.keentools_fb.pickmode_starter(
                    headnum=headnum, camnum=i, auto_detect_single=True
                )
            except Exception as e:
                retries = retry_counts.get(i, 0)
                if retries < max_retries:
                    retry_counts[i] = retries + 1
                    remaining.insert(0, (i, cam))
                    log(f"[person2meta] Auto-detect on camera {i} not ready yet "
                        f"({type(e).__name__}), retrying ({retries + 1}/{max_retries})...")
                else:
                    log(f"[person2meta] Auto-detect on camera {i} raised "
                        f"{type(e).__name__}: {e} -- giving up on this camera.")
                # Always reschedule (never `... if remaining else None`) --
                # even with nothing left, one more tick is needed to hit the
                # `if not remaining:` check at the top of _attempt() and
                # actually fire the finish log + on_finished callback below.
                # Returning None here directly, right after emptying
                # `remaining`, would skip that -- confirmed via
                # blender_startup_log.txt: "Finished auto-detecting faces on
                # all cameras." had never once appeared in any real run.
                return interval

            log(f"[person2meta] Auto-detect face on camera {i}: {result}, "
                f"pins={cam.pins_count if cam.has_pins() else 0}")
            done_count = total - len(remaining)
            write_status(f"{status_prefix}Auto-aligning photo "
                        f"{min(done_count + 1, total)}/{total}...")

            if result == {'CANCELLED'}:
                try:
                    rects = _get_detected_faces_rectangles()
                    if len(rects) > 1:
                        # rects are (x1, y1, x2, y2, original_index) tuples --
                        # pick the largest by area as the most likely intended
                        # subject.
                        best = max(rects, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
                        best_index = best[4]
                        pick_result = bpy.ops.keentools_fb.pickmode(
                            headnum=headnum, camnum=i, selected=best_index
                        )
                        log(f"[person2meta] Camera {i} had {len(rects)} faces -- auto-picked "
                            f"the largest ({best_index}): {pick_result}, "
                            f"pins={cam.pins_count if cam.has_pins() else 0}")
                    elif len(rects) == 0:
                        log(f"[person2meta] No face detected on camera {i} -- leaving it unpinned.")
                except Exception as e:
                    log(f"[person2meta] Multi-face fallback for camera {i} raised "
                        f"{type(e).__name__}: {e}")

            # See the comment on the other `return interval` above -- always
            # reschedule so the final `if not remaining:` tick actually runs.
            return interval

        bpy.app.timers.register(_attempt, first_interval=interval)

    bpy.app.timers.register(_wait_for_head, first_interval=interval)


# FaceBuilder's default template mesh is modeled ~3m tall (confirmed via a
# fresh head: dimensions ~1.9 x 2.2 x 3.1) -- not calibrated to real-world
# scale at all, same class of bug already found and fixed for the old
# KeenTools-OBJ pipeline in convert_obj_to_fbx.py (TARGET_HEIGHT_METERS).
# Feeding a ~10x-oversized mesh into the Unreal conform's camera/landmark
# term (which assumes real head proportions) produces a disfigured result.
TARGET_HEIGHT_METERS = 0.4  # matches convert_obj_to_fbx.py's own convention


def bake_mh_face_texture(headnum: int, head, output_path: str):
    """Bakes FaceBuilder's own texture (blended from every pinned camera)
    and saves it to output_path as a PNG. Returns the baked bpy.types.Image
    on success, None (non-fatal) on any failure -- the exported head just
    goes out untextured if so, same as before this feature existed.

    Uses the 'mh' UV preset since it's a reasonable choice, but this is NOT
    used as a direct texture-to-mesh mapping in Unreal: MetaHuman's conform
    step builds an entirely separate MetaHuman-topology mesh, not a copy of
    our FaceBuilder mesh, so no FaceBuilder UV preset lines up with it
    directly (confirmed -- applying this image straight onto the conformed
    head as a UV-mapped texture produced a scrambled result). Instead this
    image gets assigned as a real material on the SOURCE mesh before export
    (see _export_head_fbx_normalized), so conform_to_metahuman.py's existing
    3D-position re-projection bake (bake_texture.py) has real photo colors
    to sample from -- that bake works via actual 3D correspondence between
    source and target meshes, so it doesn't care what UV layout the source
    used."""
    try:
        try:
            from bl_ext.user_default.keentools.facebuilder.fbloader import FBLoader
        except ImportError:
            from keentools.facebuilder.fbloader import FBLoader

        settings = get_fb_settings()
        FBLoader.load_model(headnum)  # ensure the builder is ready before touching tex_uv_shape
        uv_sets = list(FBLoader.get_builder().uv_sets_list())
        mh_index = next((i for i, name in enumerate(uv_sets) if name.lower() == "mh"), None)
        if mh_index is None:
            log(f"[person2meta] No 'mh' UV set found (got {uv_sets}) -- skipping FaceBuilder texture bake.")
            return None
        settings.tex_uv_shape = f"uv{mh_index}"

        # All three default to False -- without them, blending photos taken
        # under different lighting produces a hard warm/cool split down the
        # middle of the face (confirmed: this exact artifact was showing up
        # in every bake) and more/harsher black gaps than necessary.
        settings.tex_equalize_brightness = True
        settings.tex_equalize_colour = True
        settings.tex_fill_gaps = True

        pinned = sum(1 for cam in head.cameras if cam.has_pins())
        if pinned == 0:
            log("[person2meta] No pinned cameras -- skipping FaceBuilder texture bake.")
            return None

        # Use the SAME two operators as manually clicking "Create Texture"
        # then "Export" in the Texture panel, instead of calling the bare
        # bake_tex() utility function ourselves -- the real bake_tex
        # OPERATOR (keentools_fb.bake_tex) runs common_fb_checks(...,
        # reload_facebuilder=True, ...) first, which reloads FaceBuilder's
        # internal model state; calling the utility function directly
        # skipped that, and the UV shape change wasn't reliably picked up
        # as a result (confirmed by testing -- manual clicks worked,
        # scripted bake_tex() didn't, consistently).
        bake_result = bpy.ops.keentools_fb.bake_tex(headnum=headnum)
        if bake_result != {'FINISHED'}:
            log(f"[person2meta] keentools_fb.bake_tex did not finish: {bake_result}")
            return None

        # Calling this directly (no 'INVOKE_DEFAULT') runs execute() with
        # our filepath immediately instead of popping a save-file dialog --
        # the same trick used elsewhere in this file for other ExportHelper
        # operators.
        export_result = bpy.ops.keentools_fb.texture_file_export(
            headnum=headnum, filepath=output_path, file_format='PNG'
        )
        if export_result != {'FINISHED'} or not os.path.exists(output_path):
            log(f"[person2meta] keentools_fb.texture_file_export did not "
                f"produce {output_path}: {export_result}")
            return None

        img = bpy.data.images.load(output_path)
        log(f"[person2meta] Baked face texture ({pinned}/{len(head.cameras)} "
            f"pinned cameras) -> {output_path}")
        return img
    except Exception as e:
        log(f"[person2meta] FaceBuilder texture bake raised {type(e).__name__}: {e}")
        return None


def _export_head_fbx_normalized(head_obj, fbx_path: str, bake_image=None) -> None:
    """Exports head_obj to fbx_path rescaled to a realistic real-world
    height. Works on a temporary duplicate so the live FaceBuilder head's
    own transform (and therefore its pin/camera projection math) is never
    touched -- only the exported copy is rescaled/re-materialed.

    If bake_image is given, assigns it as a real Base Color material on the
    duplicate before export, so downstream tools that sample the FBX's own
    appearance (conform_to_metahuman.py's 3D re-projection bake) get real
    photo colors instead of FaceBuilder's default gray placeholder."""
    dup = head_obj.copy()
    dup.data = head_obj.data.copy()
    bpy.context.collection.objects.link(dup)
    try:
        bpy.ops.object.select_all(action='DESELECT')
        dup.select_set(True)
        bpy.context.view_layer.objects.active = dup

        height = dup.dimensions.z
        if height > 0:
            scale_factor = TARGET_HEIGHT_METERS / height
            bpy.ops.transform.resize(value=(scale_factor, scale_factor, scale_factor))
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            log(f"[person2meta] Normalized export height {height:.3f}m -> "
                f"{TARGET_HEIGHT_METERS}m (scale x{scale_factor:.4f})")

        if bake_image is not None:
            mat = bpy.data.materials.new(name="P2M_BakedFace")
            mat.use_nodes = True
            tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex_node.image = bake_image
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            dup.data.materials.clear()
            dup.data.materials.append(mat)
            log(f"[person2meta] Assigned baked texture as export material.")

        bpy.ops.export_scene.fbx(
            filepath=fbx_path,
            use_selection=True,
            bake_anim_use_all_actions=False,
            bake_anim_use_nla_strips=False,
            add_leaf_bones=False,
            mesh_smooth_type='FACE',
            axis_forward='-Z',
            axis_up='Y',
            bake_space_transform=True,
            path_mode='COPY',
            embed_textures=True,
        )
    finally:
        bpy.data.objects.remove(dup, do_unlink=True)
        if bake_image is not None:
            bpy.data.materials.remove(bpy.data.materials["P2M_BakedFace"], do_unlink=True)


def _try_export_and_build(head_name: str, head_obj, bake_image, texture_path: str,
                          audio_path: str = "", notify: bool = True) -> bool:
    """One attempt at: export head_obj (with bake_image already baked and
    assigned as its material, if any) to FBX, write
    person2meta_config.json, and launch Unreal. Returns True once a real
    (non-broken) FBX was written and Unreal was launched, False if it
    should be retried.

    Only the FBX export + config write + launch are retried here -- the
    texture bake itself already happened once, synchronously, in the
    button's own execute() (see P2M_OT_ExportAndBuildMetaHuman) BEFORE this
    got scheduled onto a timer. Confirmed: baking/exporting the texture
    from within a bpy.app.timers callback (a lower-context environment than
    a real UI button click) produced a visibly different, wrong texture
    than the exact same operators called directly from execute() -- even
    though both silently reported {'FINISHED'} with no error. The FBX
    export itself has a separate, already-diagnosed flakiness
    (bpy.ops.export_scene.fbx silently writing a near-empty file on the
    first call right after the button is clicked, succeeding on a later
    attempt with no other change) which DOES need this retry loop -- verify
    the actual file size on disk, don't trust the operator's reported
    success alone."""
    select_and_activate(head_obj)  # re-select in case a prior tick lost the active object
    _set_progress(0.15, f"Exporting FBX for '{head_name}'...")

    vert_count = len(head_obj.data.vertices)
    log(f"[person2meta] Exporting head '{head_obj.name}': {vert_count} verts")
    if vert_count < 1000:
        log(f"[person2meta] ERROR: Head mesh only has {vert_count} verts -- "
            f"too low to be the real FaceBuilder head (expect ~15000+). "
            f"Refusing to export/launch Unreal on what looks like an empty "
            f"or wrong object.")
        return True  # not something retrying will fix

    work_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(work_dir, exist_ok=True)
    fbx_path = os.path.join(work_dir, f"{head_name}.fbx")

    # Per-head config/log files, NOT the shared CONFIG_PATH/CONFORM_LOG_PATH
    # constants -- in a multi-head session, exporting head 2 while head 1's
    # Unreal instance is still starting up (which can easily take longer
    # than the gap between two exports) would otherwise overwrite the
    # config file out from under head 1 before it ever got read, and both
    # Unreal windows could end up building from whichever config happened
    # to be on disk at the moment each one got around to reading it.
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in head_name)
    config_path = os.path.join(work_dir, f"person2meta_config_{safe_name}.json")
    conform_log_path = os.path.join(work_dir, f"person2meta_conform_log_{safe_name}.txt")

    # Same export settings as keentools_fb.export_head_to_fbx itself (see
    # facebuilder/utils/operator_action.py's export_head_to_fbx), plus a
    # real-world height normalization -- see _export_head_fbx_normalized.
    _export_head_fbx_normalized(head_obj, fbx_path, bake_image=bake_image)
    # A near-empty FBX (a few KB) imports "successfully" into Unreal as a
    # near-empty StaticMesh, which then hangs/stalls the rest of the
    # conform pipeline instead of failing loudly -- verify real content
    # landed on disk before ever handing off to Unreal.
    size = os.path.getsize(fbx_path) if os.path.exists(fbx_path) else 0
    if size < 50_000:
        log(f"[person2meta] Export attempt produced only {size} bytes -- retrying.")
        return False
    log(f"[person2meta] Exported head FBX: {fbx_path} ({size} bytes)")

    config = {
        "head_name": head_name,
        "fbx_path": fbx_path,
        "import_destination_path": "/Game/person2meta",
        "imported_mesh_name": f"SM_{head_name}",
        "output_package_path": "/Game/person2meta",
        "output_asset_name": f"MHC_{head_name}",
    }
    if bake_image is not None:
        config["texture_path"] = texture_path
    if audio_path:
        config["audio_path"] = audio_path

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    log(f"[person2meta] Wrote config: {config_path}")

    _set_progress(0.3, "Launching Unreal...")
    # Truncate any stale log from a previous run so schedule_unreal_progress_poll
    # doesn't immediately see old markers and jump straight to "Done!".
    try:
        os.remove(conform_log_path)
    except OSError:
        pass

    # Tell conform_to_metahuman.py which config/log to use for THIS head via
    # env vars (it falls back to its own hardcoded defaults if unset, for
    # backward compatibility with running it manually) -- see that script's
    # own CONFIG_PATH/LOG_FILE_PATH definitions.
    unreal_env = os.environ.copy()
    unreal_env["P2M_CONFIG_PATH"] = config_path
    unreal_env["P2M_CONFORM_LOG_PATH"] = conform_log_path

    subprocess.Popen(
        [
            UNREAL_EDITOR_EXE,
            UNREAL_PROJECT_PATH,
            f"-ExecutePythonScript={CONFORM_SCRIPT_PATH}",
        ],
        env=unreal_env,
    )
    log(f"[person2meta] Launched Unreal to build MetaHuman '{head_name}'.")
    on_finished = (
        (lambda: notify_completion(f"MetaHuman '{head_name}' is ready in Unreal!"))
        if notify else None
    )
    schedule_unreal_progress_poll(conform_log_path, has_audio=bool(audio_path), on_finished=on_finished)
    return True


# Friendly labels for the progress panel, keyed to substrings of the log
# lines conform_to_metahuman.py already writes to its own (per-head, see
# _try_export_and_build) log file as it runs (see that file's log() calls)
# -- ordered so later matches always overtake earlier ones.
BASE_UNREAL_STEP_MARKERS = [
    ("Importing FBX for", 0.35, "Importing scan into Unreal..."),
    ("Extracting target mesh topology", 0.45, "Analyzing scan mesh..."),
    ("Creating MetaHumanCharacter asset", 0.55, "Creating MetaHuman..."),
    ("Running conform", 0.65, "Conforming face to MetaHuman (can take a minute)..."),
    ("Conform complete", 0.7, "Conform complete -- applying texture..."),
    ("Found synthesized texture", 0.78, "Importing texture..."),
    ("Applied face Basecolor texture override", 0.85, "Requesting auto-rig..."),
    ("Requesting auto-rig", 0.9, "Auto-rigging (joints + blendshapes)... this can take a while"),
]
# Appended only when an audio file was picked (see schedule_unreal_progress_poll's
# has_audio param) -- without it, "Auto-rig complete" is the actual final step,
# since conform_to_metahuman.py never emits these lines in that case.
NO_AUDIO_FINAL_MARKER = ("Auto-rig complete", 1.0, "Done! MetaHuman ready in Unreal.")
AUDIO_UNREAL_STEP_MARKERS = [
    ("Auto-rig complete", 0.91, "Auto-rig complete -- animating from audio..."),
    ("Importing audio for animation", 0.93, "Importing audio..."),
    ("Requesting audio-driven animation", 0.95, "Setting up audio-driven animation..."),
    ("Running audio-driven animation solve", 0.97, "Running audio-driven animation solve..."),
    ("Exporting animation sequence", 0.99, "Exporting animation sequence..."),
    # Deliberately the LAST tracked marker, not export_synced_level_sequence's
    # own "Synced audio+animation preview" log line -- that step is best-
    # effort/non-fatal (see its docstring) and could just log a WARNING
    # instead on failure, which would leave the progress bar stuck short of
    # 100% forever if it were the thing polling waited on.
    ("Audio-driven animation complete", 1.0, "Done! MetaHuman animated and ready in Unreal."),
]


def _set_progress(fraction: float, status: str) -> None:
    scene = bpy.context.scene
    scene.p2m_progress = fraction
    scene.p2m_status = status
    scene.p2m_running = True
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def schedule_unreal_progress_poll(conform_log_path: str, interval: float = 1.5,
                                  has_audio: bool = False, on_finished=None,
                                  max_wait_seconds: float = None) -> None:
    """Polls conform_to_metahuman.py's own (per-head) log file and maps
    known lines to a friendly step + progress fraction for the person2meta
    panel, so it keeps showing real movement instead of looking frozen
    while Unreal grinds through the conform (a separate, non-Blender
    process this script otherwise has no visibility into).

    has_audio picks which marker list to use -- conform_to_metahuman.py
    only emits the audio-driven-animation log lines when an audio_path was
    actually set in the config, so without has_audio=True this would just
    poll forever past "Auto-rig complete" waiting for lines that never
    come.

    on_finished, if given, is called once the final marker is reached (or,
    if max_wait_seconds is set, once that timeout is hit instead) --
    P2M_OT_ExportAllHeads uses this (via its OWN separate call to this
    function, alongside the one _try_export_and_build already makes for
    the progress panel) to know when it's safe to start the next head's
    export. Waiting for the terminal LOG marker rather than the Unreal
    PROCESS exiting matters here: conform_to_metahuman.py deliberately
    keeps the editor open afterward (set_keep_python_script_alive) so you
    can inspect the result, so it never exits on its own. max_wait_seconds
    caps how long a single stuck/crashed build can stall a multi-head
    batch before it just gives up and moves on."""
    markers = BASE_UNREAL_STEP_MARKERS + (
        AUDIO_UNREAL_STEP_MARKERS if has_audio else [NO_AUDIO_FINAL_MARKER]
    )
    seen_index = [-1]
    elapsed = [0.0]

    def _poll():
        try:
            if not os.path.exists(conform_log_path):
                content = ""
            else:
                with open(conform_log_path, "r") as f:
                    content = f.read()
        except OSError:
            content = ""

        for i, (marker, fraction, label) in enumerate(markers):
            if i > seen_index[0] and marker in content:
                seen_index[0] = i
                _set_progress(fraction, label)

        if seen_index[0] >= len(markers) - 1:
            if on_finished is not None:
                on_finished()
            return None  # reached the final marker, stop polling

        elapsed[0] += interval
        if max_wait_seconds is not None and elapsed[0] >= max_wait_seconds:
            log(f"[person2meta] Gave up waiting on Unreal build ({conform_log_path}) "
                f"after {max_wait_seconds}s -- continuing anyway.")
            if on_finished is not None:
                on_finished()
            return None
        return interval

    bpy.app.timers.register(_poll, first_interval=interval)


def schedule_export_and_build(head_name: str, head_obj, bake_image, texture_path: str,
                              audio_path: str = "", notify: bool = True,
                              interval: float = 1.0, max_attempts: int = 10) -> None:
    attempts_left = [max_attempts]

    def _attempt():
        if _try_export_and_build(head_name, head_obj, bake_image, texture_path, audio_path, notify):
            return None  # success (or an unretryable error), stop
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            log(f"[person2meta] Gave up exporting '{head_name}' after repeated "
                f"broken exports -- try clicking Export & Build MetaHuman again.")
            return None
        return interval

    bpy.app.timers.register(_attempt, first_interval=0.0)


class P2M_OT_RunExportAndBuild(bpy.types.Operator):
    """Does the actual work: exports the current FaceBuilder head to FBX,
    writes person2meta_config.json, and launches Unreal to run
    conform_to_metahuman.py. Split out from P2M_OT_ExportAndBuildMetaHuman
    so the Audio/Video prompt chain can land here regardless of which
    branch the user picks."""
    bl_idname = "person2meta.run_export_and_build"
    bl_label = "Export & Build MetaHuman"
    bl_description = "Export this head to FBX and run the Unreal conform pipeline"
    bl_options = {'INTERNAL'}

    # SKIP_SAVE on both -- see P2M_OT_ExportAndBuildMetaHuman.head_name's
    # comment for why (Blender remembers operator property values across
    # invocations by default; these are always explicitly passed in by the
    # caller here, so stale carry-over is pure risk with no upside).
    head_name: bpy.props.StringProperty(name="Head Name", default="", options={'SKIP_SAVE'})
    # If set, gets carried through to person2meta_config.json's "audio_path" --
    # conform_to_metahuman.py picks it up after auto-rig and runs it through
    # MetaHuman's audio-driven animation (see that script's
    # run_audio_driven_animation). Empty means no animation step.
    audio_path: bpy.props.StringProperty(name="Audio Path", default="", options={'SKIP_SAVE'})
    # P2M_OT_ExportAllHeads sets this False per-head so a multi-head batch
    # doesn't fire a notification after every single head -- it shows its
    # own ONE notification once the whole batch is done instead.
    notify: bpy.props.BoolProperty(name="Notify When Done", default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        head_name = self.head_name or f"head_{int(time.time())}"

        try:
            settings = get_fb_settings()
        except Exception as e:
            self.report({'ERROR'}, f"Could not reach FaceBuilder settings: {e}")
            return {'CANCELLED'}

        # Resolved from the SELECTED object, not settings.current_headnum --
        # see resolve_active_headnum's docstring. Without this, exporting a
        # head other than whichever was created/pin-moded LAST silently
        # exported the WRONG head's mesh/texture in multi-head sessions.
        headnum = resolve_active_headnum(context, settings)
        head = settings.get_head(headnum)
        if head is None or head.headobj is None:
            self.report({'ERROR'}, "No FaceBuilder head found to export.")
            return {'CANCELLED'}

        select_and_activate(head.headobj)
        _set_progress(0.02, "Starting export...")

        work_dir = os.path.dirname(CONFIG_PATH)
        os.makedirs(work_dir, exist_ok=True)
        texture_path = os.path.join(work_dir, f"{head_name}_texture.png")

        _set_progress(0.08, "Baking face texture...")
        # Bake the texture HERE, directly in this real button-click
        # context -- NOT via a bpy.app.timers callback. See
        # _try_export_and_build's docstring for why: the exact same bake
        # operators produced a different, wrong result when run from a
        # timer tick instead of a real UI execute().
        bake_image = bake_mh_face_texture(headnum, head, texture_path)

        schedule_export_and_build(
            head_name, head.headobj, bake_image, texture_path, self.audio_path, self.notify
        )
        self.report({'INFO'}, f"Exporting '{head_name}'...")
        return {'FINISHED'}


class P2M_OT_ExportAllHeads(bpy.types.Operator):
    """One click, exports every FaceBuilder head in the scene -- one at a
    time. Each head goes through the SAME person2meta.run_export_and_build
    operator the single "Export & Build MetaHuman" button uses (invoked
    here via bpy.ops(...) from a timer callback for every head after the
    first, same proven pattern schedule_audio_video_prompt_poll already
    uses to trigger export from its own timer -- confirmed to bake
    correctly, unlike calling the raw KeenTools bake operators directly
    from a timer), waiting for that head's Unreal build to reach a
    terminal log marker (its own separate schedule_unreal_progress_poll
    watcher, alongside the one run_export_and_build starts for the
    progress panel) before starting the next head.

    Waits for the BUILD to finish, not the Unreal WINDOW to close --
    conform_to_metahuman.py deliberately leaves each result open to
    inspect (set_keep_python_script_alive), so by the end of a multi-head
    batch you may see several Unreal windows still open, but only ever one
    actively building at a time -- avoiding the config-file/log collisions
    multiple simultaneous launches caused before this existed.

    Skips the Audio/Video prompt entirely (always exports with no
    animation) -- for audio-driven animation on a specific head, use the
    single Export & Build MetaHuman button on that head instead."""
    bl_idname = "person2meta.export_all_heads"
    bl_label = "Export All Heads"
    bl_description = ("Export every head, one at a time, waiting for each Unreal "
                      "build to finish before starting the next")
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            settings = get_fb_settings()
        except Exception as e:
            self.report({'ERROR'}, f"Could not reach FaceBuilder settings: {e}")
            return {'CANCELLED'}

        queue = [(i, h) for i, h in enumerate(settings.heads) if h.headobj is not None]
        if not queue:
            self.report({'ERROR'}, "No FaceBuilder heads found to export.")
            return {'CANCELLED'}

        total = len(queue)
        remaining = list(queue)

        def _process_next():
            if not remaining:
                log("[person2meta] Export All Heads: finished all heads.")
                notify_completion(f"All {total} MetaHuman(s) are ready in Unreal!")
                return
            headnum, head = remaining.pop(0)
            done_so_far = total - len(remaining)
            name = head.headobj.get("p2m_head_name") or head.headobj.name or f"head_{int(time.time())}"

            work_dir = os.path.dirname(CONFIG_PATH)
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            conform_log_path = os.path.join(work_dir, f"person2meta_conform_log_{safe_name}.txt")
            try:
                os.remove(conform_log_path)
            except OSError:
                pass

            log(f"[person2meta] Export All Heads: starting '{name}' ({done_so_far}/{total})...")
            select_and_activate(head.headobj)
            # notify=False -- this batch shows its own ONE notification above
            # once every head is done, not one per head.
            bpy.ops.person2meta.run_export_and_build('EXEC_DEFAULT', head_name=name, notify=False)
            schedule_unreal_progress_poll(
                conform_log_path, on_finished=_process_next, max_wait_seconds=1200
            )

        _process_next()
        self.report({'INFO'}, f"Exporting {total} head(s), one at a time...")
        return {'FINISHED'}


AUDIO_VIDEO_PROMPT_SCRIPT = os.path.join(os.path.dirname(__file__), "audio_video_prompt.py")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


def schedule_audio_video_prompt_poll(head_name: str, result_path: str,
                                     interval: float = 0.5, max_attempts: int = 1200) -> None:
    """Polls for the JSON result file audio_video_prompt.py writes once its
    Tkinter window closes (see that script's own docstring for why it's a
    separate process instead of a Blender popup).

    Any picked AUDIO file gets carried through to
    person2meta_config.json's "audio_path" (see P2M_OT_RunExportAndBuild /
    _try_export_and_build), which conform_to_metahuman.py picks up after
    auto-rig to run MetaHuman's audio-driven animation. Video is not wired
    up yet -- Epic's own pipeline for that needs footage from the LiveLink
    Face capture app, not an arbitrary video file, which is a bigger
    separate effort -- so any video file picked is just logged and
    otherwise ignored; the export still proceeds normally either way."""
    attempts_left = [max_attempts]

    def _poll():
        if not os.path.exists(result_path):
            attempts_left[0] -= 1
            if attempts_left[0] <= 0:
                log("[person2meta] Audio/Video prompt timed out waiting for a response.")
                return None
            return interval

        try:
            with open(result_path, "r") as f:
                result = json.load(f)
        except (OSError, json.JSONDecodeError):
            return interval  # file may still be mid-write, retry shortly

        try:
            os.remove(result_path)
        except OSError:
            pass

        choice = result.get("choice", "cancelled")
        if choice == "cancelled":
            log("[person2meta] Audio/Video prompt cancelled -- nothing exported.")
            return None

        files = result.get("files", []) if choice != "no" else []
        audio_path = next(
            (f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS), ""
        )
        video_files = [f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
        if video_files:
            log(f"[person2meta] Audio/Video prompt: video file(s) {video_files} picked, but "
                f"video-driven animation isn't supported yet -- skipping those, export still "
                f"proceeds.")
        if audio_path:
            log(f"[person2meta] Audio/Video prompt: will animate with audio file: {audio_path}")
        elif choice != "no":
            log(f"[person2meta] Audio/Video prompt: {choice} selected, picked {files}, but none "
                f"matched a supported audio extension ({sorted(AUDIO_EXTENSIONS)}) -- "
                f"proceeding with export only (no animation).")
        else:
            log("[person2meta] Audio/Video prompt: No -- proceeding with normal export.")

        bpy.ops.person2meta.run_export_and_build(
            'EXEC_DEFAULT', head_name=head_name, audio_path=audio_path
        )
        return None

    bpy.app.timers.register(_poll, first_interval=interval)


class P2M_OT_ExportAndBuildMetaHuman(bpy.types.Operator):
    """Entry point for the 'Export & Build MetaHuman' button. First asks for
    a head name, then launches audio_video_prompt.py (a separate Tkinter
    process -- see its docstring for why) to ask whether to attach
    audio/video, a placeholder prompt chain for future animation-driving
    input. Choosing "No" (or letting it time out/cancel) hands off to
    P2M_OT_RunExportAndBuild to do the actual export + Unreal launch."""
    bl_idname = "person2meta.export_and_build_metahuman"
    bl_label = "Export & Build MetaHuman"
    bl_description = "Export this head to FBX and run the Unreal conform pipeline"
    bl_options = {'REGISTER'}

    # SKIP_SAVE -- Blender otherwise remembers this property's LAST typed/
    # confirmed value across invocations of this same operator (its usual
    # "adjust last operation" convenience), keyed by bl_idname regardless of
    # which head is selected. Without this, self.head_name was never
    # actually empty on a second click, so the "if not self.head_name"
    # auto-suggest below never re-ran -- the dialog just kept showing
    # whichever name was used the FIRST time, for every head after that.
    head_name: bpy.props.StringProperty(name="Head Name", default="", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        if not self.head_name:
            # Multi-head sessions (create_multiple_heads) tag each head's
            # object with the name given for it in the picker -- prefer
            # that over a generic timestamp so the suggested name actually
            # matches the person this head is for. Resolved from the
            # SELECTED object (resolve_active_headnum), not
            # settings.current_headnum -- see that function's docstring for
            # why current_headnum alone gave the wrong head's name back in
            # multi-head sessions.
            try:
                settings = get_fb_settings()
                headnum = resolve_active_headnum(context, settings)
                head = settings.get_head(headnum)
                if head is not None and head.headobj is not None:
                    suggested = head.headobj.get("p2m_head_name")
                    if suggested:
                        self.head_name = suggested
            except Exception:
                pass
        if not self.head_name:
            self.head_name = f"head_{int(time.time())}"
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "head_name")

    def execute(self, context):
        head_name = self.head_name or f"head_{int(time.time())}"

        work_dir = os.path.dirname(CONFIG_PATH)
        os.makedirs(work_dir, exist_ok=True)
        result_path = os.path.join(work_dir, f"{head_name}_av_prompt.json")
        try:
            os.remove(result_path)  # clear any stale result from a previous run
        except OSError:
            pass

        subprocess.Popen(["python", AUDIO_VIDEO_PROMPT_SCRIPT, result_path])
        schedule_audio_video_prompt_poll(head_name, result_path)
        self.report({'INFO'}, "Waiting on Audio/Video prompt...")
        return {'FINISHED'}


class P2M_PT_Panel(bpy.types.Panel):
    bl_label = "person2meta"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'person2meta'

    def draw(self, context):
        self.layout.operator(P2M_OT_ExportAndBuildMetaHuman.bl_idname, icon='EXPORT')
        self.layout.operator(P2M_OT_ExportAllHeads.bl_idname, icon='EXPORT')

        scene = context.scene
        if scene.p2m_running:
            box = self.layout.box()
            box.label(text=scene.p2m_status, icon='INFO')
            row = box.row()
            row.enabled = False  # display-only, not a real user-draggable slider
            row.prop(scene, "p2m_progress", text="")


def register_person2meta_ui() -> None:
    for cls in (
        P2M_OT_RunExportAndBuild,
        P2M_OT_ExportAndBuildMetaHuman,
        P2M_OT_ExportAllHeads,
        P2M_PT_Panel,
    ):
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass  # already registered (e.g. script re-run in the same session)

    bpy.types.Scene.p2m_progress = bpy.props.FloatProperty(
        name="Progress", subtype='FACTOR', min=0.0, max=1.0, default=0.0
    )
    bpy.types.Scene.p2m_status = bpy.props.StringProperty(name="Status", default="")
    bpy.types.Scene.p2m_running = bpy.props.BoolProperty(name="Running", default=False)


def create_multiple_heads(heads_def: list) -> None:
    """Creates one FaceBuilder head per entry in heads_def (each
    {"name": str, "images": [str, ...]}, from launch_picker.py's "Multiple
    Heads" flow), auto-detecting/aligning each in turn.

    Heads are processed ONE AT A TIME, not concurrently -- chained via
    schedule_auto_detect_all_cameras's on_finished callback -- so their
    face-detection timers don't fight over the same Blender UI context
    (see that function's own docstring for the exact race this avoids).

    Each head's Blender object gets a custom "p2m_head_name" property set
    to the name given for it here, so P2M_OT_ExportAndBuildMetaHuman can
    pre-fill the export dialog with the right name instead of a generic
    timestamp, once you select that head and click Export.

    The FaceBuilder tab is only auto-focused/entered for the FIRST head --
    entering pin mode there (select_camera) is also what populates
    KeenTools' cached viewport "work area" (see
    schedule_facebuilder_tab_and_pin_click's own docstring), which auto-
    detect needs to run AT ALL, for every head, not just the first --
    confirmed the hard way: deferring tab-focus until after every head's
    auto-detect had already run made EVERY camera on EVERY head fail, not
    just the first several. So for head 1, tab-focus runs and is waited on
    BEFORE auto-detect starts; for head 2 onward the work area is already
    cached, so auto-detect can start immediately -- and since head 2 isn't
    created until head 1's tab-focus + auto-detect are both fully done, the
    original glitch (creating head 2's object while head 1's pinned
    camera-locked view was still settling) is still avoided. Switch
    between heads afterward the same way you would in any multi-head
    FaceBuilder project (its own Heads list in the N-panel)."""
    remaining_defs = list(heads_def)
    total_heads = len(heads_def)
    is_first_head = [True]

    def _process_next():
        done_so_far = total_heads - len(remaining_defs)
        if not remaining_defs:
            log("[person2meta] Finished setting up all heads.")
            write_status("Ready! Adjust pins as needed.", done=True)
            return
        entry = remaining_defs.pop(0)
        name = entry.get("name") or f"head_{int(time.time())}"
        images = entry.get("images", [])
        if not images:
            log(f"[person2meta] Skipping '{name}' -- no images given.")
            _process_next()
            return

        log(f"[person2meta] Setting up head '{name}' ({len(images)} image(s))...")
        write_status(f"Loading photos for '{name}' ({done_so_far + 1}/{total_heads})...")
        headnum = create_facebuilder_head_and_load_images(images)

        try:
            settings = get_fb_settings()
            head = settings.get_head(headnum)
            if head is not None and head.headobj is not None:
                head.headobj["p2m_head_name"] = name
        except Exception as e:
            log(f"[person2meta] Could not tag head '{name}' with its name ({e}).")

        status_prefix = f"'{name}' ({done_so_far + 1}/{total_heads}): "
        if is_first_head[0]:
            is_first_head[0] = False
            schedule_facebuilder_tab_and_pin_click(
                headnum,
                on_finished=lambda: schedule_auto_detect_all_cameras(
                    headnum, on_finished=_process_next, status_prefix=status_prefix
                ),
            )
        else:
            schedule_auto_detect_all_cameras(
                headnum, on_finished=_process_next, status_prefix=status_prefix
            )

    write_status("Starting up...")
    _process_next()


def main():
    write_status("Starting up...")

    multi_heads = get_multi_head_definitions()
    if multi_heads:
        create_multiple_heads(multi_heads)
        return

    image_paths = get_image_paths()
    if not image_paths:
        write_status("Nothing to load.", done=True)
        return
    write_status("Loading photos...")
    headnum = create_facebuilder_head_and_load_images(image_paths)
    # Sequenced (tab-focus THEN auto-detect), not concurrent -- see
    # create_multiple_heads's docstring: auto-detect needs select_camera to
    # have already populated KeenTools' cached viewport "work area" at
    # least once, or every camera fails with a None-area error, not just
    # early ones. Running them concurrently "worked" here before only
    # because auto-detect's own retry budget (schedule_auto_detect_all_cameras)
    # happened to outlast tab-focus winning that race.
    schedule_facebuilder_tab_and_pin_click(
        headnum,
        on_finished=lambda: schedule_auto_detect_all_cameras(
            headnum, on_finished=lambda: write_status("Ready! Adjust pins as needed.", done=True)
        ),
    )


register_person2meta_ui()  # always available, whether or not photos were auto-loaded

if __name__ == "__main__":
    main()
