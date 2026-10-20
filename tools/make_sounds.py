"""
توليد المؤثرات الصوتية وموسيقى الخلفية برمجياً (بلا ملفات خارجية ولا حقوق نشر).

    python tools/make_sounds.py

الناتج في assets/audio:
  sfx/*.wav     مؤثرات الحركة (تقليب، إمساك، رمي، تفعيل)
  music/ambient.wav   موسيقى خلفية هادئة بطابع رافديني (مقام حجاز على ري):
                      دندنة قرار، قيثارة (كنّارة) سومرية، دفّ، ونفخ يشبه الناي.
                      الحلقة متصلة: نهايتها تلتحم ببدايتها بلا قطع.
"""

import os
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "audio")
SR = 44100
MUSIC_SR = 22050
rng = np.random.default_rng(7)

# مقام حجاز على ري: D Eb F# G A Bb C
HIJAZ = [0, 1, 4, 5, 7, 8, 10]
D3 = 146.83


def note(degree, octave=0, base=D3):
    o, d = divmod(degree, 7)
    return base * 2 ** ((HIJAZ[d] + 12 * (o + octave)) / 12)


def save(path, data, sr=SR):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = np.asarray(data, np.float32)
    if data.ndim == 1:
        data = data[:, None]
    peak = float(np.max(np.abs(data))) or 1.0
    pcm = np.empty(data.shape, "<i2")
    for s in range(0, len(data), 1 << 16):           # على أجزاء لتوفير الذاكرة
        pcm[s: s + (1 << 16)] = data[s: s + (1 << 16)] * (0.89 * 32767 / peak)
    with wave.open(path, "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    print(f"  {os.path.relpath(path, ROOT)}  {len(data) / sr:.1f}s")


def t_axis(sec, sr=SR):
    return np.arange(int(sec * sr)) / sr


def lowpass(x, cutoff, sr=SR):
    """مرشّح تمرير منخفض عبر FFT (ناعم الحافة)."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    X = np.fft.rfft(x, axis=0)
    g = 1 / (1 + (f / cutoff) ** 4)
    return np.fft.irfft(X * (g[:, None] if X.ndim > 1 else g), n, axis=0)


def bandpass(x, lo, hi, sr=SR):
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    g = 1 / (1 + (lo / np.maximum(f, 1)) ** 4) / (1 + (f / hi) ** 4)
    return np.fft.irfft(np.fft.rfft(x) * g, n)


def reverb(x, sec=2.2, mix=0.3, sr=SR, seed=1):
    """صدى قاعة حجرية: التفاف مع استجابة نبضية من ضجيج متضائل."""
    r = np.random.default_rng(seed)
    n = int(sec * sr)
    tt = np.arange(n) / sr
    stereo = x.ndim > 1
    chans = x.shape[1] if stereo else 1
    xs = x if stereo else x[:, None]
    out = np.zeros((len(xs) + n - 1, chans), np.float32)
    for c in range(chans):
        ir = r.standard_normal(n) * np.exp(-tt * 6.9 / sec)
        ir = lowpass(ir, 4200, sr)
        ir[: int(0.012 * sr)] = 0          # تأخير أولي
        ir /= np.sqrt(np.sum(ir ** 2))
        # التفاف بالتجزئة (overlap-add) حتى لا يحتاج ذاكرة كبيرة
        block = 1 << 15
        size = 1 << (block + n - 2).bit_length()
        IR = np.fft.rfft(ir, size)
        for s in range(0, len(xs), block):
            seg = xs[s: s + block, c]
            y = np.fft.irfft(np.fft.rfft(seg, size) * IR, size)[: len(seg) + n - 1]
            out[s: s + len(y), c] += y * mix
        out[: len(xs), c] += xs[:, c] * (1 - mix)
    return out if stereo else out[:, 0]


def env(n, attack, release, sr=SR):
    e = np.ones(n)
    a, r = int(attack * sr), int(release * sr)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def lyre(freq, sec=2.5, bright=1.0, sr=SR):
    """وتر مقروص (كنّارة): توافقيات تخفت العليا منها أسرع."""
    t = t_axis(sec, sr)
    y = np.zeros_like(t)
    for k in range(1, 12):
        f = freq * k * (1 + 0.0004 * k * k)          # شيء من عدم التوافق كالأوتار الحقيقية
        if f > sr / 2.2:
            break
        amp = (1 / k ** 1.1) * (bright if k > 3 else 1.0) * abs(np.sin(np.pi * k * 0.23))
        y += amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * np.exp(-t * (1.6 + 0.9 * k))
    pluck = rng.standard_normal(int(0.006 * sr)) * np.linspace(0.3, 0, int(0.006 * sr))
    y[: len(pluck)] += pluck
    return y * env(len(t), 0.002, 0.05, sr)


def bell(freq, sec=3.0, sr=SR):
    """جرس برونزي (صنج معبد): توافقيات غير منتظمة."""
    t = t_axis(sec, sr)
    y = np.zeros_like(t)
    for ratio, amp, decay in [(1, 1, 1.1), (2.76, .5, 1.9), (5.4, .25, 3.2), (8.93, .12, 5), (0.5, .3, .8)]:
        y += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t * decay)
    return y * env(len(t), 0.003, 0.1, sr)


def drum(kind, sr=SR):
    """دفّ: dum منخفض أو tek حاد."""
    if kind == "dum":
        t = t_axis(0.6, sr)
        f = 70 + 60 * np.exp(-t * 25)
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 7)
        y += 0.25 * lowpass(rng.standard_normal(len(t)), 900, sr) * np.exp(-t * 30)
    else:
        t = t_axis(0.18, sr)
        y = bandpass(rng.standard_normal(len(t)), 1800, 7000, sr) * np.exp(-t * 45)
        y += 0.3 * np.sin(2 * np.pi * 420 * t) * np.exp(-t * 40)
    return y


def ney(freq, sec, sr=SR):
    """نفخ يشبه الناي: نغمة مع اهتزاز بطيء ونَفَس."""
    t = t_axis(sec, sr)
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.0 * t) * np.clip(t / 0.8, 0, 1)
    ph = 2 * np.pi * freq * np.cumsum(vib) / sr
    y = np.sin(ph) + 0.35 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
    breath = bandpass(rng.standard_normal(len(t)), freq * 1.5, freq * 6, sr) * 0.35
    y = y + breath
    return y * env(len(t), min(0.6, sec * 0.3), min(1.0, sec * 0.4), sr)


def noise_sweep(sec, f0, f1, sr=SR):
    """هبّة هواء: ضجيج يمر عبر مرشّح يتحرك تردده."""
    n = int(sec * sr)
    x = rng.standard_normal(n)
    out = np.zeros(n)
    hop = 512
    for i in range(0, n, hop):
        seg = x[max(0, i - hop): i + 2 * hop]
        fc = f0 + (f1 - f0) * (i / n)
        y = bandpass(seg, fc * 0.6, fc * 1.6, sr)
        a = i - max(0, i - hop)
        out[i: i + hop] = y[a: a + hop][: len(out[i: i + hop])]
    return out


# ----------------------------------------------------------------------
# المؤثرات
# ----------------------------------------------------------------------
def stone_door(sec, forward):
    """باب حجري ينزلق: هدير منخفض مع احتكاك."""
    t = t_axis(sec)
    rumble = lowpass(rng.standard_normal(len(t)), 160)
    grind = bandpass(rng.standard_normal(len(t)), 300, 1400)
    grind *= 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 7 * t + np.sin(2 * np.pi * 1.3 * t)))
    shape = np.sin(np.pi * np.clip(t / sec, 0, 1)) ** 0.8
    return (rumble * 1.4 + grind * 0.35) * shape


def make_sfx():
    d = os.path.join(OUT, "sfx")
    # التقليب: باب حجري ثم جرس (صاعد للأمام، هابط للخلف)
    for name, forward in (("slide_next", True), ("slide_prev", False)):
        door = stone_door(1.3, forward)
        y = np.zeros(int(3.6 * SR))
        y[: len(door)] += door * 0.5
        whoosh = noise_sweep(0.9, 400 if forward else 2400, 2400 if forward else 400) * np.hanning(int(0.9 * SR))
        y[int(0.1 * SR): int(0.1 * SR) + len(whoosh)] += whoosh * 0.35
        notes = [note(0, 1), note(4, 1)] if forward else [note(4, 1), note(0, 1)]
        for i, f in enumerate(notes):
            b = bell(f, 2.4) * (0.5 if i == 0 else 0.4)
            s = int((0.75 + i * 0.22) * SR)
            y[s: s + len(b)] += b[: len(y) - s]
        save(os.path.join(d, name + ".wav"), reverb(y, 2.4, 0.35))

    # إمساك حرف: نقرة قيثارة على درجات المقام (خمس نسخ تتناوب فلا يتكرر الصوت)
    for i, deg in enumerate([0, 2, 4, 5, 7]):
        y = lyre(note(deg, 1), 1.4, bright=0.8)
        tap = lowpass(rng.standard_normal(int(0.03 * SR)), 1500) * np.exp(-t_axis(0.03) * 120)
        y[: len(tap)] += tap * 0.6
        save(os.path.join(d, f"grab_{i + 1}.wav"), reverb(y, 1.2, 0.2))

    # إمساك قطعة من صورة: طرقة حجر/فخار
    for i, f in enumerate([380, 470, 560]):
        t = t_axis(0.5)
        y = bandpass(rng.standard_normal(len(t)), f, f * 4) * np.exp(-t * 38)
        y += 0.8 * np.sin(2 * np.pi * f * 0.5 * t) * np.exp(-t * 22)
        save(os.path.join(d, f"grab_stone_{i + 1}.wav"), reverb(y, 0.9, 0.18))

    # إفلات/رمي: هبّة هواء قصيرة
    sec = 0.55
    y = noise_sweep(sec, 2600, 500) * np.hanning(int(sec * SR)) ** 1.5
    save(os.path.join(d, "release.wav"), reverb(y, 1.0, 0.2))

    # بداية التفاعل: انزلاق على أوتار القيثارة صعوداً
    y = np.zeros(int(3.2 * SR))
    for i, deg in enumerate([0, 2, 4, 5, 7, 9]):
        p = lyre(note(deg, 1), 2.2, bright=0.7) * (0.55 + 0.08 * i)
        s = int(i * 0.075 * SR)
        y[s: s + len(p)] += p[: len(y) - s]
    save(os.path.join(d, "awaken.wav"), reverb(y, 2.5, 0.35))

    # تشغيل/إيقاف الدليل الصوتي
    for name, notes in (("guide_on", [note(0, 1), note(4, 1)]), ("guide_off", [note(4, 1), note(0, 1)])):
        y = np.zeros(int(2.0 * SR))
        for i, f in enumerate(notes):
            b = bell(f, 1.6) * 0.5
            s = int(i * 0.16 * SR)
            y[s: s + len(b)] += b[: len(y) - s]
        save(os.path.join(d, name + ".wav"), reverb(y, 1.5, 0.3))


# ----------------------------------------------------------------------
# موسيقى الخلفية
# ----------------------------------------------------------------------
def make_music(length=64.0):
    sr = MUSIC_SR
    tail = 6.0
    n = int((length + tail) * sr)
    mix = np.zeros((n, 2), np.float32)

    def place(sig, at, gain=1.0, pan=0.0):
        s = int(at * sr) % int(length * sr)
        sig = sig[: n - s]
        left, right = np.sqrt((1 - pan) / 2), np.sqrt((1 + pan) / 2)
        mix[s: s + len(sig), 0] += sig * gain * left
        mix[s: s + len(sig), 1] += sig * gain * right

    L = int(length * sr)
    t = t_axis(length, sr)
    # دندنة القرار (ري وصول) تتنفس ببطء. الترددات مقرّبة لتكتمل دوراتها داخل الحلقة
    # فتلتحم النهاية بالبداية بلا نقرة
    swell = 0.75 + 0.25 * np.sin(2 * np.pi * t / (length / 4))
    drone = np.zeros_like(t)
    for f, a in ((note(0, -1), 1.0), (note(4, -1), 0.5), (note(0, 0), 0.35)):
        for k, ak in ((1, 1), (2, .45), (3, .25), (4, .12)):
            fk = round(f * k * length) / length
            drone += a * ak * np.sin(2 * np.pi * fk * t + k)
    drone *= swell
    mix[:L, 0] += drone * 0.11
    mix[:L, 1] += drone * 0.11

    # قيثارة: جمل قصيرة على المقام (أرقام الدرجات، None = سكتة)
    beat = 60 / 66
    phrases = [
        [0, 1, 2, None, 1, 0, None, None],
        [4, 3, 2, 1, 2, None, 0, None],
        [0, 2, 4, 5, 4, None, 2, 1],
        [5, 4, 2, None, 1, 2, 0, None],
        [7, 5, 4, None, 2, 1, 0, None],
        [None, 4, None, 2, 1, None, 0, None],
        [0, 1, 2, 4, 2, 1, 0, None],
        [None, None, 4, 2, None, 1, 0, None],
    ]
    at = 0.0
    while at < length:
        for p in phrases:
            for deg in p:
                if deg is not None and at < length:
                    place(lyre(note(deg), 2.6, bright=0.9, sr=sr), at + rng.uniform(-0.01, 0.01),
                          0.32 * rng.uniform(0.75, 1.0), pan=rng.uniform(-0.35, 0.1))
                at += beat
            at += beat * 2           # نَفَس بين الجمل
            if at >= length:
                break

    # دفّ: إيقاع بطيء هادئ (دُم … تك تك)
    pattern = [("dum", 0), ("tek", 1.5), ("tek", 2), ("dum", 3), ("tek", 3.5)]
    bar = beat * 4
    b = 0.0
    while b < length:
        for kind, off in pattern:
            place(drum(kind, sr), b + off * beat, 0.22 if kind == "dum" else 0.07, pan=0.25)
        b += bar

    # ناي: جملتان طويلتان فقط في الحلقة كلها
    for at, seq in ((14.0, [(4, 3.0), (5, 1.5), (4, 1.5), (2, 4.0)]),
                    (44.0, [(7, 2.5), (5, 1.5), (4, 2.0), (1, 1.5), (0, 4.5)])):
        for deg, dur in seq:
            place(ney(note(deg, 1), dur + 0.6, sr), at, 0.10, pan=-0.2)
            at += dur

    mix = reverb(mix, 3.2, 0.42, sr, seed=3)
    loop = mix[:L].copy()
    rest = mix[L:]
    loop[: len(rest)] += rest[:L]     # الذيل يلتف إلى البداية فتلتحم الحلقة
    save(os.path.join(OUT, "music", "ambient.wav"), loop, sr)


if __name__ == "__main__":
    print("Sound effects:")
    make_sfx()
    print("Background music:")
    make_music()
