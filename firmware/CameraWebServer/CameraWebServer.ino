#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include "esp_wifi.h"
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFiUdp.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ===========================
// Select camera model
// ===========================
#define CAMERA_MODEL_AI_THINKER // Has PSRAM
#include "camera_pins.h"

// ===========================================================================
// الشبكة تُحفظ داخل ذاكرة الكاميرا، وتُغيَّر من التطبيق (أو من صفحة الإعداد
// في الهاتف) بلا إعادة رفع الكود. لا حاجة لكتابة اسم الشبكة هنا.
// ===========================================================================
const char *DEFAULT_SSID = "";     // اختياري: شبكة افتراضية إن لم تُحفظ أي شبكة
const char *DEFAULT_PASS = "";

// إن لم تُحفظ شبكة أو فشل الاتصال بها: تبثّ الكاميرا شبكتها الخاصة (وضع الإعداد)
const char *ap_ssid = "ESP32_CAM_NET";
const char *ap_password = "password1234";

const char *HOST_NAME = "espcam";          // http://espcam.local
const uint16_t CFG_PORT = 8080;            // صفحة الإعداد: http://العنوان:8080
const uint32_t STA_TIMEOUT_MS = 10000;     // لكل محاولة اتصال (3 محاولات)

void startCameraServer();
void setupLedFlash();

static bool inStaMode = false;
String sta_ssid, sta_pass;
Preferences prefs;
WebServer cfgServer(CFG_PORT);

// true: عند كل تشغيل تبدأ الكاميرا بشبكتها الخاصة ESP32_CAM_NET ولا تتصل بأي شبكة وحدها؛
//       تتصل بشبكة منزلك فقط عندما يرسلها لها التطبيق (ولا تُحفظ، فتنساها عند إطفائها).
// false: تحفظ الشبكة وتتصل بها تلقائياً عند كل تشغيل.
static const bool ALWAYS_SETUP_FIRST = true;
static bool pendingConnect = false;
static uint32_t connectAt = 0;
static bool pendingRestart = false;
static uint32_t restartAt = 0;

// الشبكة كما رآها الـESP فعلاً (اسمها الدقيق وقناتها وعنوانها)
String foundSsid;
int foundChannel = 0;
uint8_t foundBssid[6];

// قائمة الشبكات 2.4GHz التي يراها الـESP (تُعرض في التطبيق لاختيار الاسم الصحيح)
struct Net { String ssid; int rssi; int ch; };
static const int MAX_NETS = 24;
Net nets[MAX_NETS];
int netCount = 0;

// ---------------------------------------------------------------------------
// أدوات
// ---------------------------------------------------------------------------
static String norm(String s) {   // يتجاهل المسافات الزائدة وحالة الأحرف
  s.trim();
  s.toLowerCase();
  return s;
}

static String jsonEscape(const String &s) {
  String o;
  o.reserve(s.length() + 8);
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if ((uint8_t)c >= 0x20) o += c;
  }
  return o;
}

static String htmlEscape(const String &s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') o += "&amp;";
    else if (c == '<') o += "&lt;";
    else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;";
    else o += c;
  }
  return o;
}

static String currentIp() {
  return inStaMode ? WiFi.localIP().toString() : WiFi.softAPIP().toString();
}

const char *wifiStatusName(wl_status_t s) {
  switch (s) {
    case WL_NO_SSID_AVAIL:   return "network not found (check the name; must be 2.4GHz)";
    case WL_CONNECT_FAILED:  return "connect failed (wrong password?)";
    case WL_CONNECTION_LOST: return "connection lost";
    case WL_DISCONNECTED:    return "disconnected";
    case WL_IDLE_STATUS:     return "idle";
    default:                 return "unknown";
  }
}

// يطبع سبب الانقطاع الحقيقي من الـESP
void onWifiDisconnect(WiFiEvent_t event, WiFiEventInfo_t info) {
  static uint32_t last = 0;
  if (millis() - last < 1500) return;
  last = millis();
  int r = info.wifi_sta_disconnected.reason;
  Serial.printf("\n  [disconnect reason=%d] ", r);
  switch (r) {
    case 2: case 202: case 15: Serial.println("auth failed: wrong password"); break;
    case 201:                  Serial.println("no AP found"); break;
    case 205:                  Serial.println("connection failed"); break;
    case 4: case 8:            Serial.println("timeout / AP ended the link"); break;
    default:                   Serial.println("other reason"); break;
  }
}

