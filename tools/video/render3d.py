"""
عارض ثلاثي الأبعاد بسيط لملفات STL (numpy + Pillow): تظليل لامبرت ورسم بترتيب العمق (painter's algorithm).
يكفي لنماذج العلبة (بضعة آلاف من المثلثات) بسرعة معقولة لكل إطار.
"""
import struct

import numpy as np
from PIL import Image, ImageDraw


def load_stl(path):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[80:84])[0]
    a = np.frombuffer(b[84:84 + n * 50], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    return a["v"].reshape(-1, 3, 3).astype(np.float64)


def rot(yaw=0.0, pitch=0.0, roll=0.0):
    """دوران بالدرجات: yaw حول z، ثم pitch حول x، ثم roll حول y."""
    y, p, r = np.radians([yaw, pitch, roll])
    rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    ry = np.array([[np.cos(r), 0, np.sin(r)], [0, 1, 0], [-np.sin(r), 0, np.cos(r)]])
    return rx @ rz @ ry


class Mesh:
    def __init__(self, tris, color, name=""):
        self.tris = tris
        self.color = np.array(color, float)
        self.name = name


def draw_meshes(img, items, view, scale, center, light=(0.35, -0.55, 0.75), outline=None, ambient=0.38):
    """
    items: [(Mesh, model_matrix 3x3, offset xyz mm, alpha)]
    view: مصفوفة دوران الكاميرا. scale: بكسل لكل مم. center: موضع مركز المشهد على الصورة.
    """
    L = np.array(light, float)
    L /= np.linalg.norm(L)
    polys = []
    for mesh, model, offset, alpha in items:
        if alpha <= 0.01:
            continue
        t = mesh.tris @ model.T + np.asarray(offset, float)
        t = t @ view.T
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        nrm = np.cross(e1, e2)
        ln = np.linalg.norm(nrm, axis=1)
        ok = ln > 1e-9
        nrm[ok] /= ln[ok, None]
        facing = nrm[:, 2] > 0          # نحو المشاهد (z نحو الشاشة)
        shade = ambient + (1 - ambient) * np.clip(nrm @ L, 0, 1)
        depth = t[:, :, 2].mean(1)
        sel = np.where(ok & facing)[0]
        for i in sel:
            polys.append((depth[i], t[i, :, :2], mesh.color * shade[i], alpha))
    polys.sort(key=lambda p: p[0])
    cx, cy = center
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _, xy, col, alpha in polys:
        pts = [(cx + x * scale, cy - y * scale) for x, y in xy]
        c = tuple(int(min(255, v)) for v in col) + (int(255 * alpha),)
        d.polygon(pts, fill=c, outline=outline or c)
    img.alpha_composite(layer)
    return img
