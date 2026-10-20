"""
الصور كجسيمات: تُقسَّم الصورة إلى قطع على شبكة صارمة، وكل قطعة تتصرف كما يتصرف الحرف
(تتفكك وتدور حول اليد، وتُمسك وتُسحب وتُرمى، وتعود لتكتمل الصورة عند الانسحاب).

TileCache يقدّم واجهة GlyphCache نفسها: get(id, وزن، حجم، لون، زاوية) → (سطح، ox، oy)
"""

import collections
import math
import time

import pygame
from PIL import Image

from . import config
from .layout import Glyph, Layout

TARGET_TILES = 150          # عدد القطع التقريبي لكل صورة


def build_image_layout(img: Image.Image, screen_w, screen_h):
    """توزيع قطع الصورة على الشبكة. يعيد (Layout، سطح الصورة المحجّمة، حجم القطعة)."""
    max_w, max_h = screen_w * 0.84, screen_h * 0.62
    scale = min(max_w / img.width, max_h / img.height)
    w0, h0 = img.width * scale, img.height * scale
    tile = int(math.sqrt(w0 * h0 / TARGET_TILES))
    tile = max(16, tile - tile % 2)                    # حجم زوجي → لا فواصل عند الاكتمال
    cols, rows = max(1, round(w0 / tile)), max(1, round(h0 / tile))
    w, h = cols * tile, rows * tile
    fitted = img.convert("RGB").resize((w, h), Image.LANCZOS)
    x0 = (screen_w - w) // 2
    y0 = int((screen_h - h) / 2 - screen_h * 0.045)
    glyphs = []
    for r in range(rows):
        for c in range(cols):
            glyphs.append(Glyph(r * cols + c, x0 + c * tile + tile / 2, y0 + r * tile + tile / 2, r, tile))
    baselines = [y0 + r * tile for r in range(rows + 1)]
    columns = [x0 + c * tile for c in range(cols + 1)]
    layout = Layout(glyphs, tile, (x0, y0, w, h), baselines, columns)
    surf = pygame.image.frombuffer(fitted.tobytes(), fitted.size, "RGB").convert()
    return layout, surf, (cols, rows)


ROTATION_STEP = 4


class TileCache:
    def __init__(self, image_surf, tile, grid, accent):
        self.image = image_surf
        self.tile = tile
        self.cols, self.rows = grid
        self.accent = accent
        self._base = {}
        self._cache = {}
        self._rot = {}
        self._queue = collections.deque()

    def _tile(self, idx):
        s = self._base.get(idx)
        if s is None:
            r, c = divmod(idx, self.cols)
            s = self.image.subsurface((c * self.tile, r * self.tile, self.tile, self.tile)).copy()
            self._base[idx] = s
        return s

    def _make(self, idx, scale_idx, color_idx):
        base = self._tile(idx)
        sc = config.SCALE_LEVELS[scale_idx]
        if sc != 1.0:
            n = max(1, int(round(self.tile * sc)))
            base = pygame.transform.smoothscale(base, (n, n))
        if color_idx:
            # القطعة المرشّحة أو الممسوكة: إضاءة وإطار بلون التمييز
            base = base.copy()
            base.fill((28, 28, 28), special_flags=pygame.BLEND_RGB_ADD)
            pygame.draw.rect(base, self.accent, base.get_rect(), max(2, base.get_width() // 18))
        return base

    def get(self, idx, weight_idx, scale_idx, color_idx=0, angle=0.0):
        q = int(round(angle / ROTATION_STEP))
        if q:
            key = (idx, scale_idx, color_idx, q)
            hit = self._rot.get(key)
            if hit is None:
                base, _, _ = self.get(idx, weight_idx, scale_idx, color_idx)
                surf = pygame.transform.rotozoom(base.convert_alpha(), q * ROTATION_STEP, 1.0)
                hit = (surf, 0.0, 0.0)
                if len(self._rot) > 8000:
                    self._rot.clear()
                self._rot[key] = hit
            return hit
        key = (idx, scale_idx, color_idx)
        hit = self._cache.get(key)
        if hit is None:
            hit = (self._make(idx, scale_idx, color_idx), 0.0, 0.0)
            self._cache[key] = hit
        return hit

    def warm(self, ids):
        ids = sorted(ids)
        for i in ids:
            self.get(i, 0, 0, 0)
        self._queue.clear()
        for si in range(1, len(config.SCALE_LEVELS)):
            for i in ids:
                self._queue.append((i, si, 0))
                self._queue.append((i, si, 1))

    def pump(self, budget_s=0.003):
        end = time.perf_counter() + budget_s
        q = self._queue
        while q and time.perf_counter() < end:
            idx, si, ci = q.popleft()
            key = (idx, si, ci)
            if key not in self._cache:
                self._cache[key] = (self._make(idx, si, ci), 0.0, 0.0)
