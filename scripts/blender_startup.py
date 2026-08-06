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
import time
from collections import defaultdict

LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "blender_startup_log.txt")

# ---- EDIT ONCE: must match conform_to_metahuman.py's own CONFIG_PATH ----
CONFIG_PATH = r"C:\Users\BrianBurritt\Downloads\person2meta_config.json"
# ---- EDIT ONCE: your Unreal project + engine install ----
UNREAL_PROJECT_PATH = r"C:\Users\BrianBurritt\Documents\Unreal Projects\MyProject2\MyProject2.uproject"
UNREAL_EDITOR_EXE = r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
CONFORM_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "conform_to_metahuman.py")
# --------------------------------------------------------------


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


def get_fb_settings():
    try:
        from bl_ext.user_default.keentools.addon_config import fb_settings
    except ImportError:
        from keentools.addon_config import fb_settings
    return fb_settings()


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


def schedule_auto_detect_all_cameras(headnum: int, interval: float = 0.2) -> None:
    """Runs FaceBuilder's face-detection on every loaded photo and places
    pins automatically, so the head shape actually fits all the photos
    (not just a generic template) and every camera has pins for texture
    baking later (bake_tex only pulls color from cameras with pins).

    Uses keentools_fb.pickmode_starter's execute() path directly (its own
    comment marks it "only for integration testing") instead of the normal
    interactive route (select_camera -> INVOKE_DEFAULT pin mode -> click
    "Pick Face"), which is modal and needs a live event loop ticking over
    real time to ever complete -- not something a script can drive
    reliably. execute() runs the same detection+pinning with no modal state
    involved and doesn't require pin mode to be active.

    Driven one camera per timer tick rather than a single blocking loop --
    a cold first-time ONNX model load for face detection can be slow enough
    to stall the whole startup script if run synchronously, which was
    observed to prevent the FaceBuilder-tab timer below from ever getting
    registered at all."""
    try:
        settings = get_fb_settings()
        head = settings.get_head(headnum)
    except Exception as e:
        log(f"[person2meta] Could not reach head for auto-detect ({e}), skipping.")
        return
    if head is None:
        return

    remaining = list(enumerate(head.cameras))

    def _attempt():
        if not remaining:
            log("[person2meta] Finished auto-detecting faces on all cameras.")
            return None
        i, cam = remaining.pop(0)
        result = bpy.ops.keentools_fb.pickmode_starter(
            headnum=headnum, camnum=i, auto_detect_single=True
        )
        log(f"[person2meta] Auto-detect face on camera {i}: {result}, "
            f"pins={cam.pins_count if cam.has_pins() else 0}")
        return interval if remaining else None

    bpy.app.timers.register(_attempt, first_interval=interval)


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


def _try_export_and_build(head_name: str, head_obj, bake_image, texture_path: str) -> bool:
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

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    log(f"[person2meta] Wrote config: {CONFIG_PATH}")

    subprocess.Popen([
        UNREAL_EDITOR_EXE,
        UNREAL_PROJECT_PATH,
        f"-ExecutePythonScript={CONFORM_SCRIPT_PATH}",
    ])
    log(f"[person2meta] Launched Unreal to build MetaHuman '{head_name}'.")
    return True


def schedule_export_and_build(head_name: str, head_obj, bake_image, texture_path: str,
                              interval: float = 1.0, max_attempts: int = 10) -> None:
    attempts_left = [max_attempts]

    def _attempt():
        if _try_export_and_build(head_name, head_obj, bake_image, texture_path):
            return None  # success (or an unretryable error), stop
        attempts_left[0] -= 1
        if attempts_left[0] <= 0:
            log(f"[person2meta] Gave up exporting '{head_name}' after repeated "
                f"broken exports -- try clicking Export & Build MetaHuman again.")
            return None
        return interval

    bpy.app.timers.register(_attempt, first_interval=0.0)


class P2M_OT_ExportAndBuildMetaHuman(bpy.types.Operator):
    """Exports the current FaceBuilder head to FBX, writes
    person2meta_config.json, and launches Unreal to run
    conform_to_metahuman.py -- the manual hand-off documented in this
    project's README, done from a single button instead."""
    bl_idname = "person2meta.export_and_build_metahuman"
    bl_label = "Export & Build MetaHuman"
    bl_description = "Export this head to FBX and run the Unreal conform pipeline"
    bl_options = {'REGISTER'}

    head_name: bpy.props.StringProperty(name="Head Name", default="")

    def invoke(self, context, event):
        if not self.head_name:
            self.head_name = f"head_{int(time.time())}"
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "head_name")

    def execute(self, context):
        head_name = self.head_name or f"head_{int(time.time())}"

        try:
            settings = get_fb_settings()
        except Exception as e:
            self.report({'ERROR'}, f"Could not reach FaceBuilder settings: {e}")
            return {'CANCELLED'}

        head = settings.get_head(settings.current_headnum)
        if head is None or head.headobj is None:
            self.report({'ERROR'}, "No FaceBuilder head found to export.")
            return {'CANCELLED'}

        select_and_activate(head.headobj)

        work_dir = os.path.dirname(CONFIG_PATH)
        os.makedirs(work_dir, exist_ok=True)
        texture_path = os.path.join(work_dir, f"{head_name}_texture.png")

        # Bake the texture HERE, directly in this real button-click
        # context -- NOT via a bpy.app.timers callback. See
        # _try_export_and_build's docstring for why: the exact same bake
        # operators produced a different, wrong result when run from a
        # timer tick instead of a real UI execute().
        bake_image = bake_mh_face_texture(settings.current_headnum, head, texture_path)

        schedule_export_and_build(head_name, head.headobj, bake_image, texture_path)
        self.report({'INFO'}, f"Exporting '{head_name}'...")
        return {'FINISHED'}


class P2M_PT_Panel(bpy.types.Panel):
    bl_label = "person2meta"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'person2meta'

    def draw(self, context):
        self.layout.operator(P2M_OT_ExportAndBuildMetaHuman.bl_idname, icon='EXPORT')


def register_person2meta_ui() -> None:
    for cls in (P2M_OT_ExportAndBuildMetaHuman, P2M_PT_Panel):
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass  # already registered (e.g. script re-run in the same session)


def main():
    image_paths = get_image_paths()
    if not image_paths:
        return
    headnum = create_facebuilder_head_and_load_images(image_paths)
    schedule_facebuilder_tab_and_pin_click(headnum)
    schedule_auto_detect_all_cameras(headnum)


register_person2meta_ui()  # always available, whether or not photos were auto-loaded

if __name__ == "__main__":
    main()
