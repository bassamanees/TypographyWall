"""Hand-only camera tracking. Capture and one VIDEO hand detector run independently.
Legacy pose helpers are retained but are never started by CameraTracker.
"""

import math
import logging
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from . import config
from .gestures import v_sign_score


@dataclass
class TrackState:
    ok: bool = False
    person: bool = False
    proximity: float = 0.0
    hands: list = field(default_factory=list)
    silhouette: Optional[tuple] = None
    frame: Optional[np.ndarray] = None
    fps: float = 0.0
    error: Optional[str] = None
    stamp: float = 0.0
    latency_ms: float = 0.0
    cam_fps: float = 0.0
    pose_fps: float = 0.0
    far: bool = False          # اليدان مكتشفتان بطريقة المسافة البعيدة


def _silhouette_from_mask(mask: np.ndarray, prev=None):
    small = mask.astype(np.float32)
    if small.shape[::-1] != tuple(config.MASK_SIZE):
        small = cv2.resize(small, config.MASK_SIZE, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 3.0)
    if prev is not None:
        # تنعيم زمني: حافة الظل لا ترتجف من إطار لآخر
        small = prev + (small - prev) * config.MASK_SMOOTHING
    gy, gx = np.gradient(small)
    return small, gx.astype(np.float32), gy.astype(np.float32)


def hand_shape(p, pw, ph):
    """
    شكل اليد من نقاط MediaPipe (بالأبعاد الثلاثة، فلا يتأثر كثيراً بدوران اليد):
      openness : 0 كل الأصابع منثنية (قبضة) … 1 كلها ممدودة (كف مفتوحة)
      grip     : 0 مفتوحة … 1 ممسكة — أقصى قيمتي القبضة والقرص (إبهام + سبابة)
    """
    pts = np.array([[q.x * pw, q.y * ph, q.z * pw] for q in p], dtype=np.float32)
    wrist = pts[0]
    ext = []
    for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20)):
        ext.append(np.linalg.norm(pts[tip] - wrist) / (np.linalg.norm(pts[mcp] - wrist) + 1e-6))
    lo, hi = config.OPENNESS_RANGE
    per_finger = np.clip((np.array(ext) - lo) / (hi - lo), 0, 1)
    openness = float(per_finger.mean())
    palm = np.linalg.norm(pts[9] - wrist) + 1e-6
    pinch_d = np.linalg.norm(pts[4] - pts[8]) / palm
    plo, phi = config.PINCH_RANGE
    pinch = float(np.clip((phi - pinch_d) / (phi - plo), 0, 1))
    fist = float(np.clip(1.0 - per_finger[:3].mean() * 1.15, 0, 1))  # البنصر يُهمل غالباً
    return openness, max(fist, pinch)


_GAMMA_LUTS = {}


def brighten(frame):
    """
    تفتيح تلقائي للإطارات المعتمة قبل كشف اليد (كاميرات الحاسوب ضعيفة في الإضاءة الخافتة).
    تصحيح جاما عبر جدول بحث — سريع جداً ولا يُطبّق إن كانت الإضاءة كافية.
    """
    if not config.AUTO_BRIGHTEN:
        return frame
    mean = float(frame[::8, ::8].mean())
    if mean >= config.BRIGHTEN_BELOW:
        return frame
    gamma = np.log(config.BRIGHTEN_TARGET / 255.0) / np.log(max(mean, 8.0) / 255.0)
    gamma = round(float(np.clip(gamma, 0.4, 1.0)), 2)
    lut = _GAMMA_LUTS.get(gamma)
    if lut is None:
        lut = _GAMMA_LUTS[gamma] = (255.0 * (np.arange(256) / 255.0) ** gamma).astype(np.uint8)
    return cv2.LUT(frame, lut)


def map_to_box(x, y, box=None):
    """منطقة التفاعل: الجزء الأوسط من الصورة يُمدّ ليغطي الشاشة كلها
    حتى لا يضطر المتلقي لمدّ يده إلى حافة إطار الكاميرا."""
    x0, y0, x1, y1 = box or config.INTERACTION_BOX
    return (float(np.clip((x - x0) / (x1 - x0), 0, 1)),
            float(np.clip((y - y0) / (y1 - y0), 0, 1)))


