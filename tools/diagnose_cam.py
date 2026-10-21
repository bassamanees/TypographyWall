"""
فحص كاميرا ESP32-CAM عبر الكابل:  python tools/diagnose_cam.py [COM5]

1) يعيد تشغيل الكاميرا ويطبع رسائل إقلاعها (سبب آخر إعادة تشغيل، الواي فاي، أي Brownout)
2) يجرّب وضع الكابل: هل ترد على WALLCAM (أي أن الكود الجديد مرفوع)، وكم إطاراً في الثانية يصل
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wall import usbcam  # noqa: E402


def main():
    ports = usbcam.find_ports()
    port = sys.argv[1] if len(sys.argv) > 1 else (ports[0][0] if ports else None)
    print("ports:", ports or "NONE")
    if not port:
        print("RESULT: camera not connected by USB (no COM port)")
        return
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.2
    s.dtr = False
    s.rts = False
    try:
        s.open()
    except Exception as e:
        print("RESULT: cannot open", port, "-", e, "(is Arduino IDE / Serial Monitor open?)")
        return
    # إعادة تشغيل: RTS يتحكم بـ EN في لوحة ESP32-CAM-MB (DTR يبقى False حتى لا تدخل وضع البرمجة)
    s.rts = True
    time.sleep(0.15)
    s.rts = False
    print(f"--- boot log from {port} (12 s) ---")
    t0, log = time.time(), b""
    while time.time() - t0 < 12:
        log += s.read(4096)
    text = log.decode("utf-8", "replace")
    print(text.strip()[-3000:] or "(nothing received)")
    print("--- USB stream test ---")
    s.reset_input_buffer()
    ok = False
    for _ in range(6):
        s.write(b"WALLCAM 921600\n")
        end, buf = time.time() + 0.6, b""
        while time.time() < end:
            buf += s.read(512)
        if b"\nOK\n" in buf:
            ok = True
            break
    if not ok:
        boots = text.count("ets Jul")
        if "BROWNOUT" in text or boots > 1:
            print(f"RESULT: camera keeps restarting ({boots} boots in 12 s, brownout) -> POWER problem: "
                  "better USB cable/port, 5V 2A supply, or a 470-1000uF capacitor on 5V/GND")
        elif "Waiting 3s for USB mode" in text or "SETUP MODE" in text:
            print("RESULT: camera runs but did not answer WALLCAM (try again; maybe Wi-Fi was busy)")
        else:
            print("RESULT: camera did NOT answer WALLCAM -> the new camera code may not be uploaded")
        s.close()
        return
    time.sleep(0.05)
    s.baudrate = 921600
    frames, data, t0, last_k = 0, bytearray(), time.time(), 0
    while time.time() - t0 < 5:
        if time.time() - last_k > 0.5:
            s.write(b"K")
            last_k = time.time()
        data += s.read(65536)
        while True:
            i = data.find(usbcam.MAGIC)
            if i < 0 or len(data) < i + 8:
                break
            n = int.from_bytes(data[i + 4:i + 8], "little")
            if len(data) < i + 8 + n:
                break
            frames += data[i + 8:i + 10] == b"\xff\xd8"
            del data[:i + 8 + n]
    s.close()
    print(f"RESULT: USB mode works: {frames / 5:.1f} frames/s at 921600 baud" if frames
          else "RESULT: camera answered OK but sent no frames")


if __name__ == "__main__":
    main()