// ---------------------------------------------------------------------------
// حفظ وقراءة الشبكة
// ---------------------------------------------------------------------------
void loadCreds() {
  prefs.begin("wall", true);
  sta_ssid = prefs.getString("ssid", DEFAULT_SSID);
  sta_pass = prefs.getString("pass", DEFAULT_PASS);
  prefs.end();
}

void saveCreds(const String &ssid, const String &pass) {
  prefs.begin("wall", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
}

void forgetCreds() {
  prefs.begin("wall", false);
  prefs.remove("ssid");
  prefs.remove("pass");
  prefs.end();
}

// ---------------------------------------------------------------------------
// الواي فاي
// ---------------------------------------------------------------------------
void prepareWiFi() {
  WiFi.persistent(false);        // لا تحفظ إعدادات الواي فاي في الذاكرة (نحفظ شبكتنا نحن)
  WiFi.onEvent(onWifiDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(300);
  WiFi.mode(WIFI_STA);

  // نسمح بالقنوات 1..13 (بعض الهواتف تختار 12-13 والإعداد الافتراضي قد يمنعها)
  wifi_country_t country;
  memset(&country, 0, sizeof(country));
  strcpy(country.cc, "CN");
  country.schan = 1;
  country.nchan = 13;
  country.max_tx_power = 20;
  country.policy = WIFI_COUNTRY_POLICY_MANUAL;
  esp_wifi_set_country(&country);

  WiFi.setSleep(false);          // يمنع تقطّع البث بسبب وضع توفير الطاقة
  esp_wifi_set_ps(WIFI_PS_NONE); // إلغاء توفير الطاقة نهائياً (سبب رئيسي لتأخير الواي فاي)
  WiFi.setTxPower(WIFI_POWER_19_5dBm);   // أقصى قوة إرسال
}

void doScan() {
  Serial.println("Scanning networks...");
  int n = WiFi.scanNetworks();
  Serial.printf("Found %d networks\n", n);
  netCount = 0;
  foundChannel = 0;
  String target = norm(sta_ssid);
  for (int i = 0; i < n; i++) {                        // كل النتائج (الأماكن المزدحمة فيها العشرات)
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0) continue;                  // شبكات مخفية
    bool dup = false;
    for (int k = 0; k < netCount; k++) if (nets[k].ssid == ssid) { dup = true; break; }
    if (dup && !(target.length() && norm(ssid) == target && !foundChannel)) continue;
    if (netCount < MAX_NETS && !dup) nets[netCount++] = {ssid, (int)WiFi.RSSI(i), (int)WiFi.channel(i)};
    bool mine = target.length() && norm(ssid) == target;
    Serial.printf("  %s%s  RSSI %d  ch %d\n", mine ? "* " : "  ", ssid.c_str(),
                  (int)WiFi.RSSI(i), (int)WiFi.channel(i));
    if (mine && !foundChannel) {                       // نتصل بالاسم والقناة والعنوان كما رآها الـESP
      foundSsid = ssid;
      foundChannel = WiFi.channel(i);
      memcpy(foundBssid, WiFi.BSSID(i), 6);
    }
  }
  WiFi.scanDelete();
}

bool connectToWiFi() {
  if (sta_ssid.length() == 0) {
    Serial.println("No saved WiFi: setup mode");
    return false;
  }
  for (int attempt = 1; attempt <= 3; attempt++) {
    Serial.printf("Attempt %d/3: connecting to %s", attempt, sta_ssid.c_str());
    if (foundChannel) WiFi.begin(foundSsid.c_str(), sta_pass.c_str(), foundChannel, foundBssid);
    else              WiFi.begin(sta_ssid.c_str(), sta_pass.c_str());
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < STA_TIMEOUT_MS) {
      delay(500);
      Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
      WiFi.setAutoReconnect(true);
      return true;
    }
    Serial.printf("  failed, status=%d (%s)\n", (int)WiFi.status(), wifiStatusName(WiFi.status()));
    WiFi.disconnect(true);
    delay(500);
    WiFi.mode(WIFI_STA);
  }
  return false;
}

