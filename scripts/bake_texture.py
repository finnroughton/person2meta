"""
person2meta - bake the KeenTools scan's diffuse texture onto the MetaHuman-
conformed head mesh's own UV layout.

Why this exists: after conforming, the head geometry uses MetaHuman's UV
layout, but the KeenTools scan's texture was painted against the scan's OWN
(completely different) UV layout. Applying that texture directly produces a
scrambled/misaligned result. This script does a real 3D "Selected to Active"
bake in Blender -- projecting the scan's surface color onto the conformed
mesh's surface, output in the conformed mesh's UVs -- instead of a naive UV
copy.

Called automatically by conform_to_metahuman.py -- you shouldn't need to run
this directly, but if you do:

    "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background --python bake_texture.py -- SOURCE_SCAN.fbx CONFORMED_HEAD.fbx OUTPUT_TEXTURE.png
"""

import sys
import os
import bpy
import mathutils

# Blender passes its own args before "--"; everything after is ours.
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) != 3:
    print(
        f"[person2meta] ERROR: Usage: blender --background --python bake_texture.py -- "
        f"SOURCE_SCAN.fbx CONFORMED_HEAD.fbx OUTPUT_TEXTURE.png\n"
        f"       Got {len(argv)} argument(s) after '--': {argv}"
    )
    sys.exit(1)

SOURCE_FBX, TARGET_FBX, OUTPUT_TEXTURE = argv


def log(msg):
    print(f"[person2meta] {msg}")


def fail(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


if not os.path.isfile(SOURCE_FBX):
    fail(f"Source scan FBX does not exist: {SOURCE_FBX}")
if not os.path.isfile(TARGET_FBX):
    fail(f"Conformed head FBX does not exist: {TARGET_FBX}")

# scipy is needed for proper hole-filling (see below -- a plain diffusion fallback
# was tried first and produced visible interference-pattern artifacts). `pip install`
# targeting Blender's bundled python.exe lands in the user site-packages dir (Blender's
# own site-packages under Program Files isn't writable without admin rights), and
# Blender's embedded interpreter doesn't add that to sys.path by default -- so add it
# explicitly before importing.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `import p2m_settings`
import p2m_settings
USER_SITE_PACKAGES = p2m_settings.load_settings()["python_user_site_packages"]
if os.path.isdir(USER_SITE_PACKAGES) and USER_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, USER_SITE_PACKAGES)
try:
    import scipy  # noqa: F401 -- just checking availability early, used later for hole-filling
except ImportError:
    fail(
        "scipy is required for hole-filling. Install it into Blender's bundled Python once:\n"
        r'  "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe" -m pip install scipy'
        f"\n(expected to land in {USER_SITE_PACKAGES}, which this script adds to sys.path)"
    )


def world_bounds(obj):
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]
    center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    size = mathutils.Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    return center, size


bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.import_scene.fbx(filepath=SOURCE_FBX)
source_obj = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
source_obj.name = "SourceScan"

bpy.ops.import_scene.fbx(filepath=TARGET_FBX)
target_candidates = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o is not source_obj]
target_obj = max(target_candidates, key=lambda o: len(o.data.vertices))
target_obj.name = "TargetConformed"
log(f"source verts={len(source_obj.data.vertices)}, target verts={len(target_obj.data.vertices)}")
log(f"source UV maps: {[uv.name for uv in source_obj.data.uv_layers]}")
log(f"target UV maps: {[uv.name for uv in target_obj.data.uv_layers]}")

# Target is a skeletal mesh (Armature modifier + parented to its skeleton root).
# Moving .location on it directly doesn't behave as a simple rigid translate --
# the original parent-inverse matrix from Unreal's FBX export absorbs the change.
# Bake the current pose into plain mesh data, then fully unparent (keeping the
# resulting world transform) so positioning becomes predictable.
bpy.ops.object.select_all(action='DESELECT')
target_obj.select_set(True)
bpy.context.view_layer.objects.active = target_obj
bpy.ops.object.convert(target='MESH')
bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
log("converted target to plain unparented mesh")

src_center, src_size = world_bounds(source_obj)
tgt_center, tgt_size = world_bounds(target_obj)
log(f"source center={tuple(src_center)} size={tuple(src_size)}")
log(f"target center={tuple(tgt_center)} size={tuple(tgt_size)}")

# The two FBXs come from completely separate export paths (our Blender export
# vs Unreal's own conform-and-export) and were observed to NOT be the same
# scale -- e.g. one run measured source size=(0.256, 0.295, 0.400) vs target
# size=(0.436, 0.305, 0.484), a ~1.7x mismatch in X and ~1.2x in Z. A pure
# translation (matching centers only, no scale correction) assumes matching
# size; when they don't match, Selected-to-Active's radius-limited ray search
# either misses correspondences near the larger mesh's edges (holes) or --
# worse, on a left-right-symmetric surface like a face -- snaps onto the
# WRONG point across the midline, producing exactly the mirrored/kaleidoscope
# artifacts observed in the baked texture. Scale is corrected per-axis (not
# uniformly) since the mismatch isn't uniform across X/Y/Z above -- likely
# MetaHuman's conform reshapes proportions toward its own archetype, not just
# a scale/unit difference.
if tgt_size.x > 0 and tgt_size.y > 0 and tgt_size.z > 0:
    scale = mathutils.Vector((src_size.x / tgt_size.x, src_size.y / tgt_size.y, src_size.z / tgt_size.z))
    bpy.ops.object.select_all(action='DESELECT')
    target_obj.select_set(True)
    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.transform.resize(value=tuple(scale))
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    log(f"scaled target per-axis by {tuple(scale)} to match source's size")
    tgt_center, tgt_size = world_bounds(target_obj)  # recompute after scaling
    log(f"target size after scaling={tuple(tgt_size)}")

