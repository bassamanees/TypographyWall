"""
البحث عن كاميرا ESP32-CAM على الشبكة وإعداد شبكتها من التطبيق (مكتبة بايثون القياسية فقط).

الكاميرا (CameraWebServer.ino) تفتح صفحة إعداد على المنفذ 8080:
    GET  /info       حالة الكاميرا: {"device": "wall-espcam", "mode": "sta"|"ap", "ip": ...}
    GET  /scan.json  الشبكات 2.4GHz التي تراها الكاميرا
    POST /setwifi    ssid=...&pass=...   ← تحفظ الشبكة وتعيد التشغيل
والبث على المنفذ 81: http://IP:81/stream
"""
import concurrent.futures
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CFG_PORT = 8080
STREAM_PORT = 81
AP_HOST = "192.168.4.1"          # عنوان الكاميرا حين تبثّ شبكتها الخاصة (وضع الإعداد)
AP_SSID = "ESP32_CAM_NET"
AP_PASSWORD = "password1234"
MDNS_NAME = "espcam.local"
DEVICE_ID = "wall-espcam"

# بلا وكيل (Proxy): عناوين الشبكة المحلية يجب ألا تمرّ عبر وكيل النظام
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def stream_url(host, port=STREAM_PORT):
    return f"http://{host}:{port}/stream"


def normalize_source(text):
    """
    ما يكتبه المستخدم → رابط البث. يقبل: 192.168.1.5  أو  espcam.local  أو  http://192.168.1.5:81/stream
    يعيد None إن كان النص فارغاً.
    """
    t = (text or "").strip()
    if not t:
        return None
    if "://" in t:
        return t
    host = t.split("/")[0]
    if ":" in host:                      # عنوان:منفذ
        return f"http://{host}/stream"
    return stream_url(host)


# ----------------------------------------------------------------------
def _request_json(url, timeout, data=None):
    headers = {"Connection": "close", "Cache-Control": "no-cache"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read(131072)
    return json.loads(raw.decode("utf-8", "replace"))


def probe(host, port=CFG_PORT, timeout=1.0):
    """يسأل عنواناً: هل هو كاميرا هذا المشروع؟ يعيد معلوماتها (مع host/cfg_port) أو None."""
    try:
        info = _request_json(f"http://{host}:{port}/info", timeout)
    except Exception:
        return None
    if not isinstance(info, dict) or info.get("device") != DEVICE_ID:
        return None
    ip = info.get("ip")
    try:
        ipaddress.IPv4Address(ip)
    except Exception:
        ip = host
    info["host"] = ip                    # العنوان الرقمي الفعلي (حتى لو وجدناها باسم espcam.local)
    info["cfg_port"] = port
    return info


def local_ipv4():
    """عناوين هذا الحاسوب على الشبكات المحلية."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # لا يُرسل شيء فعلاً: لمعرفة الواجهة الافتراضية فقط
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(res[4][0])
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.", "0.")))


def _probe_many(hosts, port=CFG_PORT, timeout=0.6, workers=96, progress=None, stop=None):
    hosts = list(dict.fromkeys(hosts))
    found, done = {}, 0
    if not hosts:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(hosts))) as pool:
        futures = [pool.submit(probe, h, port, timeout) for h in hosts]
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if progress:
                progress(done, len(hosts))
            if stop is not None and stop.is_set():
                for f in futures:
                    f.cancel()
                break
            info = fut.result()
            if info:
                found[info["host"]] = info
    return list(found.values())


def find_cameras(last_host=None, port=CFG_PORT, progress=None, stop=None, scan=True, subnets=None):
    """
    يبحث عن كاميرات المشروع: العناوين المحتملة أولاً (آخر عنوان معروف، شبكة الإعداد، espcam.local)،
    ثم فحص شبكتك المحلية كلها (254 عنواناً) خلال بضع ثوانٍ.
    subnets: قائمة بادئات مثل ["192.168.1"] (للاختبار)؛ الافتراضي من عناوين هذا الحاسوب.
    """
    quick = [h for h in (last_host, AP_HOST, MDNS_NAME) if h]
    found = _probe_many(quick, port, timeout=1.5, workers=4, stop=stop)
    if found or not scan:
        return found
    if subnets is None:
        mine = local_ipv4()
        subnets = list(dict.fromkeys(".".join(ip.split(".")[:3]) for ip in mine))[:3]
    own = set(local_ipv4())
    hosts = [f"{p}.{i}" for p in subnets for i in range(1, 255) if f"{p}.{i}" not in own]
    return _probe_many(hosts, port, timeout=0.6, progress=progress, stop=stop)


def wait_for_camera(timeout=90.0, port=CFG_PORT, stop=None, status=None, require_station=True,
                    subnets=None, poll=3.0):
    """
    بعد إرسال الشبكة: الكاميرا تعيد التشغيل وتنضم لشبكتك (10-25 ثانية). ننتظر ظهورها.
    require_station=True: لا نقبل إلا ظهورها على شبكة المنزل (mode == "sta").
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        if stop is not None and stop.is_set():
            return None
        if status:
            status(int(time.time() - t0))
        for info in find_cameras(port=port, stop=stop, subnets=subnets):
            if not require_station or info.get("mode") == "sta":
                return info
        time.sleep(poll)
    return None


def networks(host, port=CFG_PORT, refresh=False, timeout=8.0):
    """الشبكات التي تراها الكاميرا: [{"ssid", "rssi", "ch"}] (الأقوى أولاً)."""
    url = f"http://{host}:{port}/scan.json" + ("?refresh=1" if refresh else "")
    try:
        data = _request_json(url, timeout)
    except Exception:
        return []
    return [n for n in data if isinstance(n, dict) and n.get("ssid")] if isinstance(data, list) else []


def validate_wifi(ssid, password):
    """نفس قواعد الكاميرا. يعيد رسالة الخطأ أو None إن كان صحيحاً."""
    if not ssid or not ssid.strip():
        return "اكتب اسم الشبكة"
    if len(ssid.encode("utf-8")) > 32:
        return "اسم الشبكة أطول من 32 بايت"
    if password and not (8 <= len(password.encode("utf-8")) <= 63):
        return "كلمة المرور يجب أن تكون من 8 إلى 63 حرفاً (أو اتركها فارغة لشبكة مفتوحة)"
    return None


def provision(host, ssid, password, port=CFG_PORT, timeout=8.0):
    """يرسل الشبكة إلى الكاميرا. يعيد (نجح، رسالة). الكاميرا تعيد التشغيل بعد النجاح."""
    err = validate_wifi(ssid, password)
    if err:
        return False, err
    data = urllib.parse.urlencode({"ssid": ssid, "pass": password or ""}).encode("utf-8")
    try:
        body = _request_json(f"http://{host}:{port}/setwifi", timeout, data=data)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {}
        return False, body.get("error") or f"رفضت الكاميرا الطلب (HTTP {e.code})"
    except Exception as e:
        return False, f"تعذّر الوصول إلى الكاميرا: {e}"
    return bool(body.get("ok")), "" if body.get("ok") else str(body.get("error", "فشل"))