// وضع الإعداد: الكاميرا تبثّ شبكتها الخاصة (وتبقى قادرة على فحص الشبكات)
// نختار أقل قناة ازدحاماً (1 أو 6 أو 11) من نتيجة الفحص، فتظهر شبكة الإعداد بوضوح في الأماكن المزدحمة
int bestApChannel() {
  int best = 1;
  long bestScore = -1;
  const int cands[3] = {1, 6, 11};
  for (int c = 0; c < 3; c++) {
    long score = 0;
    for (int i = 0; i < netCount; i++) {
      if (abs(nets[i].ch - cands[c]) <= 2) score += 100 + nets[i].rssi;   // شبكة قوية قريبة = ازدحام أكبر
    }
    if (bestScore < 0 || score < bestScore) { bestScore = score; best = cands[c]; }
  }
  return best;
}

void startAp() {
  int ch = bestApChannel();
  Serial.printf("Starting Access Point (setup mode) on channel %d...\n", ch);
  WiFi.disconnect(true);
  // نقطة وصول فقط (لا STA في الخلفية): الحاسوب يتصل بالكاميرا مباشرة، وهذا أسرع وأثبت للبث
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ap_ssid, ap_password, ch, 0, 2);
  // الكاميرا نفسها هي خادم DNS للأجهزة المتصلة (انظر handleDns)
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0),
                    IPAddress(192, 168, 4, 2), IPAddress(192, 168, 4, 1));
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW_HT20);   // 20MHz أثبت من 40 في الأماكن المزدحمة
  // قوة أقل من الحد الأقصى: كافية لمسافة الحاسوب القريبة، وتمنع هبوط التيار (Brownout) الذي يعيد تشغيل الكاميرا
  WiFi.setTxPower(WIFI_POWER_15dBm);
  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info) {
    Serial.printf("AP: device connected (now %d)\n", WiFi.softAPgetStationNum());
  }, ARDUINO_EVENT_WIFI_AP_STACONNECTED);
  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info) {
    Serial.printf("AP: device disconnected, reason %d\n", info.wifi_ap_stadisconnected.reason);
  }, ARDUINO_EVENT_WIFI_AP_STADISCONNECTED);
}

// ---------------------------------------------------------------------------
// خادم DNS صغير: كل اسم يُجاب بعنوان الكاميرا، فيصل فحص الإنترنت في ويندوز/أندرويد/آبل إلى الكاميرا نفسها
// (وهي ترد بالصفحة المتوقعة في app_httpd.cpp)، فيعدّ النظام الشبكة «متصلة» ولا يقفز إلى شبكة أخرى.
// dns.msftncsi.com يُجاب بالعنوان الذي ينتظره ويندوز تحديداً.
// ---------------------------------------------------------------------------
WiFiUDP dnsUdp;
static bool dnsOn = false;

void startDns() {
  dnsOn = dnsUdp.begin(53);
  Serial.println(dnsOn ? "DNS: answering all names with the camera address" : "DNS: failed to start");
}

void handleDns() {
  if (!dnsOn) return;
  int n = dnsUdp.parsePacket();
  if (n <= 0) return;
  uint8_t q[512];
  int len = dnsUdp.read(q, sizeof(q));
  if (len <= 12 || (q[2] & 0x80) || (((q[4] << 8) | q[5]) < 1)) return;   // ليس سؤالاً صالحاً
  int i = 12;
  String name;
  while (i < len && q[i]) {
    int l = q[i++];
    if (l > 63 || i + l > len) return;
    if (name.length()) name += '.';
    for (int k = 0; k < l; k++) name += (char)tolower(q[i + k]);
    i += l;
  }
  i++;
  if (i + 4 > len) return;
  uint16_t qtype = (q[i] << 8) | q[i + 1];
  int qend = i + 4;                     // نهاية السؤال الأول (نجيب عليه وحده)
  uint8_t r[512];
  memcpy(r, q, qend);
  r[2] = 0x84 | (q[2] & 0x01);          // رد، إجابة موثوقة، مع نسخ «طلب التكرار»
  r[3] = 0x80;
  r[4] = 0; r[5] = 1;                   // سؤال واحد
  r[6] = 0; r[7] = 0;
  r[8] = r[9] = r[10] = r[11] = 0;
  int o = qend;
  if (qtype == 1) {                     // A: عنوان IPv4
    IPAddress ip = (name == "dns.msftncsi.com") ? IPAddress(131, 107, 255, 255) : WiFi.softAPIP();
    uint8_t ans[16] = {0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 30, 0, 4,
                       (uint8_t)ip[0], (uint8_t)ip[1], (uint8_t)ip[2], (uint8_t)ip[3]};
    memcpy(r + o, ans, sizeof(ans));
    o += sizeof(ans);
    r[7] = 1;                           // جواب واحد
  }                                     // غيره (مثل AAAA): لا جواب، بلا خطأ
  dnsUdp.beginPacket(dnsUdp.remoteIP(), dnsUdp.remotePort());
  dnsUdp.write(r, o);
  dnsUdp.endPacket();
}

