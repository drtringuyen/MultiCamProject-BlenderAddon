"""Check Textures (the trash button next to the material Refresh in 06): every texture of
the add-on that is no longer valid, listed, and after a confirm removed from the .blend -
its file goes to the Recycle Bin when this .blend owns it (baking.owned):

  * images with the add-on's prefixes nothing uses any more (a removed object's, a work
    texture after Unlink Original / Remove Projection), "_stale" / ".001" copies and the
    old packed BA_ / BN_;
  * files in the bake folder this .blend wrote or used that no image points at;
  * optional: the work textures of a route an object does not use now (BAp_ / BNp_ on a
    From Original object, BAo_ / BNo_ on a From Projection one).

Outdated textures are never listed: they still show the last result and the next bake
overwrites the same file. Another .blend's files in a shared folder are never listed."""
import os
import re

import bpy

from . import common, owned, route

_COPY = re.compile(r"\.\d{3}$")
_WORK = (("ba_image", "BAo_", route.ORIGINAL), ("bn_image", "BNo_", route.ORIGINAL),
         ("bap_image", "BAp_", route.PROJECTION), ("bnp_image", "BNp_", route.PROJECTION))


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _ours(img):
    return (not img.library and img.name.startswith(common.OWN_PREFIXES + common.LEGACY_PREFIXES))


def _route_unused(scene):
    """[(object, attr, image)] work textures of a route the object does not use now."""
    out = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or o.library or common.data(o).handmade:
            continue
        r = route.get(o)
        for attr, _p, side in _WORK:
            img = getattr(common.data(o), attr)
            if img is not None and r != route.MIXED and r != side:
                out.append((o, attr, img))
    return out


def find(scene, route_unused=False):
    """(images [(image, why)], files [(path, why)]) no longer valid."""
    imgs = []
    for img in bpy.data.images:
        if not _ours(img):
            continue
        if img.name.startswith(common.LEGACY_PREFIXES):
            imgs.append((img, "old packed BA_/BN_"))
        elif img.name.endswith("_stale") or _COPY.search(img.name):
            imgs.append((img, "stale copy"))
        elif img.users == 0:
            imgs.append((img, "nothing uses it"))
    if route_unused:
        listed = {i for i, _w in imgs}
        for o, _attr, img in _route_unused(scene):
            if img not in listed:
                imgs.append((img, f"{o.name} is {route.LABELS[route.get(o)]}"))
                listed.add(img)
    gone = {i for i, _w in imgs}
    # files: in the bake folder, this .blend's own, no image (except those going) points at
    used = {_norm(common.image_file(i)) for i in bpy.data.images
            if i not in gone and common.image_file(i)}
    own = owned.names(scene)
    folder = common.output_dir(scene)
    files = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        names = []
    for f in names:
        p = os.path.join(folder, f)
        if (f.lower().endswith(".png") and f.startswith(common.OWN_PREFIXES)
                and os.path.isfile(p) and owned.is_own(scene, p, own) and _norm(p) not in used):
            files.append((p, "this .blend's, nothing uses it"))
    return imgs, files


def remove(scene, imgs, files):
    """Unlink and remove the images, files to the Recycle Bin. Returns (images, files) gone."""
    try:
        from ..export.fixes import to_recycle_bin
    except ImportError:
        to_recycle_bin = None
    for o in bpy.data.objects:
        if o.type != 'MESH' or o.library:
            continue
        d = common.data(o)
        for attr, _p, _s in _WORK:
            if getattr(d, attr) in imgs:
                setattr(d, attr, None)
                if attr == "ba_image":
                    d.ba_fingerprint, d.ba_size = "", 0
                elif attr == "bap_image":
                    d.bp_fingerprint, d.bp_size = "", 0
                elif attr == "bnp_image":
                    d.bnp_fingerprint = ""
    n = 0
    for img in imgs:
        try:
            bpy.data.images.remove(img)
            n += 1
        except ReferenceError:
            pass
    done = to_recycle_bin(files) if (files and to_recycle_bin) else []
    try:
        from . import cache
        cache.clear()
    except ImportError:
        pass
    return n, done


class MULTICAMPROJECT_OT_CheckTextures(bpy.types.Operator):
    """Check Textures: list the add-on's textures that are no longer valid - unused images,
    stale copies, old packed BA_/BN_, this .blend's files in the bake folder nothing uses -
    and, after you confirm, remove them (files to the Recycle Bin). Outdated textures and
    another .blend's files are never touched"""
    bl_idname = "multicamproject.check_textures"
    bl_label = "Check Textures"
    bl_options = {'REGISTER', 'UNDO'}

    route_unused: bpy.props.BoolProperty(
        name="Also the routes not in use",
        description="Also the work textures of a route an object does not use now (BAp_/BNp_ "
                    "of a From Original object, BAo_/BNo_ of a From Projection one) - "
                    "switching back bakes them again")

    def invoke(self, context, event):
        imgs, files = find(context.scene, True)
        if not imgs and not files:
            self.report({'INFO'}, "Textures are clean: nothing invalid")
            return {'FINISHED'}
        return context.window_manager.invoke_props_dialog(
            self, width=460, title="Remove textures that are no longer valid?",
            confirm_text="Remove")

    def draw(self, context):
        imgs, files = find(context.scene, self.route_unused)
        col = self.layout.column(align=True)
        col.prop(self, "route_unused")
        col.separator()
        if not imgs and not files:
            col.label(text="Nothing invalid (tick the box for the routes not in use)", icon='INFO')
        rows = [(i.name, w, 'IMAGE_DATA') for i, w in imgs]
        rows += [(os.path.basename(p), w, 'FILE_IMAGE') for p, w in files]
        for name, why, icon in rows[:24]:
            r = col.row()
            r.label(text=name, icon=icon)
            sub = r.row()
            sub.active = False
            sub.label(text=why)
        if len(rows) > 24:
            col.label(text=f"... and {len(rows) - 24} more")
        if files:
            col.separator()
            col.label(text="Files go to the Recycle Bin (Drive: its own bin)", icon='INFO')

    def execute(self, context):
        imgs, files = find(context.scene, self.route_unused)
        paths = [p for p, _w in files]
        # an image going may hold the file of the list: its file goes too when it is ours
        own = owned.names(context.scene)
        folder = _norm(common.output_dir(context.scene))
        for img, _w in imgs:
            p = common.image_file(img)
            if (p and os.path.isfile(p) and _norm(os.path.dirname(p)) == folder
                    and owned.is_own(context.scene, p, own) and p not in paths):
                still = [i for i in bpy.data.images if i != img and i not in {x for x, _ in imgs}
                         and common.image_file(i) and _norm(common.image_file(i)) == _norm(p)]
                if not still:
                    paths.append(p)
        n, done = remove(context.scene, [i for i, _w in imgs], paths)
        self.report({'INFO'}, f"Removed {n} image(s), {len(done)} file(s) to the Recycle Bin")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MULTICAMPROJECT_OT_CheckTextures)


def unregister():
    bpy.utils.unregister_class(MULTICAMPROJECT_OT_CheckTextures)
