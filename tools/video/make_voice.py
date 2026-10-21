"""توليد صوت التعليق لكل مشهد (edge-tts، نفس صوت الدليل) ← <out>/<id>.mp3"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from narration import SCENES  # noqa: E402

VOICE = "ar-IQ-BasselNeural"


async def main(out, only=None):
    import edge_tts
    os.makedirs(out, exist_ok=True)
    for key, text in SCENES:
        path = os.path.join(out, key + ".mp3")
        if only and key not in only:
            continue
        if not only and os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        for _ in range(4):
            try:
                await asyncio.wait_for(edge_tts.Communicate(text, VOICE, rate="-4%").save(path), 120)
                break
            except Exception as e:
                print(f"  {key}: retry ({type(e).__name__})", flush=True)
        else:
            raise SystemExit(f"failed: {key}")
        print(f"  {key}.mp3  {os.path.getsize(path) // 1024} KB", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
