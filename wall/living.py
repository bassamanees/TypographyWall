"""Living typography: short hand paths, ribbons, waves, and elastic words.

Coordinates are screen pixels. All histories are bounded and owned by one slide.
The camera code does not know about these effects.
"""
from collections import deque
from dataclasses import dataclass
import math
import numpy as np
from . import config


def sample_path(points, distances):
    """Sample a polyline backwards from its newest point by arc length."""
    p = np.asarray(points, dtype=np.float32)[::-1]
    if not len(p):
        return np.empty((0, 2), np.float32)
    if len(p) == 1:
        return np.repeat(p, len(distances), axis=0)
    lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
    arc = np.r_[0.0, np.cumsum(lengths)]
    keep = np.r_[True, np.diff(arc) > 1e-4]
    arc, p = arc[keep], p[keep]
    return np.column_stack([np.interp(distances, arc, p[:, axis]) for axis in (0, 1)]).astype(np.float32)


@dataclass
class Wave:
    x: float
    y: float
    born: float
    strength: float


class LivingMotion:
    def __init__(self):
        self.paths = {}
        self.waves = deque(maxlen=6)
        self.ribbon_ids = {}
        self.last_wave = {}
        self.last_motion = -1e9
        self.contour = None
        self._mask_id = None
        self._contour_at = -1e9

    def update(self, now, hands, field, enabled=True):
        """Observe only fresh hands; expire memories even when tracking stops."""
        active_slots = set()
        for h in hands if enabled else ():
            slot = h.slot
            active_slots.add(slot)
            path = self.paths.setdefault(slot, deque(maxlen=80))
            point = np.array([h.dx, h.dy])
            moved = not path or np.linalg.norm(point-np.array(path[-1][1:])) > 2.0
            if moved:
                path.append((now, h.dx, h.dy))
            if math.hypot(h.vx, h.vy) > 25:
                self.last_motion = now
            speed = math.hypot(h.vx, h.vy) / max(field.screen_w, 1)
            if not h.closed and speed > config.WAVE_SPEED and now-self.last_wave.get(slot, -1e9) > .25:
                self.waves.append(Wave(h.dx, h.dy, now, min(speed, 2.0)))
                self.last_wave[slot] = now
            held = np.flatnonzero(field.held == slot)
            if h.closed and held.size:
                if slot not in self.ribbon_ids:
                    d = np.linalg.norm(field.pos-point, axis=1)
                    candidates = np.flatnonzero((field.held < 0) & (d < field.screen_w*.3))
                    self.ribbon_ids[slot] = candidates[np.argsort(d[candidates])][:config.RIBBON_COUNT]
            else:
                self.ribbon_ids.pop(slot, None)
        for slot in list(self.paths):
            p = self.paths[slot]
            while p and now-p[0][0] > config.PATH_LIFETIME:
                p.popleft()
            if not p:
                self.paths.pop(slot)
            if slot not in active_slots:
                self.ribbon_ids.pop(slot, None)
                self.last_wave.pop(slot, None)
        while self.waves and now-self.waves[0].born > config.WAVE_LIFETIME:
            self.waves.popleft()

    def update_contour(self, silhouette, now, size):
        if silhouette is None:
            self.contour = None
            self._mask_id = None
            return
        mask = silhouette[0]
        if mask is self._mask_id or now-self._contour_at < .12:
            return
        import cv2
        outlines, _ = cv2.findContours((mask > .45).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        self._mask_id, self._contour_at = mask, now
        if not outlines:
            self.contour = None
            return
        line = max(outlines, key=cv2.contourArea).reshape(-1, 2)
        line = line[::max(1, len(line)//100)]
        self.contour = line.astype(np.float32) * np.array([size[0]/mask.shape[1], size[1]/mask.shape[0]])

    def forces(self, field, hands, now, gain, image=False):
        """Return acceleration; free items only. A still palm restores order."""
        force = np.zeros_like(field.pos)
        if field.n == 0 or gain < .01:
            return force
        width = field.screen_w
        for h in hands:
            p = np.array([h.dx, h.dy], np.float32)
            d = field.pos-p
            dist = np.linalg.norm(d, axis=1) + 1e-3
            fall = np.exp(-(dist/(width*.22))**2)
            speed = math.hypot(h.vx, h.vy)
            moving = np.clip(speed/120.0, 0, 1)
            if image:
                # Open palm separates tiles; closed palm gathers them locally.
                sign = -1.0 if h.closed else 1.0
                force += sign * d/dist[:,None] * fall[:,None] * config.IMAGE_FORCE * (1 if h.closed else moving)
                continue
            path = self.paths.get(h.slot)
            if path and len(path) > 2 and not h.closed and speed < width*config.WAVE_SPEED:
                curve = np.array([q[1:] for q in path], np.float32)
                delta = curve[None,:,:]-field.pos[:,None,:]
                nearest = np.argmin((delta**2).sum(2),axis=1)
                target = curve[nearest]
                force += (target-field.pos)*fall[:,None]*config.BRUSH_STRENGTH*moving
            ids = self.ribbon_ids.get(h.slot)
            if ids is not None and len(ids) and path and len(path)>2:
                curve = np.array([q[1:] for q in path],np.float32)
                length = np.linalg.norm(np.diff(curve,axis=0),axis=1).sum()
                # Don't pile followers up before a usable path has been drawn.
                spacing = max(22.0, min(field.spacing, 90.0))
                count = min(len(ids), int(length/spacing))
                ids = ids[:count]
                target = sample_path(curve, np.arange(1,count+1)*spacing)
                force[ids] += (target-field.pos[ids])*config.RIBBON_STRENGTH
        for wave in self.waves:
            age = now-wave.born
            d = field.pos-np.array([wave.x,wave.y])
            dist = np.linalg.norm(d,axis=1)+1e-3
            ring = np.exp(-((dist-age*width*.65)/(width*.045))**2)
            life = max(0,1-age/config.WAVE_LIFETIME)
            force += d/dist[:,None]*ring[:,None]*life*wave.strength*config.WAVE_FORCE
        if self.contour is not None and not any(h.closed for h in hands):
            d = self.contour[None,:,:]-field.pos[:,None,:]
            nearest = np.argmin((d**2).sum(2),axis=1)
            delta = self.contour[nearest]-field.pos
            # Gather around the silhouette mostly when the visitor is still.
            calm = np.clip((now-self.last_motion)/1.2,0,1)
            force += delta*config.CONTOUR_STRENGTH*calm
        force[field.held >= 0] = 0
        magnitude = np.linalg.norm(force,axis=1)+1e-6
        force *= np.minimum(1, config.MAX_EFFECT_FORCE/magnitude)[:,None]
        return force*gain


class ElasticWords:
    """Two pinches on opposite ends of ONE word; open palms never stretch."""
    def __init__(self):
        self.index = None
        self.slots = ()
        self.start_span = 1.0
        self.base_stretch = 1.0
        self.stretch = None
        self.center = None
        self.owner = None

    def update(self, field, hands, dt, enabled):
        if self.stretch is None or len(self.stretch) != field.n:
            self.stretch = np.ones(field.n,np.float32)
        target = np.ones(field.n,np.float32)
        fresh = {h.slot:h for h in hands}
        closed = [h for h in hands if h.closed]
        if self.index is not None:
            if not enabled or not all(s in fresh and fresh[s].closed for s in self.slots):
                # Let a remaining pinched hand retain the word without snapping.
                if self.index < field.n:
                    remaining = [fresh[s] for s in self.slots if s in fresh and fresh[s].closed]
                    if remaining and enabled:
                        h = remaining[0]
                        field.held[self.index] = h.slot
                        field.hold_off[self.index] = field.pos[self.index]-[h.dx,h.dy]
                        h.caught = 1
                    else:
                        field.held[self.index] = -1
                        field.vel[self.index] = 0
                self.index = None
                self.slots = ()
        if enabled and self.index is None and len(closed)==2:
            a,b = closed
            for owner, other in ((a,b),(b,a)):
                for i in np.flatnonzero(field.held==owner.slot):
                    if not isinstance(field.chars[i],str) or len(field.chars[i])<2:
                        continue
                    x,y = field.pos[i]
                    half = field.widths[i]*self.stretch[i]/2
                    owner_side = -1 if owner.dx < x else 1
                    opposite = x-owner_side*half
                    if abs(other.dx-opposite)<max(38,half*.45) and abs(other.dy-y)<65 and abs(other.dx-owner.dx)>40:
                        field.release(other.slot,fling=0)
                        self.index,self.slots = int(i),(owner.slot,other.slot)
                        self.start_span = max(40,abs(other.dx-owner.dx))
                        self.base_stretch = float(self.stretch[i])
                        other.caught = 1
                        break
                if self.index is not None:break
        self.center = None
        self.owner = None
        if self.index is not None:
            a,b = (fresh[s] for s in self.slots)
            target[self.index] = np.clip(self.base_stretch*abs(b.dx-a.dx)/self.start_span,1,config.MAX_WORD_STRETCH)
            self.center = ((a.dx+b.dx)/2,(a.dy+b.dy)/2)
            self.owner = self.slots[0]
            field.held[self.index] = self.owner
            field.hold_off[self.index] = 0
        self.stretch += (target-self.stretch)*(1-math.exp(-dt*10))
        self.stretch[np.abs(self.stretch-1)<.002] = 1
        return self.center
