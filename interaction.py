"""Hand association, grabbing, and two-hand slide gestures."""
import math
import numpy as np
from . import config
from .filters import HandFilter
from .gestures import VSwipe

def _lerp(a, b, t):
    return a + (b-a)*t

class InteractionMixin:
    def _reset_hands(self):
        if hasattr(self, "field"):
            self.field.release_all()
        self.hand_filters.clear()
        if hasattr(self,"living"):
            from .living import LivingMotion, ElasticWords
            self.living,self.elastic = LivingMotion(),ElasticWords()
        if hasattr(self,"mouse"):
            self.mouse.anchor = None
        self.hand_screen = []
        self._holds = {}
        self._hover = set()
        self._span_hist.clear()
        self._gesture_intent = False
        if hasattr(self, "_vswipe"):
            self._vswipe.reset()
        self._hands_since = None
        self._hands_last = -1e9
        if hasattr(self, "_last_stamp"):
            del self._last_stamp

    def _update_hand_gain(self, dt):
        """
        بعد كل انتقال تبقى الشريحة ثابتة لحظة (SLIDE_HOLD) ثم يعود تأثير اليدين تدريجياً
        (SLIDE_RAMP)، وأثناء إيماءة الكفّين يخفت التأثير — فلا تختلط حركة التقليب باللعب بالحروف.
        """
        if self.transition or self._pending:
            target = 0.0
        else:
            since = self.t - self._settle_start
            target = float(np.clip((since - config.SLIDE_HOLD) / config.SLIDE_RAMP, 0.0, 1.0))
        if self._gesture_intent:
            target = min(target, 0.3)
        rate = 12.0 if target < self.hand_gain else 5.0
        self.hand_gain += (target - self.hand_gain) * min(1.0, dt * rate)

    def _update_hands(self, dt, track, W, H):
        """
        تتبّع كل يد عبر الزمن:
          - مطابقة القراءات الجديدة بأقرب يد سابقة (لا نعتمد على تسمية يمنى/يسرى لأنها تتقلّب)
          - مرشّح One Euro يُحدَّث فقط عند وصول قراءة جديدة من الكاميرا،
            ثم تلحق اليد المرسومة بها بسلاسة في كل إطار رسم
          - الإمساك بحرف واحد عند إغلاق اليد/القرص، والإفلات عند فتحها
        """
        seen = set()
        new_reading = track.stamp != getattr(self, "_last_stamp", None)
        if new_reading:
            sdt = float(np.clip(track.stamp - getattr(self, "_last_stamp", track.stamp - 1 / 30),
                                1 / 120, 0.25))
            self._last_stamp = track.stamp
            free = dict(self.hand_filters)
            max_d = config.HAND_MATCH_DISTANCE * W
            # (x، y، الانفتاح، الإمساك، حجم اليد) — الحجم يحدد قوة التنعيم (اليد البعيدة أصغر وأكثر ضجيجاً)
            dets = [(h_[0] * W, h_[1] * H, h_[2], h_[4], h_[5] if len(h_) > 5 else 0.1,
                     h_[6] if len(h_) > 6 else 0.0) for h_ in track.hands]
            # مطابقة جشِعة: أقرب زوج (قراءة، يد سابقة) أولاً
            pairs = sorted(((math.hypot(hf.x - dx, hf.y - dy), i, slot)
                            for i, (dx, dy, *_) in enumerate(dets)
                            for slot, hf in free.items()), key=lambda r: r[0])
            used = set()
            assign = {}
            for d, i, slot in pairs:
                if d > max_d or i in used or slot not in free:
                    continue
                assign[i] = free.pop(slot)
                used.add(i)
            # يد اختفت لحظة ثم ظهرت أبعد (حركة سريعة): تُعاد إلى هويتها نفسها
            # فتبقى قبضتها والحرف الذي تمسكه، بدل أن تُعدّ يداً جديدة
            for i, (dx, dy, *_) in enumerate(dets):
                if i in used:
                    continue
                lost = [(math.hypot(hf.x - dx, hf.y - dy), slot) for slot, hf in free.items()
                        if self.t - hf.seen > 0.05]
                if lost:
                    d, slot = min(lost)
                    if d < 0.6 * W:
                        assign[i] = free.pop(slot)
                        used.add(i)
            for i, (dx, dy, o, grip, hsize, vscore) in enumerate(dets):
                hf = assign.get(i)
                if hf is None:
                    hf = HandFilter(config.HAND_FILTER_MINCUTOFF, config.HAND_FILTER_BETA)
                    hf.slot = self._next_slot = getattr(self, "_next_slot", 0) + 1
                    hf.dx, hf.dy = dx, dy
                    hf.grip = grip
                    self.hand_filters[hf.slot] = hf
                # يد صغيرة (بعيدة) → تنعيم أقوى للموضع والإمساك
                small = float(np.clip(hsize / 0.08, 0.35, 1.0))
                hf.fx.min_cutoff = hf.fy.min_cutoff = config.HAND_FILTER_MINCUTOFF * small
                if hf.seen_n and self.t - hf.seen > config.HAND_FRESH_TIMEOUT:
                    hf._samples.clear()
                    hf.vx = hf.vy = 0.0
                hdt = float(np.clip(track.stamp-getattr(hf,"capture_stamp",track.stamp-sdt),1/120,.25))
                hf.capture_stamp = track.stamp
                hf.update(dx, dy, o, hdt)
                hf.grip += (grip - hf.grip) * 0.6 * max(small, 0.45)
                hf.v += (vscore - hf.v) * 0.6
                hf.seen = self.t
                hf.seen_n += 1
                seen.add(hf.slot)
        # يد اختفت مدة أطول من المهلة تُنسى وتُفلت حرفها (الومضات القصيرة لا تُفلته)
        for slot in [k for k, hf in self.hand_filters.items() if self.t - hf.seen > config.HAND_LOST_TIMEOUT]:
            self.field.release(slot, fling=0.0)
            del self.hand_filters[slot]

        k = min(1.0, dt * config.HAND_DISPLAY_RATE)
        # مدى الإمساك يكبر مع حجم الحروف (الكلمة الواحدة تُعرض بحروف ضخمة)
        W_r = config.GRAB_RADIUS * W
        if self.page["kind"] == "text":
            W_r = max(W_r, self.layout.font_size * 0.55)
        self._hover = set()
        for hf in self.hand_filters.values():
            # تنبؤ خفيف بالحركة يعوّض جزءاً من تأخير الكاميرا، ويمتد قليلاً أثناء فقدان لحظي
            # لليد فيكمل المؤشر انزلاقه بدل أن يتجمّد
            age = max(self.t - hf.seen, 0.0)
            fresh = age <= config.HAND_FRESH_TIMEOUT
            pred = config.HAND_PREDICTION if fresh else 0.0
            tx = float(np.clip(hf.x + hf.vx * pred, 0, W))
            ty = float(np.clip(hf.y + hf.vy * pred, 0, H))
            if not fresh:
                tx, ty = hf.dx, hf.dy
            hf.dx += (tx - hf.dx) * k
            hf.dy += (ty - hf.dy) * k
            can_grab = fresh and self.activity > 0.4 and self.transition is None and self.hand_gain > 0.9
            # الإمساك والإفلات بعتبتين مختلفتين (لا تذبذب عند الحد الفاصل)
            if fresh and not hf.closed and hf.grip > config.GRIP_CLOSE:
                hf.closed = True
                hf.closed_at = self.t
                hf.caught = self.field.catch(hf.slot, hf.dx, hf.dy, W_r, config.GRAB_MAX) if can_grab else 0
                self._grab_sound(hf.caught)
            elif fresh and hf.closed and hf.grip < config.GRIP_OPEN:
                hf.closed = False
                if self.field.release(hf.slot, hf.vx, hf.vy, config.FLING):
                    # صوت الرمي يعلو مع سرعة اليد
                    self.audio.play("release", float(np.clip(0.3 + math.hypot(hf.vx, hf.vy) / W, 0.3, 1.0)))
            elif hf.closed and can_grab and hf.caught == 0 and self.t - hf.closed_at < config.GRAB_GRACE:
                hf.caught = self.field.catch(hf.slot, hf.dx, hf.dy, W_r * 1.3, config.GRAB_MAX)
                self._grab_sound(hf.caught)
            # الحرف المرشّح للإمساك يُضاء تحت اليد المفتوحة
            if not hf.closed and can_grab:
                i = self.field.nearest_free(hf.dx, hf.dy, W_r)
                if i is not None:
                    self._hover.add(i)
        self.hand_screen = [(hf.dx, hf.dy, hf.open, hf.closed) for hf in self.hand_filters.values()]
        self._holds = {hf.slot: (hf.dx, hf.dy) for hf in self.hand_filters.values() if hf.closed}
        speed = max((math.hypot(hf.vx, hf.vy) for hf in self.hand_filters.values()), default=0.0)
        self.hand_speed = _lerp(self.hand_speed, speed, min(1.0, dt * 4))
        self.ripple = _lerp(self.ripple,
                            min(1.0, self.hand_speed / (W * 1.2)) * self.activity * 0.6 * self.hand_gain,
                            min(1.0, dt * 3))

        self._two_hand_gesture(W, observed=bool(seen))
        self._v_swipe_step(W, new_reading)

    def _grab_sound(self, caught):
        """نقرة قيثارة عند إمساك حرف، وطرقة حجر عند إمساك قطعة من صورة."""
        if caught:
            self.audio.play("grab_stone" if self.page["kind"] == "image" else "grab")

    def _v_swipe_step(self, W, new_reading):
        """
        علامة V (السبابة والوسطى) ثم سحب اليد أفقياً: يميناً = التالي، يساراً = السابق.
        تُحسب فقط عند وصول قراءة جديدة (لا تتكرر العينات بين قراءتين)، وتُحجب أثناء
        الإمساك والانتقال، ويخفت تأثير اليد على الحروف ما دامت العلامة قائمة.
        """
        vs = getattr(self, "_vswipe", None)
        if vs is None:
            vs = self._vswipe = VSwipe()
            self._v_ready_at = 0.0
        if not new_reading:
            if vs.active:
                self._gesture_intent = True
            return
        hands = [hf for hf in self.hand_filters.values()
                 if self.t - hf.seen <= config.HAND_FRESH_TIMEOUT and not hf.closed and hf.seen_n >= 3]
        allowed = not (self.transition or self._pending or self._holds) and self.activity >= 0.5
        ready_at = max(self._v_ready_at, self._gesture_ready_at)
        direction = vs.update(self.t, hands, W, allowed, ready_at)
        if vs.active:
            self._gesture_intent = True      # تخفيت تأثير اليد على الحروف أثناء العلامة
            self._span_hist.clear()
        if direction:
            self._v_ready_at = self.t + config.V_COOLDOWN
            self._last_gesture = (self.t, direction)
            if direction > 0:
                self.go_slide(self.slide + 1, "open")
            else:
                self.go_slide(self.slide - 1, "close")

    def _two_hand_gesture(self, W, observed=True):
        """
        الكفّان مفتوحتان معاً:
          تتباعدان بسرعة (اليسرى لليسار واليمنى لليمين) → الشريحة التالية
          تتقاربان بسرعة → الشريحة السابقة
        حركة الرجوع الطبيعية بعد الإيماءة (في الاتجاه المعاكس) تُتجاهل لفترة قصيرة.
        """
        hands = [hf for hf in self.hand_filters.values()
                 if self.t - hf.seen <= config.HAND_FRESH_TIMEOUT
                 and not hf.closed and hf.grip < config.GRIP_OPEN and hf.seen_n >= 4]
        v_active = getattr(self, "_vswipe", None) is not None and self._vswipe.active
        if len(hands) != 2 or v_active or self._holds or self.transition or self._pending or self.activity < 0.5:
            self._span_hist.clear()
            self._gesture_intent = False
            return
        if not observed:
            return
        L, R = sorted(hands, key=lambda h: h.x)
        # الكفّان تتباعدان/تتقاربان بسرعة → نية تقليب: يخفت تأثيرهما على الحروف
        self._gesture_intent = abs(R.vx - L.vx) > config.GESTURE_INTENT_SPEED * W
        span = R.x - L.x
        hist = self._span_hist
        if self.t < self._gesture_ready_at:
            # أثناء المهلة لا تُسجَّل الحركة أصلاً، فلا تُقبل إيماءة بدأت داخلها بعد انتهائها
            hist.clear()
            return
        hist.append((self.t, span, L.x, R.x))
        while hist and self.t - hist[0][0] > config.TWO_HAND_WINDOW:
            hist.popleft()
        if len(hist) < 3:
            return
        dist = config.TWO_HAND_DISTANCE * W
        each = config.TWO_HAND_EACH * W
        rate = R.vx - L.vx                         # موجب = تباعد، سالب = تقارب
        lo = min(hist, key=lambda r: r[1])
        hi = max(hist, key=lambda r: r[1])
        direction = 0
        if span - lo[1] > dist and L.x < lo[2] - each and R.x > lo[3] + each \
                and rate > config.TWO_HAND_MIN_SPEED * W:
            direction = +1
        elif hi[1] - span > dist and L.x > hi[2] + each and R.x < hi[3] - each \
                and rate < -config.TWO_HAND_MIN_SPEED * W:
            direction = -1
        if not direction:
            return
        last_t, last_dir = self._last_gesture
        if direction == -last_dir and self.t - last_t < config.TWO_HAND_REBOUND:
            return                                  # حركة الرجوع بعد إيماءة سابقة
        self._last_gesture = (self.t, direction)
        self._gesture_ready_at = self.t + config.TWO_HAND_COOLDOWN
        hist.clear()
        if direction > 0:
            self.go_slide(self.slide + 1, "open")
        else:
            self.go_slide(self.slide - 1, "close")

