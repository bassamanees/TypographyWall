"""
مرشّح One Euro لتنعيم حركة اليد (Casiez et al., 2012).
يزيل الارتجاف عند الحركة البطيئة، ويبقى سريع الاستجابة عند الحركة السريعة.
"""

import math
from collections import deque


class OneEuro:
    def __init__(self, min_cutoff=1.2, beta=0.02, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x = None
        self.dx = 0.0
        self.raw = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        if self.x is None:
            self.x = self.raw = x
            return x
        dt = max(dt, 1e-3)
        dx = (x - self.raw) / dt
        self.raw = x
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        return self.x


class HandFilter:
    """يد واحدة: مرشّح لكل من x و y، مع حساب السرعة."""

    def __init__(self, min_cutoff, beta):
        self.fx = OneEuro(min_cutoff, beta)
        self.fy = OneEuro(min_cutoff, beta)
        self.open = None
        self.x = self.y = None
        self.dx = self.dy = 0.0   # الموضع المرسوم (يلحق بالقراءة المرشّحة)
        self.vx = self.vy = 0.0
        self.seen = 0.0
        self.grip = 0.0
        self.closed = False
        self.caught = 0
        self.slot = 0
        self.seen_n = 0      # عدد القراءات منذ ظهور اليد
        self.closed_at = 0.0
        self.v = 0.0         # درجة علامة V (مُنعَّمة)
        self._samples = deque(maxlen=8)
        self._sample_time = 0.0

    def update(self, x, y, openness, dt):
        px, py = self.x, self.y
        self.x = self.fx(x, dt)
        self.y = self.fy(y, dt)
        self._sample_time += dt
        self._samples.append((self._sample_time, self.x, self.y))
        while len(self._samples) > 2 and self._sample_time - self._samples[0][0] > 0.12:
            self._samples.popleft()
        if len(self._samples) >= 2:
            # Least-squares velocity over recent samples, independent of render FPS.
            mt = sum(s[0] for s in self._samples) / len(self._samples)
            den = sum((s[0] - mt) ** 2 for s in self._samples)
            if den > 1e-9:
                self.vx = sum((t - mt) * x for t, x, y in self._samples) / den
                self.vy = sum((t - mt) * y for t, x, y in self._samples) / den
        self.open = openness if self.open is None else self.open + (openness - self.open) * min(1.0, dt * 6)
        return self.x, self.y
