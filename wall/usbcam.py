"""
كاميرا ESP32-CAM عبر كابل USB (لوحة ESP32-CAM-MB، شريحة CH340) بلا واي فاي.

الاتفاق مع كود الكاميرا (CameraWebServer.ino):
  1. نفتح المنفذ بسرعة 115200 دون أن نلمس DTR/RTS (وإلا تُعاد الكاميرا أو تدخل وضع البرمجة)
  2. نرسل "WALLCAM <السرعة>\n" حتى ترد "OK" (قد تكون ما زالت تُقلع)
  3. ننتقل للسرعة نفسها؛ الكاميرا تطفئ الواي فاي وترسل:  A5 5A 57 43 | الطول (4 بايت) | JPEG
  4. نرسل "K" كل نصف ثانية؛ إن توقفنا 3 ثوانٍ تعيد الكاميرا التشغيل (فيعود الواي فاي)
إن لم تصل إطارات بالسرعة الأولى نجرّب أبطأ.

بواجهة تشبه cv2.VideoCapture مثل MjpegStream: خيط يقرأ بأقصى سرعة ويحتفظ بأحدث إطار فقط.
"""
import struct
import threading
import time

import cv2
import numpy as np

MAGIC = b"\xa5\x5aWC"
# 921,600 جُرّبت مع كاميرا حقيقية (CH340): 28 إطاراً/ث بلا أي بايت فاسد، وهذا حد الكاميرا أصلاً.
# 2,000,000 أسقطت بايتات (صور تالفة)، فلا فائدة منها.
BAUDS = (921_600, 460_800)
HANDSHAKE_BAUD = 115_200
PREFIX = "usb:"                  # مصدر الكاميرا في التطبيق: "usb:COM5"
# شرائح USB-تسلسلي الشائعة في لوحات ESP: CH340، CP210x، FTDI، ESP الأصلية
KNOWN_VIDS = {0x1A86: "CH340", 0x10C4: "CP210x", 0x0403: "FTDI", 0x303A: "ESP"}


def find_ports():
    """منافذ USB المحتملة للكاميرا: [(المنفذ، الوصف)] — الشرائح المعروفة أولاً."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for p in list_ports.comports():
        known = p.vid in KNOWN_VIDS
        if known or "usb" in (p.description or "").lower():
            ports.append((not known, p.device, KNOWN_VIDS.get(p.vid) or p.description or p.device))
    return [(dev, desc) for _, dev, desc in sorted(ports)]


def _open(port, baud):
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.05, 1.0
    s.dtr = False                 # قبل الفتح: لوحة MB تربط DTR/RTS بإعادة التشغيل ووضع البرمجة
    s.rts = False
    s.open()
    return s


class SerialJpegStream:
    def __init__(self, port, timeout=12.0):
        self.port = port
        self.baud = None
        self.error = None
        self._cond = threading.Condition()
        self._jpeg = None
        self._id = self._seen = 0
        self._running = True
        self._ser = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        with self._cond:          # انتظر أول إطار (الكاميرا قد تحتاج ثوانٍ لتُقلع)
            self._cond.wait_for(lambda: self._jpeg is not None or not self._running, timeout)

    # ---------------------------------------------------------------- واجهة VideoCapture
    def isOpened(self):
        return self._jpeg is not None

    def set(self, *_):
        return False

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

    # ---------------------------------------------------------------- الخيط
    def _publish(self, data):
        with self._cond:
            self._jpeg = data
            self._id += 1
            self._cond.notify_all()

    def _handshake(self, ser, baud, wait=8.0):
        """يكرر الطلب حتى ترد الكاميرا OK (يُتجاهل نص الإقلاع)."""
        t0, buf = time.monotonic(), b""
        while self._running and time.monotonic() - t0 < wait:
            ser.write(f"WALLCAM {baud}\n".encode())
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                buf = (buf + ser.read(4096))[-4096:]
                if b"\nOK\n" in buf:
                    return True
        return False

    def _stream(self, ser, seconds_without_frame=3.0):
        """يقرأ الإطارات حتى ينقطع البث. يعيد عدد الإطارات."""
        buf = bytearray()
        frames, last_frame, last_keep = 0, time.monotonic(), 0.0
        while self._running:
            now = time.monotonic()
            if now - last_keep > 0.5:
                ser.write(b"K")
                last_keep = now
            chunk = ser.read(max(1, min(ser.in_waiting, 65536)))
            if chunk:
                buf += chunk
            # استخرج أحدث إطار كامل فقط؛ وما قبله يُرمى
            newest = None
            while True:
                i = buf.find(MAGIC)
                if i < 0:
                    del buf[:max(0, len(buf) - 3)]
                    break
                if len(buf) < i + 8:
                    del buf[:i]
                    break
                n = struct.unpack_from("<I", buf, i + 4)[0]
                if not 100 < n < 400_000:            # طول غير منطقي: بايت عشوائي يشبه البداية
                    del buf[:i + 1]
                    continue
                if len(buf) < i + 8 + n:
                    del buf[:i]
                    break
                jpeg = bytes(buf[i + 8:i + 8 + n])
                del buf[:i + 8 + n]
                if jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9":   # إطار كامل فقط
                    newest = jpeg
            if newest is not None:
                self._publish(newest)
                frames += 1
                last_frame = now
            elif now - last_frame > seconds_without_frame:
                break
        return frames

    def _run(self):
        try:
            while self._running:
                for baud in BAUDS:
                    if not self._running:
                        break
                    try:
                        ser = self._ser = _open(self.port, HANDSHAKE_BAUD)
                    except Exception as e:
                        self.error = f"تعذّر فتح المنفذ {self.port}: {e}"
                        self._running = False
                        break
                    try:
                        if not self._handshake(ser, baud):
                            self.error = "الكاميرا لا ترد عبر الكابل (هل رُفع الكود الجديد إليها؟)"
                            continue
                        time.sleep(0.05)
                        ser.baudrate = baud
                        self.baud = baud
                        if self._stream(ser) > 0:
                            self.error = None
                            break            # البث انقطع بعد أن عمل: نعيد الاتصال بالسرعة نفسها
                        # لا إطارات بهذه السرعة: ننتظر إعادة تشغيل الكاميرا ونجرّب أبطأ
                        time.sleep(3.5)
                    finally:
                        try:
                            ser.close()
                        except Exception:
                            pass
                else:
                    if self._running:
                        self._running = self._jpeg is not None   # لم يعمل أبداً: نتوقف
                if self._running:
                    time.sleep(1.0)
        finally:
            self._running = False
            with self._cond:
                self._cond.notify_all()
