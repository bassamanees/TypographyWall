"""
الصوت: مؤثرات الحركة، موسيقى الخلفية، والدليل الصوتي.

الملفات في assets/audio (تُولَّد بـ tools/make_sounds.py وtools/make_guide.py):
  sfx/<اسم>.wav أو <اسم>_1.wav، <اسم>_2.wav …  (النسخ المتعددة تتناوب فلا يتكرر الصوت نفسه)
  music/ambient.wav                            حلقة متصلة
  guide/<مفتاح>.mp3                            نصوص wall/guide_texts.py

إن لم يتوفر جهاز صوت أو ملف ما يعمل التطبيق صامتاً بلا أخطاء.
"""

import glob
import os
import random
import time

import pygame

from . import config

ROOT = os.path.dirname(os.path.dirname(__file__))
AUDIO_DIR = os.path.join(ROOT, "assets", "audio")


class Audio:
    def __init__(self, sound=True, guide=True):
        self.ok = False
        self.sound = sound            # كتم/تشغيل كل الأصوات
        self.guide = guide            # الدليل الصوتي
        self._sfx = {}
        self._turn = {}
        self._last = {}
        self._voice_cache = {}
        self._music_level = 0.0
        self._music = False
        self.voice_key = None
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(44100, -16, 2, 1024)
            pygame.mixer.set_num_channels(20)
            # القناة 0 للدليل، والباقي للمؤثرات نختارها بأنفسنا
            # (find_channel في pygame-ce لا يحترم set_reserved فيقطع كلام الدليل)
            self.voice = pygame.mixer.Channel(0)
            self._fx = [pygame.mixer.Channel(i) for i in range(1, 20)]
            self._fx_next = 0
            self.ok = True
        except Exception as e:             # لا جهاز صوت
            print("audio disabled:", e)
            return
        for path in glob.glob(os.path.join(AUDIO_DIR, "sfx", "*.wav")):
            name = os.path.splitext(os.path.basename(path))[0]
            base = name.rsplit("_", 1)[0] if name.rsplit("_", 1)[-1].isdigit() else name
            try:
                self._sfx.setdefault(base, []).append(pygame.mixer.Sound(path))
            except Exception as e:
                print("audio: cannot load", path, e)
        music = os.path.join(AUDIO_DIR, "music", "ambient.wav")
        if os.path.exists(music):
            try:
                pygame.mixer.music.load(music)
                pygame.mixer.music.set_volume(0.0)
                pygame.mixer.music.play(-1, fade_ms=0)
                self._music = True
            except Exception as e:
                print("audio: cannot play music", e)

    # ------------------------------------------------------------------
    def play(self, name, volume=1.0, min_gap=0.08):
        """مؤثر صوتي؛ min_gap يمنع تكدّس الصوت نفسه في لحظة واحدة."""
        if not (self.ok and self.sound):
            return
        sounds = self._sfx.get(name)
        if not sounds:
            return
        now = time.monotonic()
        if now - self._last.get(name, -1e9) < min_gap:
            return
        self._last[name] = now
        # نسخة عشوائية غير التي سبقتها
        i = random.choice([k for k in range(len(sounds)) if k != self._turn.get(name)] or [0])
        self._turn[name] = i
        # قناة مؤثرات فارغة، وإلا الأقدم بالتناوب
        ch = next((c for c in self._fx if not c.get_busy()), None)
        if ch is None:
            ch = self._fx[self._fx_next]
            self._fx_next = (self._fx_next + 1) % len(self._fx)
        ch.set_volume(max(0.0, min(1.0, volume * config.SFX_VOLUME)))
        ch.play(sounds[i])

    # ------------------------------------------------------------------
    def has_voice(self, key):
        return os.path.exists(os.path.join(AUDIO_DIR, "guide", key + ".mp3"))

    def narrate(self, key):
        """يشغّل نص الدليل الصوتي (ويوقف ما قبله). يرجع False إن لم يوجد ملفه."""
        if not (self.ok and self.sound and self.guide):
            return False
        snd = self._voice_cache.get(key)
        if snd is None:
            path = os.path.join(AUDIO_DIR, "guide", key + ".mp3")
            if not os.path.exists(path):
                return False
            try:
                snd = self._voice_cache[key] = pygame.mixer.Sound(path)
            except Exception as e:
                print("audio: cannot load", path, e)
                return False
        self.voice.set_volume(config.GUIDE_VOLUME)
        self.voice.play(snd, fade_ms=150)
        self.voice_key = key
        return True

    def stop_voice(self, fade_ms=500):
        if self.ok and self.voice.get_busy():
            self.voice.fadeout(fade_ms)
        self.voice_key = None

    @property
    def speaking(self):
        return self.ok and self.voice.get_busy()

    # ------------------------------------------------------------------
    def update(self, dt):
        """تخفيض الموسيقى (ducking) أثناء كلام الدليل، وكتمها عند إيقاف الصوت."""
        if not (self.ok and self._music):
            return
        target = config.MUSIC_VOLUME if self.sound else 0.0
        if self.speaking:
            target *= config.MUSIC_DUCK
        rate = 3.0 if target < self._music_level else 0.8
        self._music_level += (target - self._music_level) * min(1.0, dt * rate)
        pygame.mixer.music.set_volume(self._music_level)

    def set_sound(self, on):
        self.sound = on
        if not on:
            self.stop_voice(200)
            if self.ok:
                for ch in self._fx:
                    ch.fadeout(200)
