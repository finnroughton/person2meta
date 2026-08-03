# person2meta - current status for debugging

## What this project does
Turns uploaded photos/video of a face into a rigged, animation-ready
MetaHuman in Unreal Engine, via KeenTools' Cloud API for 3D reconstruction
and Unreal's MetaHuman Character Python API for conforming + rigging.

## Pipeline stages and their current state

1. **`scripts/run_pipeline.py`** (system Python, run on your machine)
   Picks 2-15 face photos + one portrait via file-picker windows, sends them
   to KeenTools' Cloud API (`https://api.keentools.workers.dev`), polls until
   reconstruction completes, downloads the resulting mesh (OBJ+MTL+texture
   ZIP), calls Blender headless to convert it to FBX, and writes
   `person2meta_config.json` for the next stage to read.
   **STATUS: confirmed working end-to-end.**

2. **`scripts/convert_obj_to_fbx.py`** (called automatically by run_pipeline.py
   via Blender in `--background` mode)
   Imports the OBJ into a fresh Blender scene and exports it as FBX.
   **STATUS: confirmed working.**

3. **`scripts/conform_to_metahuman.py`** (run manually inside Unreal Engine
   5.8's Python console/tab, NOT run automatically by run_pipeline.py)
   Reads `person2meta_config.json`, imports the FBX as a static mesh,
   creates a `MetaHumanCharacter` asset, and calls
   `MetaHumanCharacterEditorSubsystem.conform_to_target_meshes()` to fit
   the MetaHuman head shape to the imported mesh.
   **STATUS: BROKEN / IN PROGRESS -- this is what needs debugging.**

## CONFIRMED scale mismatch (most recent finding, likely primary cause)

Verified directly via Unreal's Python API on the imported `SM_natalie` static mesh:

```python
target_mesh = unreal.load_asset("/Game/person2meta/SM_natalie")
b = target_mesh.get_bounds()
print(b.box_extent)  # -> Vector(x=179.94, y=134.42, z=187.51)
```

`box_extent` is half-extents, so the real bounding box is **~360 x 269 x 375
(cm, presumably, since Unreal's internal unit is cm)** -- this matches the
"Approx Size: 359x268x375" stat shown in the Static Mesh Editor viewport
overlay exactly, confirming that stat IS the real bounding box (an earlier
theory that it was measuring something else, like a distance-field texture
footprint, was wrong).

This is a ~3.75 meter tall "head" -- a real human head+neck+shoulders bust
should be roughly 0.3-0.5m tall. Something is inflating the scale by
roughly 7-10x between the source data and what Unreal imports.

**The confusing part**: in Blender, selecting the same mesh (after OBJ
import, or checking the freshly-exported FBX) and reading the N-panel's
"Dimensions" field showed approximately **0.72 x 0.72 x 1.0 (meters)** --
a completely reasonable size. So Blender and Unreal disagree by roughly
3.75x on the same file, which were both checked after the fix below.

**A fix was already attempted and did NOT resolve this**: modified
`convert_obj_to_fbx.py` to explicitly bake object transforms
(`bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)`
after import, before export) and explicitly set FBX export parameters
(`global_scale=1.0`, `apply_unit_scale=True`,
`apply_scale_options='FBX_SCALE_ALL'`, `axis_forward='-Z'`, `axis_up='Y'`)
instead of relying on Blender's defaults. Re-ran the full OBJ->FBX->Unreal
reimport cycle after this fix -- Unreal's reported bounds were IDENTICAL
(down to the same exact numbers) both before and after this fix, which is
itself suspicious -- it suggests either the fix genuinely made no
difference, or the reimport in Unreal did not actually pick up the newly
exported FBX (file timestamp was manually confirmed to match the latest
export, for whatever that's worth, but this was not verified any more
rigorously than that).

**Not yet checked**: the Scale X/Y/Z values in Blender's own N-panel (as
opposed to just the Dimensions field) on the object right before/after
export, to confirm whether `transform_apply` actually zeroed out/normalized
scale to 1,1,1 as intended.

**Not yet checked**: whether this scale inflation is present all the way
back in the original KeenTools OBJ output itself (i.e., is the OBJ file
itself already "too large" in whatever units it uses, independent of
anything Blender or Unreal do with it), versus being introduced somewhere
in the Blender->FBX->Unreal chain. Worth checking OBJ file's raw vertex
coordinate values directly (e.g. via a text editor or simple script) as a
completely independent data point.

## LATEST UPDATE: scale bug fixed, but new blank-result problem (most recent, active)

**Good news first**: the scale mismatch above WAS real and IS now fixed.
Added an auto-normalization step to `convert_obj_to_fbx.py` that measures
the mesh's actual height after OBJ import and rescales it to a target of
0.4m (reasonable placeholder for a head+neck+shoulders bust), baking the
scale before FBX export. Confirmed via Unreal's `get_bounds()` that the
imported mesh now measures ~38x29x40cm instead of the previous ~360x269x375cm.
Also re-enabled the camera/landmark term (previously disabled for
diagnosis) and added file-based logging (writes to
`person2meta_conform_log.txt` next to the config file, in addition to
console output) since the editor had frozen once before mid-run.

**With the scale fix in place, running `conform_to_metahuman.py` (HEAD_ONLY,
camera term enabled) now completes without freezing or crashing** -- prints
`Conform complete -- saved /Game/person2meta/MHC_natalie` and exits cleanly.
This is the first fully clean run of the whole script.

**However, opening the resulting `MHC_natalie` asset shows a completely
blank viewport** -- no head, no body, nothing renders. This was confirmed
NOT to be a UI/editor glitch: restarted Unreal entirely and the asset is
still blank afterward.

**Leading theory**: this log line, present in the successful run, is
suspected to be the real root cause rather than harmless noise:
```
Autorigger: Warning: (BodyShapeEditor.cpp, l2874): AlignToTargetMesh[head]: insufficient correspondences
```
Theory: `conform_to_target_meshes` returns `True` (so the script's own
success check doesn't catch anything wrong), but the underlying solve may
silently fail to produce meaningful geometry when it can't establish good
correspondences between an arbitrary/unstructured vertex soup (our
KeenTools scan) and MetaHuman's expected template layout -- resulting in
"success" that's actually empty.

This might connect to a parameter we deliberately left out: the original
Epic example script (`example_conform_from_custom_mesh.py`) has a large
commented-out optional block for `key_point_targets` -- explicit
vertex-index-to-3D-position pin constraints meant to help the solver
anchor correspondences. We never populated this, assuming it was purely
optional. **It may not actually be optional for an arbitrary/unstructured
mesh like ours** -- possibly required specifically when there's no
pre-existing correspondence between the input mesh's vertex layout and
whatever the solver expects by default.

**Supporting data point**: the very first attempt (before `HEAD_ONLY` was
discovered) used `TargetPartsType.COMBINED` with `body_vertices`, and that
attempt rendered a distorted-but-visible result, not a blank one. This is
worth investigating -- it suggests `COMBINED` and `HEAD_ONLY` may handle
correspondence-finding differently, and `HEAD_ONLY` might be the one with
the actual problem, not necessarily the more "correct" choice we assumed
it was.

**Concrete next steps to try**:
1. Populate `key_point_targets` with at least a handful of manually-identified
   correspondences (e.g., a few obviously identifiable points like eye
   corners, nose tip, mouth corners) between our mesh's known vertex indices
   and reasonable target positions, and see if that resolves the "insufficient
   correspondences" warning and produces a non-blank result.
2. Try `TargetPartsType.HEAD_AND_BODY` (the 4th enum value, not yet tried)
   as a middle ground between `COMBINED` and `HEAD_ONLY`.
3. Try re-testing `COMBINED` now that the scale bug is fixed -- the original
   COMBINED distortion may have been partly or entirely caused by the same
   scale bug, and might look completely different (possibly correct, or at
   least non-blank) now that the mesh is realistically sized.
4. Check whether `conform_to_target_meshes`'s return value can be `True`
   even on a partial/degenerate solve -- if so, consider additional
   validation after the call (e.g., checking whether the character's actual
   mesh data/vertex count is non-zero) rather than trusting the boolean
   return alone.

## Earlier (now superseded) camera/landmark hypothesis -- kept for context

Running step 3 produces a severely distorted/warped head shape in the
resulting `MetaHumanCharacter` asset -- not a recognizable face, more like
stretched/mangled geometry (screenshots of the distortion are not included
here, but it looked like the mesh was turned inside-out / grossly stretched
around the eye and mouth area).

Debugging so far, in order:

- Original attempt used `TargetPartsType.COMBINED` with `body_vertices` /
  `body_vertex_indices` -- this is meant for a full-body scan, but our
  KeenTools output is a head-and-neck bust only. Switched to
  `TargetPartsType.HEAD_ONLY` with `head_vertices` / `head_vertex_indices`
  and `target_mesh_key.head_mesh` instead of `.combined_mesh`. **This did
  NOT fix the distortion** -- still looked warped after this change.

- Next diagnostic step (in progress, not yet confirmed): commented out the
  `curve_tracking_points` / `camera_view_info` / `image_size` block
  entirely, to test whether the guessed camera parameters (see below) are
  the cause, versus the vertex data itself being wrong. **The editor froze
  during this test run and we haven't yet confirmed the actual result** --
  this is where we stopped.

  **UPDATE on the freeze**: this was a genuine hard freeze, not just a slow
  operation -- Unreal had to be force-quit via Task Manager, it did not
  recover on its own. Unknown whether any `[person2meta]` print statements
  had appeared before the freeze (Output Log history was lost on restart,
  so this couldn't be confirmed after the fact). This freeze happened
  specifically on the vertex-only conform attempt (camera/landmark term
  disabled) -- it's not yet confirmed whether the earlier (distorted but
  non-frozen) COMBINED and HEAD_ONLY attempts also risked freezing, or
  whether this is unique to the vertex-only code path.

## Known-shaky assumptions worth scrutinizing

1. **Camera parameters are a total guess, not measured values**:
   ```python
   CAMERA_LOCATION = unreal.Vector(0.0, 100.0, 165.0)
   CAMERA_ROTATION = unreal.Rotator(pitch=0.0, yaw=-90.0, roll=0.0)
   CAMERA_FOV_DEG = 40.0
   ```
   These were copied from Epic's own example script
   (`example_conform_from_custom_mesh.py`, ships with Unreal Engine at
   `Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/Python/examples/`),
   which assumes a pre-rendered portrait from a KNOWN virtual camera. Our
   portrait is a real photo from an unknown phone/camera, so these values
   are unverified placeholders. This function's actual coordinate space
   (world units, mesh-relative, or something else) has not been confirmed.

2. **`get_mesh_data_for_conforming(target_mesh)` return values** were
   assumed to be `(vertices, indices, ...)` based on a comment in Epic's
   example script ("UE Python wraps `bool Func(In, &OutA, &OutB)`..."), but
   we never independently verified the actual shape/units/coordinate space
   of what this returns for OUR specific imported mesh.

3. **The FBX import path** (`import_fbx_as_static_mesh` in
   `conform_to_metahuman.py`) was fixed once already -- it originally grabbed
   the wrong sub-asset (a texture named `EyeLeft` instead of the actual
   `SM_natalie` static mesh) by blindly taking index `[0]` from
   `imported_object_paths`. Now loads the asset directly by expected path
   instead. This part is believed fixed but only lightly tested.

4. **Editor froze** on the most recent test run (camera/landmark term
   removed). Unknown whether this is: an infinite loop in the conform solve
   itself, an unrelated Unreal Editor hang, or something about running via
   `exec(open(...).read())` in the Python tab rather than a more robust
   execution method.

## Reference material available

The original, unmodified Epic example script this was adapted from
(`example_conform_from_custom_mesh.py`) is NOT included in this bundle, but
ships with any Unreal Engine 5.8 install at:
`Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/Python/examples/example_conform_from_custom_mesh.py`
-- worth diffing against if the adapted logic here is suspected of drifting
from the original in a meaningful way.

Two sibling example files were also inspected earlier in the process and
may be useful for the NEXT stage (auto-rigging + texture download, which
hasn't been attempted yet since conform isn't working):
`example_auto_rig.py` and `example_download_textures.py`, same folder.

## Practical suggestion given the freeze

Since Unreal's Output Log history is lost on a forced restart, it's hard to
know how far a frozen run actually got. Worth considering, before more
manual test runs:
- Redirect prints to a file too (e.g. also `print(..., file=open(log_path, "a"))`
  or Python's `logging` module writing to a file) so progress survives a
  crash/force-quit.
- Consider running via Unreal's command-line commandlet mode instead of the
  interactive editor's Python tab, which may make it easier to set a hard
  timeout and capture output externally, though note command-line/commandlet
  execution may itself interact differently with the Epic Cloud login flow
  needed for later steps (untested).

**Reproducibility confirmed**: `person2meta_conform_log.txt` (the file-logging
safety net added alongside the scale fix) shows three separate clean runs,
all completing successfully in a consistent ~45 seconds for the actual
conform step, no freezing, no errors:
```
10:44:50 [person2meta] Running conform (16 face curves)...
10:45:36 [person2meta] Conform complete -- saved /Game/person2meta/MHC_natalie
10:47:24 [person2meta] Running conform (16 face curves)...
10:48:07 [person2meta] Conform complete -- saved /Game/person2meta/MHC_natalie
10:54:34 [person2meta] Running conform (16 face curves)...
10:55:17 [person2meta] Conform complete -- saved /Game/person2meta/MHC_natalie
```
This is a reliable, reproducible bug -- not an intermittent one -- which
should make it more tractable to debug.

## Environment
- Unreal Engine 5.8 (project was originally created in 5.7, then switched
  via "Switch Unreal Engine version..." -- worth double-checking nothing
  from that migration is silently causing issues)
- Blender 5.2.0 LTS with KeenTools FaceBuilder/FaceTracker/GeoTracker addon
  (2026.3.0), used only as an OBJ->FBX converter in this pipeline, not for
  its own reconstruction features
- KeenTools Cloud API, trial account (credits are limited -- be careful
  about any fix that requires re-running the full KeenTools reconstruction,
  since that spends real trial credits; prefer testing against the already-
  downloaded FBX/OBJ where possible)
- Windows 11, PowerShell
  since that spends real trial credits; prefer testing against the already-
  downloaded FBX/OBJ where possible)
- Windows 11, PowerShell
