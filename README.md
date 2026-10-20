# Living Typography Wall — v3

Read **ابدأ_هنا.txt** for Arabic instructions and the full gesture guide.

## Run in PyCharm (Python 3.14)

1. Extract the ZIP and open the `p1` folder in PyCharm.
2. Create a new project virtual environment with **Python 3.14** as its base interpreter.
   Do not reuse the previous Python 3.12 environment.
3. In PyCharm's terminal, with that environment activated, run:

```bash
python --version
python -m pip install -r requirements.txt
python main.py
```

The version command must show Python 3.14.x. Run `main.py` using the same
interpreter in your run configuration. `start.py` also uses this interpreter;
it does not install or switch Python versions.

Ubuntu terminal alternative, from this folder:

```bash
python3.14 -m venv .venv314
source .venv314/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Select `.venv314/bin/python` in PyCharm if you created this environment in the terminal.
If venv is unavailable, install Ubuntu's `python3-venv` package.
If MediaPipe reports missing libGLESv2 or libEGL, run `sudo apt install libgles2 libegl1`.

Mouse preview: `python main.py --mouse`. Fullscreen: `python main.py --fullscreen`.
Models are bundled in `models`; no runtime model download is needed.
This is source code, not a standalone executable. Install only the OpenCV contrib
package and pygame-ce listed in requirements; avoid installing opencv-python or
pygame alongside them because they share import names.

## Clay + hand-only update

- One persistent hand detector, at most two hands; no body detector or segmentation runs.
- Camera requests 640x480, inference capped at 24 FPS; rendering remains at 60 FPS.
- Latest-frame capture and existing One Euro filtering smooth hand motion without a frame queue.
- SDL handles window resizing without repeatedly recreating the window.
- Camera preview is embedded in the main window, hidden by default; press C to toggle.
- Cached clay surface and recessed lettering for Arabic and cuneiform; image slides remain available.
- Pinch to grab, open to release, two pinches to stretch a word, open hands to change slides.
- Body silhouette effects are inactive because this build only detects hands.
- The clay surface is redrawn cleanly each frame; legacy trail toggling is disabled.

## Simple map of the code

`start.py` launches with the selected interpreter; `main.py` starts the application. Change content in
`wall/texts.py`, effect values in `wall/config.py`, and new creative behavior in
`wall/living.py`. Rendering, tracking, physics, and interaction are kept separate
so one change does not require editing one very large script.

## Verify

```bash
python -m unittest discover -s tests -v
```

39 tests passed under Python 3.14.8. See TEST_RESULTS_CLAY.txt for this revision's results. No live webcam or
Ubuntu window manager is available here: actual laptop FPS and the reported
window flicker must still be checked on your machine. The resize regression is
covered by a test that forbids recreating the display in a resize handler.

## Libraries and assets

| Library | Version | Purpose |
|---|---|---|
| MediaPipe | 1.1.0 | Hands/Pose Tasks API |
| OpenCV contrib | 5.0.0.93 | Camera, contours, video encoding |
| NumPy | 2.5.4 | Particle math |
| pygame-ce | 2.5.8 | Display and input (imported as pygame) |
| Pillow | 12.3.0 | Fonts and images |
| arabic-reshaper | 3.0.1 | Connected Arabic forms |
| python-bidi | 0.6.11 | Visual RTL ordering |

`wall/landmarkers.py` adapts the MediaPipe Tasks API to the existing interaction
code. Models, their sources, and license are in `models`.

Bundled Noto Arabic and cuneiform fonts include their SIL Open Font Licenses in
`assets/fonts`. Fonts: https://github.com/google/fonts/tree/main/ofl/notosansarabic
and https://github.com/google/fonts/tree/main/ofl/notosanscuneiform . Original image
attributions remain in `assets/mesopotamia/credits.json`. Default settings/logs/
recordings/screenshots are project-local; `WALL_DATA_DIR` can relocate them.

Recording captures the artwork, not audio. Fixed-FPS encoding can shorten playback
if frames are dropped. Camera permissions and OS libraries are still required.
The original project PDF and historical README are retained for reference; this
README and the Arabic guide supersede earlier feature/setup instructions.
