"""Area, triangle count and the area-split budget of the OBJECTS collection's meshes."""
import numpy as np

from ... import roles


def objects_collection(context):
    """The OBJECTS collection (picked in the Linking panel, else by its name), or None."""
    return roles.find(context.scene, 'OBJECTS')


def measure(obj, depsgraph):
    """(world-space area in m², triangles) of the mesh as shown - modifiers and GN included.
    An object outside the view layer has no evaluated mesh: its own mesh is measured."""
    ev = obj.evaluated_get(depsgraph)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        ev, me = None, obj.data
    try:
        me.calc_loop_triangles()
        n = len(me.loop_triangles)
        if not n:
            return 0.0, 0
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = np.array(obj.matrix_world, dtype=np.float64)
        co = co @ m[:3, :3].T + m[:3, 3]
        idx = np.empty(n * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", idx)
        t = co[idx].reshape(-1, 3, 3)
        cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        return float(np.linalg.norm(cross, axis=1).sum() * 0.5), n
    finally:
        if ev is not None:
            ev.to_mesh_clear()


def split(areas, budget, minimum):
    """Triangles per object: `budget` split by area, no object under `minimum`. The objects
    lifted to the minimum are taken out and the rest is split again among the others, so
    the total stays the budget (unless the minimums alone are over it)."""
    n = len(areas)
    out = [0] * n
    free = [i for i in range(n)]
    left = budget
    while free:
        total = sum(areas[i] for i in free)
        share = {i: (left * areas[i] / total if total > 0 else left / len(free)) for i in free}
        low = [i for i in free if share[i] < minimum]
        if not low:
            for i in free:
                out[i] = int(round(share[i]))
            # rounding leftovers go to the biggest, so the total is exactly the budget
            out[max(free, key=lambda i: areas[i])] += left - sum(out[i] for i in free)
            break
        for i in low:
            out[i] = minimum
            free.remove(i)
            left -= minimum
        left = max(left, 0)
    return out


def removable(budget, tris):
    """Fraction of the triangles that can go (0 when already within the budget)."""
    return max(0.0, 1.0 - budget / tris) if tris else 0.0
