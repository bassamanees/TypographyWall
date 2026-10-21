"""
مجسّمات مبسّطة للقطع داخل العلبة بمقاسات محادثة التصميم (مم، بإحداثيات ملف القاعدة):
  البريدبورد 400: 55 × 82.5 × 8.5 — موزع الطاقة MB102 على طرفه السفلي — ESP32-CAM 27.5 × 48 في منتصفه،
  والعدسة على بعد 15.5 مم من طرف الكاميرا (عند y = 71)، وأعلى الكاميرا 12.4 مم فوق البريدبورد.
"""
import numpy as np

from render3d import Mesh


def box(x0, y0, z0, x1, y1, z1):
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v[np.array(f)]


def cylinder(cx, cy, z0, z1, r, n=20):
    a = np.linspace(0, 2 * np.pi, n + 1)
    tris = []
    for i in range(n):
        p0 = (cx + r * np.cos(a[i]), cy + r * np.sin(a[i]))
        p1 = (cx + r * np.cos(a[i + 1]), cy + r * np.sin(a[i + 1]))
        tris += [[(p0[0], p0[1], z0), (p1[0], p1[1], z0), (p1[0], p1[1], z1)],
                 [(p0[0], p0[1], z0), (p1[0], p1[1], z1), (p0[0], p0[1], z1)],
                 [(cx, cy, z1), (p0[0], p0[1], z1), (p1[0], p1[1], z1)],
                 [(cx, cy, z0), (p1[0], p1[1], z0), (p0[0], p0[1], z0)]]
    return np.array(tris, float)


def cylinder_y(cx, cz, y0, y1, r, n=16):
    """أسطوانة على محور y (للقابس والكابل)."""
    t = cylinder(0, 0, y0, y1, r, n)            # على z
    out = t.copy()
    out[..., 0] = t[..., 0] + cx
    out[..., 1] = t[..., 2]
    out[..., 2] = t[..., 1] + cz
    return out


def group(*parts):
    return np.concatenate(parts, axis=0)


Z_FLOOR = 3.0            # أرضية القاعدة (ظهر سماكته 3 مم)
BB = (7.5, 4.5, 62.5, 87.0)
BB_TOP = Z_FLOOR + 8.5


def breadboard():
    x0, y0, x1, y1 = BB
    body = Mesh(box(x0, y0, Z_FLOOR, x1, y1, BB_TOP), (238, 236, 228), "breadboard")
    groove = Mesh(box(34.0, y0 + 1, BB_TOP - 0.2, 36.0, y1 - 1, BB_TOP + 0.05), (170, 168, 160))
    rails = Mesh(group(box(x0 + 1.5, y0 + 2, BB_TOP - 0.1, x0 + 2.3, y1 - 2, BB_TOP + 0.05),
                       box(x1 - 2.3, y0 + 2, BB_TOP - 0.1, x1 - 1.5, y1 - 2, BB_TOP + 0.05)), (200, 60, 50))
    blue = Mesh(group(box(x0 + 3.2, y0 + 2, BB_TOP - 0.1, x0 + 4.0, y1 - 2, BB_TOP + 0.05),
                      box(x1 - 4.0, y0 + 2, BB_TOP - 0.1, x1 - 3.2, y1 - 2, BB_TOP + 0.05)), (60, 90, 200))
    return [body, groove, rails, blue]


MB_Z = BB_TOP + 3.5


def power_module():
    """MB102: لوحة حمراء على الطرف السفلي، مقبس DC أسود، USB فضي، زر، منظّم جهد."""
    pcb = Mesh(box(8.0, 4.5, MB_Z, 62.5, 34.2, MB_Z + 1.6), (180, 40, 40), "power")
    top = MB_Z + 1.6
    dc = Mesh(box(51.5, 4.0, top, 60.5, 18.0, top + 9.5), (25, 25, 28))
    usb = Mesh(box(16.5, 4.0, top, 24.5, 10.0, top + 3.2), (190, 192, 198))
    button = Mesh(group(box(42.5, 9.0, top, 48.5, 15.0, top + 5.0), cylinder(45.5, 12.0, top + 5.0, top + 7.0, 1.8)),
                  (240, 240, 235))
    reg = Mesh(box(28.0, 20.0, top, 38.0, 29.0, top + 4.5), (35, 35, 38))
    pins = Mesh(group(box(10.0, 29.5, BB_TOP, 60.5, 31.5, MB_Z)), (30, 30, 30))
    return [pcb, dc, usb, button, reg, pins]


