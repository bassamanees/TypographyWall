"""إيماءة السحب بإصبعين (علامة V) لتقليب الشرائح.

v_sign_score : درجة 0..1 لشبه اليد بعلامة V (السبابة والوسطى ممدودتان ومتباعدتان،
               والبنصر والخنصر منثنيان). تُحسب بالنقاط الثلاثية الأبعاد ونسبةً إلى حجم الكف،
               فلا تتأثر بالمسافة عن الكاميرا ولا بدوران اليد.
VSwipe       : يراقب يداً واحدة بعلامة V ويُرجع +1 (التالي) أو -1 (السابق) عند سحبها أفقياً.
"""
import collections

import numpy as np

from . import config


def v_sign_score(pts, pw, ph):
    a = np.array([[q.x * pw, q.y * ph, q.z * pw] for q in pts], dtype=np.float32)
    wrist = a[0]
    lo, hi = config.OPENNESS_RANGE
    f = []
    for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20)):
        r = np.linalg.norm(a[tip] - wrist) / (np.linalg.norm(a[mcp] - wrist) + 1e-6)
        f.append(float(np.clip((r - lo) / (hi - lo), 0, 1)))
    palm = np.linalg.norm(a[9] - wrist) + 1e-6
    spread = float(np.linalg.norm(a[8] - a[12]) / palm)
    slo, shi = config.V_SPREAD_RANGE
    apart = float(np.clip((spread - slo) / (shi - slo), 0, 1))
    up = min(f[0], f[1])
    down = 1.0 - max(f[2], f[3])
    return min(up, down, apart)


class VSwipe:
    def __init__(self):
        self.hist = collections.deque()
        self.slot = None          # اليد التي تحمل العلامة حالياً
        self.t_on = 0.0
        self.last = (-1e9, 0)     # (زمن، اتجاه) آخر تقليب

    @property
    def active(self):
        return self.slot is not None

    def reset(self):
        self.hist.clear()
        self.slot = None

    def update(self, t, hands, W, allowed, ready_at=0.0):
        """hands: كائنات HandFilter الحديثة. يُنادى مرة لكل قراءة جديدة. يعيد +1 أو -1 أو 0."""
        cur = None
        if self.slot is not None:
            cur = next((h for h in hands if h.slot == self.slot), None)
            if cur is not None and cur.v < config.V_OFF:      # عتبة أدنى للبقاء (تخلّف)
                cur = None
        if cur is None:
            self.hist.clear()
            cands = [h for h in hands if h.v > config.V_ON]
            cur = cands[0] if len(cands) == 1 else None       # يدان بعلامة V = التباس، نتجاهل
            if cur is not None:
                self.t_on = t
        if cur is None or not allowed:
            self.reset()
            return 0
        self.slot = cur.slot
        if t < ready_at:
            self.hist.clear()
            return 0
        if t - self.t_on < config.V_HOLD:                      # تثبيت العلامة قبل احتساب الحركة
            return 0

        hist = self.hist
        hist.append((t, cur.x, cur.y))
        while hist and t - hist[0][0] > config.V_WINDOW:
            hist.popleft()
        if len(hist) < 3:
            return 0
        xs = [s[0 + 1] for s in hist]
        ys = [s[2] for s in hist]
        dist = config.V_DISTANCE * W
        speed = config.V_MIN_SPEED * W
        right, left = cur.x - min(xs), max(xs) - cur.x
        if right > dist and cur.vx > speed:
            direction, span = 1, right
        elif left > dist and cur.vx < -speed:
            direction, span = -1, left
        else:
            return 0
        if max(ys) - min(ys) > config.V_MAX_VERTICAL * span:   # الحركة يجب أن تكون أفقية
            return 0
        last_t, last_dir = self.last
        if direction == -last_dir and t - last_t < config.V_REBOUND:
            return 0                                           # رجوع اليد بعد التقليب
        self.last = (t, direction)
        hist.clear()
        return direction