void startMdns() {
  if (MDNS.begin(HOST_NAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("mDNS name: %s.local\n", HOST_NAME);
  }
}

// الكاميرا شُغّلت قبل شبكتك (مثلاً الهاتف لم يفتح النقطة بعد): أعد المحاولة بين حين وآخر
// ما دام لا أحد متصل بشبكة الإعداد، فتنتقل لشبكتك وحدها بلا إعادة تشغيل.
bool tryStaFromAp() {
  Serial.println("Retrying saved WiFi...");
  WiFi.begin(sta_ssid.c_str(), sta_pass.c_str());
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 9000) {
    cfgServer.handleClient();
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    inStaMode = true;
    MDNS.end();
    startMdns();
    Serial.println("Connected to saved WiFi: " + WiFi.localIP().toString());
    return true;
  }
  WiFi.disconnect(false);        // أوقف المحاولة وأبقِ شبكة الإعداد
  return false;
}

// ---------------------------------------------------------------------------
// صفحة وخادم الإعداد (المنفذ 8080)
//   GET  /info       حالة الكاميرا (يستعملها التطبيق لإيجادها)
//   GET  /scan.json  الشبكات التي يراها الـESP (?refresh=1 لإعادة الفحص وهو متصل بشبكتك)
//   POST /setwifi    ssid=...&pass=...  يحفظ ثم يعيد التشغيل
//   POST /forget     ينسى الشبكة المحفوظة ويعيد التشغيل
//   GET  /           نموذج للهاتف
// ---------------------------------------------------------------------------
void handleInfo() {
  String ip = currentIp();
  String j = "{\"device\":\"wall-espcam\",\"mode\":\"";
  j += inStaMode ? "sta" : "ap";
  j += "\",\"ssid\":\"" + jsonEscape(inStaMode ? sta_ssid : String(ap_ssid)) + "\"";
  j += ",\"saved\":\"" + jsonEscape(sta_ssid) + "\"";
  j += ",\"ip\":\"" + ip + "\"";
  j += ",\"rssi\":" + String(inStaMode ? WiFi.RSSI() : 0);
  j += ",\"stream\":\"http://" + ip + ":81/stream\"}";
  cfgServer.sendHeader("Cache-Control", "no-store");
  cfgServer.send(200, "application/json", j);
}

void handleScanJson() {
  if (cfgServer.hasArg("refresh") && inStaMode) doScan();
  String j = "[";
  for (int i = 0; i < netCount; i++) {
    if (i) j += ",";
    j += "{\"ssid\":\"" + jsonEscape(nets[i].ssid) + "\",\"rssi\":" + String(nets[i].rssi) +
         ",\"ch\":" + String(nets[i].ch) + "}";
  }
  j += "]";
  cfgServer.sendHeader("Cache-Control", "no-store");
  cfgServer.send(200, "application/json", j);
}

static void replyHtml(int code, const String &body) {
  String p = "<!doctype html><html lang=ar dir=rtl><head><meta charset=utf-8>"
             "<meta name=viewport content='width=device-width,initial-scale=1'><title>إعداد الكاميرا</title>"
             "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;max-width:420px;"
             "margin:24px auto;padding:0 16px}input,button{width:100%;box-sizing:border-box;font-size:18px;"
             "padding:12px;margin:6px 0;border-radius:8px;border:1px solid #555;background:#222;color:#fff}"
             "button{background:#e27848;border:0;color:#111;font-weight:700}small{color:#999}</style></head><body>";
  p += body;
  p += "</body></html>";
  cfgServer.send(code, "text/html; charset=utf-8", p);
}