# Align target onto source so Selected-to-Active baking's ray search can
# actually find corresponding surface points.
offset = src_center - tgt_center
target_obj.location += offset
log(f"translated target by {tuple(offset)} to align with source")

# Recalculate target normals outward -- if these came in flipped, Selected-to-Active's
# ray cast (which follows the surface normal) would search the wrong direction and
# silently find nothing (black bake, no error).
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode='OBJECT')
log("recalculated target normals (outside)")

# Target originally had multiple material slots (MetaHuman's per-region setup: head
# skin, teeth, eyes, and several "M_Hide" slots for interior/hidden geometry). All of
# these share the SAME UV layout, often packed into overlapping/degenerate UV space
# for the non-visible regions -- baking them alongside the real head-skin faces
# corrupts the whole texture with garbage. Keep only the actual head-skin polygons
# (materials named M_GrayTexture_Head*) and delete the rest before baking.
head_material_names = {m.name for m in target_obj.data.materials if m and m.name.startswith("M_GrayTexture_Head")}
log(f"keeping only head-skin materials: {sorted(head_material_names)}")
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='DESELECT')
bpy.ops.object.mode_set(mode='OBJECT')
for poly in target_obj.data.polygons:
    mat = target_obj.data.materials[poly.material_index]
    poly.select = not (mat and mat.name in head_material_names)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.delete(type='FACE')
bpy.ops.object.mode_set(mode='OBJECT')
log(f"target verts after removing non-head-skin faces: {len(target_obj.data.vertices)}")

# --- Set up materials for baking ---
img_size = 4096
baked_image = bpy.data.images.new("baked_face_texture", width=img_size, height=img_size, alpha=False)

bake_mat = bpy.data.materials.new(name="BakeTarget")
bake_mat.use_nodes = True
nodes = bake_mat.node_tree.nodes
nodes.clear()
bsdf = nodes.new("ShaderNodeBsdfPrincipled")
output = nodes.new("ShaderNodeOutputMaterial")
bake_mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
tex_node = nodes.new("ShaderNodeTexImage")
tex_node.image = baked_image
nodes.active = tex_node  # bake writes to whichever image texture node is "active"

target_obj.data.materials.clear()
target_obj.data.materials.append(bake_mat)
for poly in target_obj.data.polygons:
    poly.material_index = 0

# --- Selection: Selected-to-Active bakes FROM selected objects TO the active object ---
bpy.ops.object.select_all(action='DESELECT')
source_obj.select_set(True)
target_obj.select_set(True)
bpy.context.view_layer.objects.active = target_obj

# --- Bake settings ---
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 16
scene.render.bake.use_selected_to_active = True
# Both were way too large relative to head size (~0.4m tall, eye-to-eye
# ~0.06-0.08m) -- max_ray_distance=0.25 is well over half the head's
# height, easily letting a ray searching near one eye cross the midline
# and sample the OTHER eye or an unrelated feature instead, producing
# ghosting/doubling artifacts around eyes and nose specifically (the most
# curved, symmetric regions, most sensitive to this). Tightened to values
# proportional to expected surface deviation between the scan and the
# conformed mesh, not the whole head's scale.
scene.render.bake.cage_extrusion = 0.03
scene.render.bake.max_ray_distance = 0.1
scene.cycles.bake_type = 'DIFFUSE'
scene.render.bake.use_pass_direct = False
scene.render.bake.use_pass_indirect = False
scene.render.bake.use_pass_color = True
scene.render.bake.margin = 32
if hasattr(scene.render.bake, "margin_type"):
    scene.render.bake.margin_type = 'ADJACENT_FACES'

log("baking (Selected to Active, DIFFUSE color-only pass)...")
bpy.ops.object.bake(type='DIFFUSE')
log("bake complete")

# Blender's bake margin only pads UV *island edges* by a few pixels -- it can't reach
# failed-ray-cast holes in the middle of an island (grazing-angle misses on
# cheeks/jaw/chin, well inside the face UV island). Fill those directly: for every
# near-black pixel, find the nearest non-black pixel and copy its color (a proper
# nearest-fill inpaint via distance transform -- NOT a blended/diffusion fill, which
# produces visible interference-pattern artifacts where wavefronts meet).
import numpy as np
from scipy.ndimage import distance_transform_edt

log("filling interior holes (nearest-fill inpaint)...")
w, h = baked_image.size
flat = np.empty(w * h * 4, dtype=np.float32)
baked_image.pixels.foreach_get(flat)
pixels = flat.reshape(h, w, 4)
rgb = pixels[:, :, :3]
hole_mask = (rgb.max(axis=2) < 0.02)
log(f"hole pixels: {int(hole_mask.sum())} / {w * h} ({100.0 * hole_mask.sum() / (w * h):.1f}%)")

_, indices = distance_transform_edt(hole_mask, return_indices=True)
filled_rgb = rgb[tuple(indices)]
rgb[hole_mask] = filled_rgb[hole_mask]

pixels[:, :, :3] = rgb
baked_image.pixels.foreach_set(pixels.ravel())

os.makedirs(os.path.dirname(OUTPUT_TEXTURE), exist_ok=True)
baked_image.filepath_raw = OUTPUT_TEXTURE
baked_image.file_format = 'PNG'
baked_image.save()
log(f"saved baked texture to {OUTPUT_TEXTURE}")
log("Done.")
