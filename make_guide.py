"""
توليد ملفات الدليل الصوتي من wall/guide_texts.py بصوت عربي طبيعي (Microsoft Edge TTS).
يحتاج إنترنت مرة واحدة فقط وقت التوليد؛ التطبيق نفسه يعمل بعدها بلا إنترنت.

    pip install edge-tts
    python tools/make_guide.py                       # كل النصوص
    python tools/make_guide.py 0_images intro        # نصوص محددة فقط
    python tools/make_guide.py --voice ar-IQ-RanaNeural

أصوات عربية مقترحة: ar-IQ-BasselNeural (عراقي، رجل — الافتراضي)، ar-IQ-RanaNeural (عراقية، امرأة)،
ar-SA-HamedNeural، ar-EG-SalmaNeural. الناتج: assets/audio/guide/<المفتاح>.mp3
"""

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wall.guide_texts import GUIDE  # noqa: E402

OUT = os.path.join(ROOT, "assets", "audio", "guide")


async def main():
    import edge_tts

    p = argparse.ArgumentParser()
    p.add_argument("keys", nargs="*", help="مفاتيح النصوص (افتراضياً الكل)")
    p.add_argument("--voice", default="ar-IQ-BasselNeural")
    p.add_argument("--rate", default="-6%", help="سرعة القراءة (هادئة كأدلّة المتاحف)")
    a = p.parse_args()
    os.makedirs(OUT, exist_ok=True)
    keys = a.keys or list(GUIDE)
    for k in keys:
        path = os.path.join(OUT, k + ".mp3")
        for attempt in range(3):           # الخدمة تتعلّق أحياناً: مهلة ثم إعادة محاولة
            try:
                await asyncio.wait_for(edge_tts.Communicate(GUIDE[k], a.voice, rate=a.rate).save(path), 90)
                break
            except Exception as e:
                print(f"  {k}: retry ({type(e).__name__})", flush=True)
        else:
            raise SystemExit(f"failed: {k}")
        print(f"  {k}.mp3  {os.path.getsize(path) // 1024} KB", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
