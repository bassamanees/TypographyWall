"""
نظام الجسيمات: كل حرف جسيم له موضع أصلي في الشبكة وسرعة وموضع حالي.

القوى المؤثرة:
  - نابض يعيد الحرف إلى موضعه في الشبكة (قوي في السكون، ضعيف أثناء التفاعل)
  - نبض خفيف عند رصد شخص بعيد (Presence)
  - مجال مغناطيسي حول اليد: جذب نحو حلقة حول اليد + دوران
  - تنافر بين الحروف المتقاربة حتى تتوزع كسحابة حول اليد
  - الإمساك: إغلاق اليد يمسك أقرب الحروف فتتبعها، وفتحها يرميها بزخم الحركة
  - الظل: دفع الحروف خارج جسم المتلقي وجذبها نحو حافته فتتجمع حول ظله
  - اضطراب عضوي (Noise) وتخميد (Damping)
"""

import numpy as np

from . import config


class ParticleField:
    def __init__(self, glyphs, screen_size, entrance=True, spacing=40.0):
        self.screen_w, self.screen_h = screen_size
        self.spacing = spacing   # أقل مسافة مريحة بين حرفين أثناء التفاعل
        n = len(glyphs)
        self.chars = [g.ch for g in glyphs]
        self.widths = np.array([getattr(g,"adv",20) for g in glyphs],np.float32)
        self.word_units = np.array([isinstance(g.ch,str) and len(g.ch)>1 for g in glyphs])
        self.emph = np.array([1.0 if getattr(g, "emph", False) else 0.0 for g in glyphs], dtype=np.float32)
        self.home = np.array([[g.x, g.y] for g in glyphs], dtype=np.float32).reshape(n, 2)
        self.n = n
        rng = np.random.default_rng()
        if entrance:
            # الحروف الجديدة تدخل متناثرة ثم تتجمّع في الشبكة
            self.pos = self.home + rng.normal(0, 1, (n, 2)).astype(np.float32) * \
                np.array([self.screen_w * 0.35, self.screen_h * 0.35], dtype=np.float32)
        else:
            self.pos = self.home.copy()
        self.vel = np.zeros((n, 2), dtype=np.float32)
        self.phase = rng.uniform(0, 2 * np.pi, n).astype(np.float32)
        self.ring = rng.uniform(0.3, 1.0, n).astype(np.float32)  # مسافة كل حرف المفضّلة عن اليد
        self.spin = rng.choice([-1.0, 1.0], n).astype(np.float32)
        self.weight = np.full(n, config.REST_WEIGHT, dtype=np.float32)
        self.scale = np.ones(n, dtype=np.float32)
        self.lens_f = np.zeros(n, dtype=np.float32)   # قيمة العدسة المنعّمة لكل حرف
        self.lens_dx = np.zeros(n, dtype=np.float32)
        self.angle = np.zeros(n, dtype=np.float32)    # ميلان منعّم
        self.held = np.full(n, -1, dtype=np.int16)          # رقم اليد الممسكة بالحرف (-1 = حر)
        self.hold_off = np.zeros((n, 2), dtype=np.float32)   # موضع الحرف داخل القبضة
        self.rng = rng

    # ------------------------------------------------------------------
    # الإمساك والسحب
    # ------------------------------------------------------------------
    def catch(self, slot, hx, hy, radius, max_count):
        """إغلاق اليد: الإمساك بأقرب الحروف الحرّة داخل نصف القطر."""
        d = self.pos - np.array([hx, hy], dtype=np.float32)
        hit_dx = np.maximum(0,np.abs(d[:,0])-np.where(self.word_units,self.widths*.45,0))
        dist = np.hypot(hit_dx,d[:,1])
        free = (dist < radius) & (self.held < 0)
        idx = np.where(free)[0]
        if idx.size == 0:
            return 0
        idx = idx[np.argsort(dist[idx])][:max_count]
        self.held[idx] = slot
        if idx.size == 1:
            self.hold_off[idx] = d[idx] if self.word_units[idx[0]] else 0.0
        else:
            # عدة حروف تتكتّل داخل القبضة مع الحفاظ على ترتيبها النسبي
            off = d[idx] * 0.6
            lim = radius * 0.5
            n = np.sqrt((off ** 2).sum(1))[:, None] + 1e-6
            self.hold_off[idx] = np.where(n > lim, off / n * lim, off)
        return int(idx.size)

    def nearest_free(self, hx, hy, radius):
        """أقرب حرف حرّ داخل نصف القطر (للإضاءة قبل الإمساك)."""
        if self.n == 0:
            return None
        delta = self.pos-np.array([hx,hy],np.float32)
        dx = np.maximum(0,np.abs(delta[:,0])-np.where(self.word_units,self.widths*.45,0))
        d = dx**2+delta[:,1]**2
        d = np.where(self.held < 0, d, np.inf)
        i = int(np.argmin(d))
        return i if d[i] <= radius * radius else None

    def release(self, slot, vx=0.0, vy=0.0, fling=0.85):
        """فتح اليد: إفلات الحروف مع زخم حركة اليد (رمي)."""
        m = self.held == slot
        if not m.any():
            return 0
        self.held[m] = -1
        v = np.array([vx, vy], dtype=np.float32) * fling
        speed = float(np.linalg.norm(v))
        if not np.isfinite(speed):
            v[:] = 0
        elif speed > config.MAX_THROW_SPEED:
            v *= config.MAX_THROW_SPEED / speed
        self.vel[m] = v
        return int(m.sum())

    def release_all(self):
        self.held[:] = -1

    def scatter(self, strength=1.0):
        """دفعة التفكك/التناثر لحظة دخول المتلقي منطقة التفاعل."""
        ang = self.rng.uniform(0, 2 * np.pi, self.n)
        mag = self.rng.uniform(0.4, 1.0, self.n) * config.SCATTER_IMPULSE * strength
        self.vel += np.stack([np.cos(ang), np.sin(ang)], 1).astype(np.float32) * mag[:, None]

    def step(self, dt, t, activity, presence, attractors, silhouette, holds=None, extra_force=None):
        """attractors: [(x, y, k)] — k شدة المجال لكل يد. holds: {رقم اليد: (x, y)}."""
        if self.n == 0:
            return
        W = self.screen_w
        pos, vel = self.pos, self.vel

        # 1) العودة إلى الشبكة + نبض الحضور
        spring = config.SPRING_REST + (config.SPRING_ACTIVE - config.SPRING_REST) * activity
        pulse = presence * (1.0 - activity) * config.PULSE_AMPLITUDE
        target = self.home.copy()
        if pulse > 0.01:
            s = np.sin(t * config.PULSE_SPEED + self.phase)
            target[:, 1] += pulse * s
            target[:, 0] += pulse * 0.5 * np.cos(t * config.PULSE_SPEED * 0.7 + self.phase)
        force = spring * (target - pos)

        # 2) المجال المغناطيسي حول اليد
        if activity > 0.01 and attractors:
            R = config.MAGNET_RADIUS * W
            ring = self.ring * R * 0.45   # الحروف تقترب بما يكفي لالتقاطها
            for (ax, ay, k) in attractors:
                if k <= 0.01:
                    continue
                d = np.array([ax, ay], dtype=np.float32) - pos
                dist = np.sqrt((d ** 2).sum(1)) + 1e-3
                dirn = d / dist[:, None]
                fall = np.exp(-(dist / R) ** 2)
                radial = np.tanh((dist - ring) / (ring + 1.0))
                perp = np.stack([-dirn[:, 1], dirn[:, 0]], 1) * self.spin[:, None]
                force += activity * k * fall[:, None] * (
                    config.MAGNET_STRENGTH * radial[:, None] * dirn
                    + config.SWIRL_STRENGTH * perp)

        # تنافر الحروف المتقاربة → سحابة متوازنة بدل كتلة متكدّسة
        if activity > 0.01 and self.n > 1:
            # Same forces, bounded temporary memory (still O(n²) computation).
            for start in range(0, self.n, 128):
                stop = min(start + 128, self.n)
                d = pos[start:stop, None, :] - pos[None, :, :]
                dist = np.sqrt((d ** 2).sum(2)) + 1e-3
                push = np.clip(1.0 - dist / self.spacing, 0.0, None)
                push[np.arange(stop-start), np.arange(start, stop)] = 0.0
                force[start:stop] += activity * config.SEPARATION * ((push / dist)[:, :, None] * d).sum(1)

        # 3) الظل: تجمع الحروف حول جسم المتلقي (قوى متدرجة بلا عتبات حادة → بلا ارتجاف)
        if activity > 0.01 and silhouette is not None:
            mask, gx, gy = silhouette
            mh, mw = mask.shape
            ix = np.clip((pos[:, 0] / W * mw).astype(int), 0, mw - 1)
            iy = np.clip((pos[:, 1] / self.screen_h * mh).astype(int), 0, mh - 1)
            m = mask[iy, ix]
            g = np.stack([gx[iy, ix], gy[iy, ix]], 1)
            gn = g / (np.sqrt((g ** 2).sum(1))[:, None] + 1e-6)
            inside = np.clip((m - 0.35) / 0.35, 0, 1)
            inside = (inside * inside * (3 - 2 * inside))[:, None]          # smoothstep
            near = (np.clip(m * 2.5, 0, 1) * (1 - inside[:, 0]))[:, None]
            force += activity * (
                -config.SILHOUETTE_PUSH * gn * inside
                + config.SILHOUETTE_PULL * gn * near)

        # 4) انجراف عضوي ناعم (موجات بطيئة مختلفة لكل حرف — لا ضوضاء عشوائية في كل إطار)
        if activity > 0.01:
            ph = self.phase
            force[:, 0] += activity * config.NOISE * np.sin(t * 0.8 + ph * 3.1)
            force[:, 1] += activity * config.NOISE * np.cos(t * 0.65 + ph * 2.3)

        if extra_force is not None:
            force += extra_force
        vel += force * dt
        vel *= np.exp(-config.DAMPING * dt)
        speed = np.sqrt((vel ** 2).sum(1))
        cap = 2200.0
        over = speed > cap
        if over.any():
            vel[over] *= (cap / speed[over])[:, None]
        pos += vel * dt
        # الحروف الممسوكة تتبع القبضة مباشرة
        if holds and (self.held >= 0).any():
            follow = 1.0 - np.exp(-dt * config.GRAB_FOLLOW)
            for slot, (hx, hy) in holds.items():
                m = self.held == slot
                if not m.any():
                    continue
                target = self.hold_off[m] + np.array([hx, hy], dtype=np.float32)
                new = pos[m] + (target - pos[m]) * follow
                vel[m] = (new - pos[m]) / max(dt, 1e-3)
                pos[m] = new
            # يد اختفت عن الكاميرا → تُفلت حروفها
            lost = (self.held >= 0) & ~np.isin(self.held, list(holds.keys()))
            self.held[lost] = -1
        elif (self.held >= 0).any():
            self.held[:] = -1
        # حدود الشاشة
        np.clip(pos[:, 0], -50, W + 50, out=pos[:, 0])
        np.clip(pos[:, 1], -50, self.screen_h + 50, out=pos[:, 1])

    def lens(self, attractors, radius):
        """قيمة العدسة المكبّرة لكل حرف (0..1) واتجاه التباعد عن أقرب يد."""
        f = np.zeros(self.n, dtype=np.float32)
        dx = np.zeros(self.n, dtype=np.float32)
        for (ax, ay, *_) in attractors:
            ddx = self.pos[:, 0] - ax
            ddy = self.pos[:, 1] - ay
            fi = np.exp(-(ddx ** 2 + ddy ** 2) / (radius ** 2))
            better = fi > f
            f = np.where(better, fi, f)
            dx = np.where(better, ddx, dx)
        return f, dx