def warp_region(img, x0, y0, w, h, out_w, out_h):
    """قصّ منطقة (بالبكسل، قد تتجاوز حدود الصورة) وتحجيمها في عملية واحدة."""
    m = np.array([[w / out_w, 0, x0], [0, h / out_h, y0]], dtype=np.float32)
    return cv2.warpAffine(img, m, (out_w, out_h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _hand_box(p, w, h):
    """مركز اليد وحجمها بالبكسل من نقاطها."""
    xs = [q.x * w for q in p]
    ys = [q.y * h for q in p]
    return ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, max(max(xs) - min(xs), max(ys) - min(ys), 1.0))


class _P:
    """نقطة يد بعد إعادتها من القصاصة إلى إحداثيات الصورة الكاملة."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # الكاميرا على شبكة محلية: لا وكيل


class MjpegStream:
    """
    قارئ بث MJPEG عبر HTTP (ESP32-CAM) بتأخير أدنى، بواجهة تشبه cv2.VideoCapture.

    خيط يقرأ الشبكة بأقصى سرعة ويحتفظ بأحدث إطار JPEG كاملٍ فقط؛ وما تأخّر يُتجاهل.
    بدل ذلك يخزّن FFmpeg الإطارات فيتراكم التأخير كلما تباطأت المعالجة لحظةً.
    يعيد الاتصال تلقائياً إن انقطع البث.
    """

    max_fails = 200          # ~60 ثانية بلا إطار قبل الاستسلام: وقت كافٍ لإعادة وصل الواي فاي

    def __init__(self, url, timeout=5.0):
        self.url = url
        self.timeout = timeout
        self._cond = threading.Condition()
        self._jpeg = None
        self._id = 0
        self._seen = 0
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        with self._cond:      # انتظر أول إطار (أو فشل الاتصال)
            self._cond.wait_for(lambda: self._jpeg is not None or not self._running, timeout)

    def isOpened(self):
        return self._jpeg is not None

    def set(self, *_):
        return False          # الدقة والمعدل يُضبطان من برنامج الـESP

    def release(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()

    def read(self):
        with self._cond:
            ready = self._cond.wait_for(lambda: self._id != self._seen or not self._running, 0.3)
            if not ready or not self._running:
                return False, None
            data, self._seen = self._jpeg, self._id
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return frame is not None, frame

    def _publish(self, data):
        with self._cond:
            self._jpeg = data
            self._id += 1
            self._cond.notify_all()

    def _run(self):
        while self._running:
            try:
                req = urllib.request.Request(self.url, headers={"Connection": "close"})
                with _NO_PROXY.open(req, timeout=self.timeout) as resp:
                    buf = bytearray()
                    while self._running:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        buf += chunk
                        end = buf.rfind(b"\xff\xd9")           # آخر نهاية JPEG كاملة
                        if end < 0:
                            if len(buf) > 2_000_000:
                                buf.clear()
                            continue
                        start = buf.rfind(b"\xff\xd8", 0, end)
                        if start >= 0:
                            self._publish(bytes(buf[start:end + 2]))   # أحدث إطار فقط
                        del buf[:end + 2]
            except Exception:
                pass
            if self._running:
                if "//192.168.4.1" in self.url and self._running:
                    # اتصال مباشر بشبكة الكاميرا: ويندوز قد يقفز لشبكة فيها إنترنت، فنعيده إليها
                    try:
                        from .esp import keep_on_camera_wifi
                        keep_on_camera_wifi(still_wanted=lambda: self._running)
                    except Exception:
                        pass
                time.sleep(0.5)       # إعادة الاتصال
        with self._cond:
            self._cond.notify_all()


class FrameGrabber:
    """
    يقرأ الكاميرا باستمرار ويحتفظ بأحدث إطار فقط.
    بدونه تتكدّس الإطارات في ذاكرة الكاميرا فيعمل التتبع على صور قديمة (تأخير متزايد).
    """

    def __init__(self, cap):
        self.cap = cap
        self.frame = None
        self.stamp = 0.0
        self.frame_id = 0
        self.fps = 0.0
        self.error = None
        self._cond = threading.Condition()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._capture_loop()
        except Exception as exc:
            logging.getLogger(__name__).exception("Camera capture failed")
            with self._cond:
                self.error = f"Camera capture failed: {exc}"
                self._cond.notify_all()

    def _capture_loop(self):
        fails, last = 0, time.monotonic()
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                fails += 1
                if fails > getattr(self.cap, "max_fails", 60):
                    self.error = "انقطع بث الكاميرا"
                    with self._cond:
                        self._cond.notify_all()
                    return
                time.sleep(0.01)
                continue
            fails = 0
            if config.MIRROR:
                frame = cv2.flip(frame, 1)
            now = time.monotonic()
            self.fps = 0.9 * self.fps + 0.1 / max(now - last, 1e-3)
            last = now
            with self._cond:
                self.frame, self.stamp = frame, now
                self.frame_id += 1
                self._cond.notify_all()

    def wait_newer(self, last_id, timeout=0.5):
        """ينتظر إطاراً أحدث من last_id ويعيد (id، الإطار، زمن الالتقاط)."""
        with self._cond:
            if self.frame_id <= last_id and self.error is None:
                self._cond.wait(timeout)
            return self.frame_id, self.frame, self.stamp

    def stop(self):
        self._running = False
        self._thread.join(timeout=1.0)


class CameraTracker:
    """
    ثلاثة خيوط مستقلة:
      الكاميرا  → أحدث إطار دائماً
      اليد      → MediaPipe Hands بالنموذج الكامل على الإطار كاملاً، بأسرع ما يمكن
                  + كل ذراع مرفوعة لا يراها الكشف العادي (يد صغيرة/بعيدة) لها متتبّع خاص
                    على قصاصة مكبّرة بالدقة الكاملة تتبع اليد (كما في MediaPipe Holistic)
                  + استعادة اليد المفقودة لحظياً حين لا يُرى الجسم
      الجسم     → MediaPipe Pose (النموذج الكامل، الدقة الكاملة) + قناع الظل، بإيقاعه الخاص
    """

    def __init__(self, camera_index=config.CAMERA_INDEX):
        self.camera_index = camera_index
        self._lock = threading.Lock()
        self._hands = {"hands": [], "stamp": 0.0, "fps": 0.0, "frame": None, "ok": False, "far": False}
        self._pose = {"person": False, "proximity": 0.0, "silhouette": None, "stamp": 0.0, "fps": 0.0,
                      "arms": {}, "shoulder_w": 0.0}
        self._error = None
        self._running = False
        self._threads = []
        self.grabber = None
        self._box = tuple(config.INTERACTION_BOX)   # منطقة التفاعل الفعلية (تتّسع حركتها حين يبتعد المتلقي)
        self.want_preview = False      # تُرسم معاينة الكاميرا فقط حين تكون ظاهرة (توفير للمعالج)

    # ------------------------------------------------------------------
    def start(self):
        self._running = True
        t = threading.Thread(target=self._worker, args=(self._boot,), daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._running = False
        for t in list(self._threads):
            t.join(timeout=2.0)
        if self.grabber:
            self.grabber.stop()
            self.grabber.cap.release()

    def _optional_worker(self, target, *args):
        """رصد الجسم إضافة لليد البعيدة: إن فشل (مثلاً ذاكرة قليلة) يستمر التتبع العادي بدونه."""
        try:
            target(*args)
        except Exception:
            logging.getLogger(__name__).exception("Body tracking failed; far-hand detection disabled")
            config.FAR_HANDS = False

    def _worker(self, target, *args):
        try:
            target(*args)
        except Exception as exc:
            logging.getLogger(__name__).exception("Tracking worker failed")
            self._error = f"Tracking failed: {exc}"
            self._running = False

    def snapshot(self) -> TrackState:
        with self._lock:
            h, p = dict(self._hands), dict(self._pose)
        error = self._error or (self.grabber.error if self.grabber else None)
        now = time.monotonic()
        person = p["person"] and now - p["stamp"] < 1.0
        proximity = p["proximity"] if person else 0.0
        fresh = now - h["stamp"] < config.TRACK_STALE_TIMEOUT
        if not fresh:
            h["hands"] = []
        if h["hands"] and not person:
            # يد قريبة من الكاميرا دون رؤية الكتفين = المتلقي قريب
            person, proximity = True, 0.5
        return TrackState(ok=h["ok"] and fresh, person=person, proximity=proximity, hands=h["hands"],
                          silhouette=p["silhouette"] if person and now - p["stamp"] < 1.0 else None, frame=h["frame"],
                          fps=h["fps"], error=error, stamp=h["stamp"],
                          latency_ms=max(0.0, now-h["stamp"])*1000 if h["stamp"] else 0.0,
                          cam_fps=self.grabber.fps if self.grabber else 0.0, pose_fps=p["fps"],
                          far=h["far"])

    # ------------------------------------------------------------------
    def _boot(self):
        try:
            from . import landmarkers as detectors
        except Exception as e:  # pragma: no cover
            self._error = f"تعذّر تحميل MediaPipe: {e}"
            return
        is_url = isinstance(self.camera_index, str)
        if is_url and self.camera_index.startswith("usb:"):
            # ESP32-CAM عبر كابل USB (بلا واي فاي): "usb:COM5"
            from .usbcam import SerialJpegStream
            cap = SerialJpegStream(self.camera_index[4:])
            if not cap.isOpened():
                cap.release()
                self._error = cap.error or f"تعذّر استقبال الصورة من الكاميرا عبر {self.camera_index[4:]}"
                return
        elif is_url:
            # كاميرا شبكية (ESP32-CAM): رابط بث MJPEG مثل http://192.168.1.50:81/stream
            cap = MjpegStream(self.camera_index)
            if not cap.isOpened():
                cap.release()
                self._error = f"تعذّر الاتصال بالكاميرا الشبكية: {self.camera_index}"
                return
        else:
            backend = (cv2.CAP_V4L2 if sys.platform.startswith("linux") else
                       cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
            cap = cv2.VideoCapture(self.camera_index, backend)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                cap.release()
                self._error = "تعذّر فتح الكاميرا — تحقق من إذن الكاميرا في إعدادات النظام"
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # الدقة والمعدل في الكاميرا الشبكية تُضبط من برنامج الـESP
        if not self._running:
            cap.release()
            return
        self.grabber = FrameGrabber(cap)
        loops = [(self._worker, self._hand_loop)]
        if config.FAR_HANDS:
            loops.append((self._optional_worker, self._pose_loop))
        for runner, target in loops:
            t = threading.Thread(target=runner, args=(target, detectors), daemon=True)
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------
    # اليد
    # ------------------------------------------------------------------
    def _hand_loop(self, detectors):
        """
        كشف اليد في الصورة كاملة بأحدث إطار. وإن رفع المتلقي ذراعه ولم تُرَ يده (بعيد = يد صغيرة)
        نقصّ مربعاً حولها بموضع الرسغ من رصد الجسم ونكشف فيه (anchored crops).
        """
        last_id, fps, last_t = 0, 0.0, time.monotonic()
        far_detector = None      # يُحمَّل عند أول حاجة فقط (توفير الذاكرة حين يكون المتلقي قريباً)
        with detectors.HandsTask(max_num_hands=2,
                min_detection_confidence=config.HAND_DETECTION_CONFIDENCE,
                min_tracking_confidence=config.HAND_TRACKING_CONFIDENCE) as detector:
            while self._running:
                started = time.monotonic()
                fid, frame, stamp = self.grabber.wait_newer(last_id)
                if self.grabber.error:
                    break
                if frame is None or fid == last_id:
                    continue
                last_id = fid
                h, w = frame.shape[:2]
                if w > config.CAMERA_WIDTH:
                    frame = cv2.resize(frame, (config.CAMERA_WIDTH,
                        round(h * config.CAMERA_WIDTH / w)), interpolation=cv2.INTER_AREA)
                    h, w = frame.shape[:2]
                rgb = cv2.cvtColor(brighten(frame), cv2.COLOR_BGR2RGB)
                result = detector.process(rgb)
                hand_list, pts_all = [], []
                for i, hand in enumerate(result.multi_hand_landmarks):
                    pts = hand.landmark
                    openness, grip = hand_shape(pts, w, h)
                    cx = (pts[4].x + pts[8].x) * .35 + pts[5].x * .3
                    cy = (pts[4].y + pts[8].y) * .35 + pts[5].y * .3
                    bx, by = map_to_box(cx, cy)
                    label = result.multi_handedness[i].classification[0].label
                    size = _hand_box(pts, w, h)[2] / w
                    v = v_sign_score(pts, w, h)
                    hand_list.append((bx, by, openness, label, grip, size, v))
                    pts_all.append((pts, cx, cy, grip))
                rois = self._far_rois(pts_all, w, h) if config.FAR_HANDS else []
                if rois and far_detector is None:
                    try:
                        far_detector = detectors.HandsTask(
                            max_num_hands=2, static_image_mode=True,
                            min_detection_confidence=config.FAR_HAND_DETECTION_CONFIDENCE)
                    except Exception:
                        logging.getLogger(__name__).exception("Far-hand detector unavailable")
                        config.FAR_HANDS = False
                        rois = []
                if rois:
                    for pts, key in self._detect_in_rois(far_detector, frame, rois, w, h):
                        openness, grip = hand_shape(pts, w, h)
                        cx = (pts[4].x + pts[8].x) * .35 + pts[5].x * .3
                        cy = (pts[4].y + pts[8].y) * .35 + pts[5].y * .3
                        bx, by = map_to_box(cx, cy)
                        size = _hand_box(pts, w, h)[2] / w
                        hand_list.append((bx, by, openness, key, grip, size, v_sign_score(pts, w, h)))
                        pts_all.append((pts, cx, cy, grip))
                now = time.monotonic()
                fps = .9 * fps + .1 / max(now-last_t, .001)
                last_t = now
                thumb = self._thumbnail(frame, pts_all, rois, config.INTERACTION_BOX) if self.want_preview else None
                with self._lock:
                    self._hands = {"hands": hand_list, "stamp": stamp, "fps": fps,
                                   "frame": thumb, "ok": True, "far": bool(rois)}
            if far_detector is not None:
                far_detector.close()
                # Leave CPU time for rendering; capture continues independently.
                delay = 1.0 / config.HAND_MAX_FPS - (time.monotonic()-started)
                if delay > 0:
                    time.sleep(delay)

    def _far_rois(self, found, w, h):
        """
        قصاصات لليد البعيدة: لكل ذراع مرفوعة يراها رصد الجسم ولا توجد قرب رسغها يد مكتشفة.
        حجم القصاصة من عرض الكتفين (اليد ≈ نصف عرض الكتفين) مع هامش للحركة.
        """
        with self._lock:
            pose = dict(self._pose)
        if not pose.get("person") or time.monotonic() - pose.get("stamp", 0) > 0.5:
            return []
        sw = pose.get("shoulder_w", 0.0)
        if sw <= 0:
            return []
        size = float(np.clip(config.HAND_CROP_SCALE * 0.55 * sw * w, 56, 0.6 * min(w, h)))
        rois = []
        for side, arm in pose.get("arms", {}).items():
            if not arm.get("raised") or arm.get("vis", 0) < 0.35:
                continue
            (wx, wy), (ix, iy) = arm["wrist"], arm["index"]
            cx, cy = (wx + 0.6 * (ix - wx)) * w, (wy + 0.6 * (iy - wy)) * h
            if any(math.hypot(fx * w - cx, fy * h - cy) < size * 0.8 for _, fx, fy, _ in found):
                continue                     # اليد مكتشفة أصلاً في الصورة كاملة
            rois.append((side, cx, cy, size))
        return rois

    @staticmethod
    def _detect_in_rois(hands, img, rois, w, h):
        """
        قصاصات مربعة مكبّرة (key, cx, cy, size بالبكسل) تُجمع أفقياً في صورة واحدة،
        يُكشف فيها مرة واحدة، ثم تُعاد النقاط إلى إحداثيات الصورة الكاملة.
        """
        T = config.HAND_CROP
        tiles = [warp_region(img, cx - s / 2, cy - s / 2, s, s, T, T) for (_, cx, cy, s) in rois]
        mosaic = cv2.cvtColor(np.hstack(tiles), cv2.COLOR_BGR2RGB)
        mosaic.flags.writeable = False
        res = hands.process(mosaic)
        out, used = [], set()
        n = len(rois)
        for hl in (res.multi_hand_landmarks or []):
            lm = hl.landmark
            k = int(np.clip(np.mean([q.x for q in lm]) * n, 0, n - 1))
            if k in used:
                continue                                   # يد مكررة في القصاصة نفسها
            key, cx, cy, s = rois[k]
            x0, y0 = cx - s / 2, cy - s / 2
            out.append(([_P((x0 + (q.x * n - k) * s) / w, (y0 + q.y * s) / h, q.z * n * s / w) for q in lm], key))
            used.add(k)
        return out

    @staticmethod
    def _thumbnail(frame, pts_all, rois, box):
        tw = 240
        th = int(frame.shape[0] * tw / frame.shape[1])
        sx = tw / frame.shape[1]
        thumb = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        for _, cx, cy, s in rois:                     # قصاصات الاستعادة/المسافة البعيدة
            cv2.rectangle(thumb, (int((cx - s / 2) * sx), int((cy - s / 2) * sx)),
                          (int((cx + s / 2) * sx), int((cy + s / 2) * sx)), (60, 200, 60), 1)
        for p, cx, cy, grip in pts_all:
            for lmk in p:
                cv2.circle(thumb, (int(lmk.x * tw), int(lmk.y * th)), 2, (72, 120, 226), -1)
            col = (40, 200, 255) if grip > config.GRIP_CLOSE else (255, 255, 255)
            cv2.circle(thumb, (int(cx * tw), int(cy * th)), 5, col, 2)
        bx0, by0, bx1, by1 = box
        cv2.rectangle(thumb, (int(bx0 * tw), int(by0 * th)), (int(bx1 * tw), int(by1 * th)),
                      (90, 90, 90), 1)
        return cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)

    # ------------------------------------------------------------------
    # الجسم
    # ------------------------------------------------------------------
    def _pose_loop(self, detectors):
        pose = detectors.PoseTask(
            min_detection_confidence=config.POSE_DETECTION_CONFIDENCE,
            min_tracking_confidence=0.5)
        last_id, fps, last_t, prev_mask = 0, 0.0, time.monotonic(), None
        mw, mh = config.MASK_SIZE
        x0, y0, x1, y1 = config.INTERACTION_BOX
        period = 1.0 / config.POSE_MAX_FPS
        gain, center = 1.0, None          # تكبير مدى الحركة للمتلقي البعيد، حول مركز بطيء الحركة
        try:
            while self._running:
                t_start = time.monotonic()
                fid, frame, stamp = self.grabber.wait_newer(last_id)
                if self.grabber.error:
                    break
                if frame is None or fid == last_id:
                    continue
                last_id = fid
                h, w = frame.shape[:2]
                # الدقة الكاملة: النموذج يقصّ الشخص داخلياً، فلا تكلفة إضافية تُذكر ودقة أعلى من بعيد
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                res = pose.process(rgb)

                person, proximity, silhouette, arms, sw = False, 0.0, None, {}, 0.0
                if res.pose_landmarks:
                    lm = res.pose_landmarks.landmark
                    ls, rs = lm[11], lm[12]
                    if ls.visibility > 0.3 and rs.visibility > 0.3:
                        person = True
                        sw = float(math.hypot(ls.x - rs.x, (ls.y - rs.y) * h / w))
                        proximity = sw
                        # الرسغ والسبابة لكل ذراع (لقصاصات اليد البعيدة)
                        for side, (wi, ii, hi) in (("left", (15, 19, 23)), ("right", (16, 20, 24))):
                            # الذراع مرفوعة = الرسغ أعلى من الورك (أو الورك غير ظاهر)
                            raised = lm[hi].visibility < 0.3 or lm[wi].y < lm[hi].y
                            arms[side] = {"wrist": (lm[wi].x, lm[wi].y), "index": (lm[ii].x, lm[ii].y),
                                          "vis": min(lm[wi].visibility, lm[ii].visibility), "raised": raised}
                    elif lm[0].visibility > 0.5:   # الوجه ظاهر فقط → قريب جداً
                        person, proximity = True, 0.5

                # مدى الحركة: حين يبتعد المتلقي تتقلّص حركة يده في الصورة، فيُكبَّر قليلاً (حتى REACH_MAX)
                # حول مركز يتحرك ببطء شديد — فلا ينجرف المؤشر مع حركة الكتفين
                g_target = 1.0
                if person and 0 < proximity < config.REACH_FROM:
                    g_target = min(config.REACH_MAX, config.REACH_FROM / proximity)
                    c = ((ls.x + rs.x) / 2, (ls.y + rs.y) / 2 + 0.1)
                    center = c if center is None else (center[0] + (c[0] - center[0]) * 0.04,
                                                       center[1] + (c[1] - center[1]) * 0.04)
                gain += (g_target - gain) * 0.05
                if center is None or gain < 1.01:
                    box = tuple(config.INTERACTION_BOX)
                else:
                    ccx, ccy = center
                    box = (ccx + (x0 - ccx) / gain, ccy + (y0 - ccy) / gain,
                           ccx + (x1 - ccx) / gain, ccy + (y1 - ccy) / gain)
                if config.SILHOUETTE and person and res.segmentation_mask is not None:
                    bx0, by0, bx1, by1 = box
                    small = warp_region(res.segmentation_mask, bx0 * w, by0 * h, (bx1 - bx0) * w,
                                        (by1 - by0) * h, mw, mh)
                    silhouette = _silhouette_from_mask(small, prev_mask)
                prev_mask = silhouette[0] if silhouette is not None else None

                now = time.monotonic()
                fps = 0.9 * fps + 0.1 / max(now - last_t, 1e-3)
                last_t = now
                with self._lock:
                    self._box = box
                    self._pose = {"person": person, "proximity": proximity, "silhouette": silhouette,
                                  "stamp": now, "fps": fps, "arms": arms, "shoulder_w": sw}
                rest = period - (time.monotonic() - t_start)
                if rest > 0:
                    time.sleep(rest)
        finally:
            pose.close()


class MouseInput:
    """
    محاكاة التفاعل بالفأرة (للتجربة والعرض بلا كاميرا):
      - عجلة الفأرة أو السهمين ↑↓ : القرب/البعد
      - موضع الفأرة               : اليد
      - الزر الأيسر مضغوط          : قبضة — تمسك الحروف وتسحبها، والإفلات يرميها
      - الزر الأيمن مضغوط          : ظل جسم وهمي تحت المؤشر
    """

    def __init__(self, screen_size):
        self.screen_w, self.screen_h = screen_size
        self.proximity = 0.25
        self.mouse = (0.5, 0.5)
        self.anchor = None
        self.left = False
        self.right = False
        self._sil_cache = None

    def resize(self, screen_size):
        self.screen_w, self.screen_h = screen_size

    def nudge(self, delta):
        self.proximity = float(np.clip(self.proximity + delta, 0.0, 0.5))

    def _fake_silhouette(self, x):
        key = round(x, 2)
        if self._sil_cache and self._sil_cache[0] == key:
            return self._sil_cache[1]
        mw, mh = config.MASK_SIZE
        mask = np.zeros((mh, mw), np.float32)
        cx = int(x * mw)
        cv2.ellipse(mask, (cx, int(mh * 0.35)), (int(mw * 0.06), int(mh * 0.14)), 0, 0, 360, 1.0, -1)
        cv2.ellipse(mask, (cx, int(mh * 0.95)), (int(mw * 0.16), int(mh * 0.45)), 0, 0, 360, 1.0, -1)
        sil = _silhouette_from_mask(mask)
        self._sil_cache = (key, sil)
        return sil

    def snapshot(self) -> TrackState:
        person = self.proximity > 0.02
        hands = []
        if person and self.proximity >= config.NEAR_THRESHOLD:
            hands = [(self.mouse[0], self.mouse[1], 0.1 if self.left else 0.75, "mouse",
                      1.0 if self.left else 0.0, 0.1)]
        if self.anchor is not None and person:
            hands.append((*self.anchor,0.0,"anchor",1.0,0.1))
        sil = self._fake_silhouette(self.mouse[0]) if (self.right and person) else None
        return TrackState(ok=True, person=person, proximity=self.proximity, hands=hands,
                          silhouette=sil, fps=0.0, stamp=time.monotonic())