void handleRoot() {
  String b = "<h2>إعداد شبكة الكاميرا</h2>";
  b += "<p>الحالة: ";
  b += inStaMode ? ("متصلة بشبكة <b>" + htmlEscape(sta_ssid) + "</b> — " + currentIp())
                 : "في وضع الإعداد (لم تتصل بشبكة)";
  b += "</p><form method=POST action=/setwifi><input type=hidden name=html value=1>";
  b += "<input name=ssid list=n placeholder='اسم الشبكة' value='" + htmlEscape(sta_ssid) + "' required><datalist id=n>";
  for (int i = 0; i < netCount; i++) b += "<option value='" + htmlEscape(nets[i].ssid) + "'>";
  b += "</datalist><input name=pass type=password placeholder='كلمة المرور'>";
  b += "<button type=submit>حفظ وإعادة التشغيل</button></form>";
  b += "<small>الكاميرا تعمل على شبكات 2.4GHz فقط.</small>";
  replyHtml(200, b);
}

void handleSetWifi() {
  String ssid = cfgServer.arg("ssid");
  String pass = cfgServer.arg("pass");
  bool html = cfgServer.hasArg("html");
  String err = "";
  if (ssid.length() < 1 || ssid.length() > 32) err = "ssid must be 1..32 bytes";
  else if (pass.length() != 0 && (pass.length() < 8 || pass.length() > 63)) err = "password must be 8..63 chars (or empty for open network)";
  if (err.length()) {
    if (html) replyHtml(400, "<h3>خطأ</h3><p>" + htmlEscape(err) + "</p><a href=/>رجوع</a>");
    else cfgServer.send(400, "application/json", "{\"ok\":false,\"error\":\"" + jsonEscape(err) + "\"}");
    return;
  }
  if (ALWAYS_SETUP_FIRST) {
    sta_ssid = ssid;                     // في الذاكرة فقط، بلا حفظ دائم
    sta_pass = pass;
    Serial.println("WiFi received (not saved): " + ssid);
    if (html) replyHtml(200, "<h3>تم ✓</h3><p>تتصل الكاميرا الآن بشبكة <b>" + htmlEscape(ssid) +
                             "</b>. أعد توصيل جهازك بنفس الشبكة.</p>");
    else cfgServer.send(200, "application/json", "{\"ok\":true,\"restarting\":true}");
    pendingConnect = true;               // نبدأ الاتصال بعد إرسال الرد
    connectAt = millis() + 800;
    return;
  }
  saveCreds(ssid, pass);
  Serial.println("Saved new WiFi: " + ssid);
  if (html) replyHtml(200, "<h3>تم الحفظ ✓</h3><p>تعيد الكاميرا التشغيل الآن وتتصل بشبكة <b>" +
                           htmlEscape(ssid) + "</b>. أعد توصيل جهازك بنفس الشبكة.</p>");
  else cfgServer.send(200, "application/json", "{\"ok\":true,\"restarting\":true}");
  pendingRestart = true;
  restartAt = millis() + 1200;
}

void handleForget() {
  forgetCreds();
  cfgServer.send(200, "application/json", "{\"ok\":true,\"restarting\":true}");
  pendingRestart = true;
  restartAt = millis() + 1200;
}

void startConfigServer() {
  cfgServer.on("/", HTTP_GET, handleRoot);
  cfgServer.on("/info", HTTP_GET, handleInfo);
  cfgServer.on("/scan.json", HTTP_GET, handleScanJson);
  cfgServer.on("/setwifi", HTTP_ANY, handleSetWifi);
  cfgServer.on("/forget", HTTP_POST, handleForget);
  cfgServer.begin();
}

// ---------------------------------------------------------------------------
// البث عبر كابل USB (بلا واي فاي): التطبيق يرسل  "WALLCAM <السرعة>\n"  بسرعة 115200،
// فترد الكاميرا "OK"، وتطفئ الواي فاي، وتنتقل لتلك السرعة وترسل الإطارات:
//     A5 5A 57 43 | طول JPEG (4 بايت little-endian) | JPEG
// التطبيق يرسل "K" كل نصف ثانية؛ إن انقطع 3 ثوانٍ (أُغلق التطبيق) تعيد الكاميرا التشغيل فيعود الواي فاي.
// ---------------------------------------------------------------------------
// إعدادات الكاميرا محفوظة لإعادة تهيئتها بدقة أخرى (وضع الكابل)
static camera_config_t camCfg;

static void tuneSensor(framesize_t size, int quality) {
  sensor_t *s = esp_camera_sensor_get();
  if (!s) return;
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  s->set_framesize(s, size);
  s->set_quality(s, quality);    // صفحة الويب قد تعيد الجودة العالية؛ نثبّتها هنا
}

static bool usbMode = false;
static bool wifiStarted = false;
static uint32_t usbKeepAt = 0;
static String usbLine;