ESP_Z = BB_TOP + 2.5


def esp32cam():
    x0, x1 = 35 - 13.75, 35 + 13.75
    y1 = 71 + 15.5
    y0 = y1 - 48.0
    top = ESP_Z + 1.6
    pcb = Mesh(box(x0, y0, ESP_Z, x1, y1, top), (30, 30, 34), "esp")
    shield = Mesh(box(x0 + 2.5, y0 + 4, top, x1 - 2.5, y0 + 22, top + 2.6), (196, 198, 204))
    cam = Mesh(box(35 - 4.3, 71 - 4.3, top, 35 + 4.3, 71 + 4.3, top + 3.4), (20, 20, 22))
    lens = Mesh(cylinder(35, 71, top + 3.4, BB_TOP + 12.4, 4.2), (45, 45, 50), "lens")
    glass = Mesh(cylinder(35, 71, BB_TOP + 12.4, BB_TOP + 12.5, 2.4), (70, 110, 160))
    led = Mesh(box(33.5, y0 + 25, top, 36.5, y0 + 28, top + 1.2), (250, 230, 150))
    pins = Mesh(group(box(x0 + 0.5, y0 + 2, BB_TOP, x0 + 3.0, y1 - 2, ESP_Z),
                      box(x1 - 3.0, y0 + 2, BB_TOP, x1 - 0.5, y1 - 2, ESP_Z)), (25, 25, 25))
    return [pcb, shield, cam, lens, glass, led, pins]


# ثقوب براغي الغطاء الأربعة (من المسقط العلوي للملف الحقيقي)
SCREW_HOLES = [(3.75, 80.5), (66.25, 80.5), (3.75, 52.0), (66.25, 52.0)]
SCREW_TOP = 29.8


def screw(x, y, z_head, turn=0.0):
    head = cylinder(x, y, z_head, z_head + 2.2, 2.7, 14)
    shaft = cylinder(x, y, z_head - 12, z_head, 1.4, 10)
    # شق الرأس يدور مع البرغي
    c, s = np.cos(turn), np.sin(turn)
    slot = box(-2.2, -0.35, 0, 2.2, 0.35, 0.3)
    slot[..., 0], slot[..., 1] = (slot[..., 0] * c - slot[..., 1] * s + x,
                                  slot[..., 0] * s + slot[..., 1] * c + y)
    slot[..., 2] += z_head + 2.2
    return [Mesh(group(head, shaft), (170, 172, 178)), Mesh(slot, (60, 60, 64))]


DC_X, DC_Z = 56.0, MB_Z + 1.6 + 4.8


def dc_plug(y_tip):
    """قابس الـ DC وكابله يدخلان من الجدار السفلي (y < 0) نحو المقبس."""
    plug = cylinder_y(DC_X, DC_Z, y_tip - 14, y_tip, 3.6)
    tip = cylinder_y(DC_X, DC_Z, y_tip, y_tip + 6, 2.6)
    cable = cylinder_y(DC_X, DC_Z, y_tip - 80, y_tip - 14, 1.8)
    return [Mesh(group(plug, cable), (28, 28, 30)), Mesh(tip, (190, 190, 196))]


# مواضع للتسميات (مم)
ANCHORS = {
    "lens": (35.0, 71.0, 28.6),
    "button": (45.5, 12.0, 31.0),
    "inscription": (35.0, 44.0, 30.5),
    "vents": (70.0, 29.5, 20.5),
    "openings": (38.0, 0.0, 16.0),
    "ear_top": (35.0, 104.0, 3.0),
    "ear_bottom": (35.0, -12.0, 3.0),
    "breadboard": (12.0, 60.0, BB_TOP),
    "power": (14.0, 22.0, MB_Z + 1.6),
    "esp": (35.0, 50.0, ESP_Z + 4.2),
}