void pollUsbCommands() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == 'K') { usbKeepAt = millis(); continue; }
    if (usbMode) continue;
    if (c == '\n') {
      usbLine.trim();
      if (usbLine.startsWith("WALLCAM ")) {
        uint32_t baud = (uint32_t)usbLine.substring(8).toInt();
        if (baud >= 115200 && baud <= 3000000) {
          Serial.print("\nOK\n");
          Serial.flush();
          if (wifiStarted) {
            cfgServer.stop();
            WiFi.mode(WIFI_OFF);
          }             // لا حاجة للواي فاي: أقل حرارة واستهلاكاً (تغذية USB)
          delay(30);
          Serial.updateBaudRate(baud);
          delay(150);                      // يعطي التطبيق وقتاً ليغيّر سرعته هو أيضاً
          while (Serial.available()) Serial.read();
          // الكابل أبطأ من أن يحمل 640x480 بسلاسة. تصغير الصورة وحده يُبقي الحساس على توقيت الصورة الكبيرة
          // (21 إطاراً/ث)، فنعيد تهيئة الكاميرا كاملة بـ 320x240 لتعود إلى نحو 30 إطاراً/ث
          delay(100);
          esp_camera_deinit();
          camCfg.frame_size = FRAMESIZE_QVGA;
          camCfg.jpeg_quality = 16;
          if (esp_camera_init(&camCfg) == ESP_OK) tuneSensor(FRAMESIZE_QVGA, 16);
          usbMode = true;
          usbKeepAt = millis();
        }
      }
      usbLine = "";
    } else if (c != '\r') {
      usbLine += c;
      if (usbLine.length() > 40) usbLine = "";
    }
  }
}

void usbStreamFrame() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;
  uint8_t hdr[8] = {0xA5, 0x5A, 0x57, 0x43,
                    (uint8_t)(fb->len), (uint8_t)(fb->len >> 8), (uint8_t)(fb->len >> 16), (uint8_t)(fb->len >> 24)};
  Serial.write(hdr, sizeof(hdr));
  Serial.write(fb->buf, fb->len);          // يتوقف عند امتلاء المخزن، فيضبط الإيقاع وحده
  esp_camera_fb_return(fb);
}

// ---------------------------------------------------------------------------
void setup() {
  // تغذية USB من الحاسوب ضعيفة غالباً: هبوط قصير في الجهد كان يعيد تشغيل الكاميرا باستمرار (Brownout).
  // نلغي القطع عند الهبوط القصير (حل معروف في ESP32-CAM)، ونخفّض سرعة المعالج لاستهلاك أقل.
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  setCpuFrequencyMhz(160);
  Serial.setTxBufferSize(16384);  // مخزن كبير لإرسال الإطارات عبر USB
  Serial.setRxBufferSize(512);
  Serial.begin(115200);
  Serial.setDebugOutput(false);   // كان true: رسائل النظام تغطي رسائلنا
  Serial.println();
  // سبب آخر إعادة تشغيل: إن كان هبوط تيار فالمشكلة في التغذية لا في الكود
  esp_reset_reason_t why = esp_reset_reason();
  if (why == ESP_RST_BROWNOUT)
    Serial.println("!!! LAST RESTART: BROWNOUT (power too weak) - use a 5V 2A adapter or a better USB port/cable");
  else
    Serial.printf("Last restart reason: %d\n", (int)why);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_VGA;      // 640x480: اليد البعيدة أوضح (وضع الكابل ينزل إلى QVGA)
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  if (psramFound()) {
    config.jpeg_quality = 16;                // ضغط أعلى = إطار أصغر = إرسال أسرع (أقل تأخيراً)
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;   // دائماً أحدث إطار: يقلل التأخير
  } else {
    config.frame_size = FRAMESIZE_SVGA;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  camCfg = config;
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x - restarting\n", err);
    delay(3000);
    ESP.restart();
  }

  // الواي فاي: 640x480 لتُرى اليد من نحو مترين (QVGA كانت تكفي لمتر واحد تقريباً).
  // ضغط أعلى قليلاً (18) يُبقي الإطار صغيراً فيبقى البث سلساً.
  tuneSensor(FRAMESIZE_VGA, 18);

#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  // ننتظر 3 ثوانٍ طلب وضع الكابل من التطبيق قبل تشغيل الواي فاي (أكبر مستهلك للتيار):
  // في وضع الكابل لا يعمل الواي فاي أبداً، فلا هبوط في الجهد
  Serial.println("Waiting 3s for USB mode (WALLCAM)...");
  for (uint32_t t0 = millis(); millis() - t0 < 3000 && !usbMode; delay(10)) pollUsbCommands();
  if (usbMode) return;

  wifiStarted = true;
  if (!ALWAYS_SETUP_FIRST) loadCreds();     // وضع "الإعداد أولاً": لا شبكة محفوظة
  prepareWiFi();
  doScan();                      // يملأ قائمة الشبكات ويحدد قناة شبكتك المحفوظة

  // 1) الاتصال بالشبكة المحفوظة   2) وإلا: وضع الإعداد (شبكة خاصة بالكاميرا)
  // شبكتك المحفوظة غير ظاهرة الآن (مكان آخر)؟ لا داعي لانتظار محاولات الاتصال: انتقل لوضع الإعداد فوراً
  if (sta_ssid.length() && !foundChannel) {
    Serial.println("Saved WiFi not in range: setup mode");
    inStaMode = false;
  } else {
    inStaMode = connectToWiFi();
  }
  if (!inStaMode) {
    startAp();
    startDns();
  }

  startCameraServer();
  startConfigServer();
  startMdns();

  String IP = currentIp();
  Serial.println();
  Serial.println("=========================================");
  if (inStaMode) {
    Serial.println("Connected to: " + sta_ssid);
    Serial.printf("Signal: %d dBm\n", WiFi.RSSI());
  } else {
    Serial.println("SETUP MODE. WiFi: " + String(ap_ssid) + "  password: " + String(ap_password));
  }
  Serial.println("Camera Web:  http://" + IP);
  Serial.println("Stream URL:  http://" + IP + ":81/stream");
  Serial.println("Setup page:  http://" + IP + ":" + String(CFG_PORT));
  Serial.println("=========================================");
}

void loop() {
  static uint32_t lastReconnect = 0;
  static uint32_t lastRetry = millis();

  pollUsbCommands();
  if (usbMode) {
    if (millis() - usbKeepAt > 3000) ESP.restart();   // أُغلق التطبيق: عودة للوضع العادي (الواي فاي)
    usbStreamFrame();
    return;
  }

  cfgServer.handleClient();
  handleDns();

  if (pendingRestart && (int32_t)(millis() - restartAt) >= 0) ESP.restart();

  if (pendingConnect && (int32_t)(millis() - connectAt) >= 0) {
    pendingConnect = false;
    WiFi.mode(WIFI_AP_STA);              // نقطة الوصول تعمل وحدها عادة؛ نحتاج STA هنا فقط
    doScan();                            // يحدد قناة الشبكة المطلوبة
    if (foundChannel) {
      WiFi.begin(foundSsid.c_str(), sta_pass.c_str(), foundChannel, foundBssid);
      uint32_t t0 = millis();
      while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { cfgServer.handleClient(); delay(100); }
    }
    if (WiFi.status() == WL_CONNECTED) {
      WiFi.softAPdisconnect(true);       // الاتصال نجح: أوقف شبكة الإعداد
      WiFi.mode(WIFI_STA);
      WiFi.setAutoReconnect(true);
      inStaMode = true;
      MDNS.end();
      startMdns();
      Serial.println("Connected to: " + sta_ssid + "  IP " + WiFi.localIP().toString());
    } else {
      Serial.println("Could not connect to " + sta_ssid + ": staying in setup mode");
      WiFi.disconnect(false);
      sta_ssid = "";                     // لا نعيد المحاولة بشبكة خاطئة
      doScan();
    }
  }

  uint32_t now = millis();
  if (inStaMode) {
    // انقطعت الشبكة: أعد الاتصال
    if (WiFi.status() != WL_CONNECTED && now - lastReconnect > 5000) {
      lastReconnect = now;
      Serial.println("WiFi lost, reconnecting...");
      WiFi.reconnect();
    }
  } else if (sta_ssid.length() && WiFi.softAPgetStationNum() == 0 && now - lastRetry > 60000) {
    // الفحص والاتصال يقطعان شبكة الإعداد لثوانٍ، لذا لا نجرّب إلا عند رؤية شبكتك المحفوظة، ولا أحد متصل
    lastRetry = now;
    doScan();
    if (foundChannel) tryStaFromAp();
  }
  delay(2);
}
