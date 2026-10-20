"""MediaPipe Tasks API adapters for Python 3.14.

The rest of the app receives plain landmark lists. No mp.solutions dependency.
Each tracker owns its detector and monotonic VIDEO timestamps. Cropped images
use IMAGE mode because their coordinate systems can change between frames.
"""
from pathlib import Path
from types import SimpleNamespace as Result
import time
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

MODELS = Path(__file__).resolve().parent.parent / 'models'


def model_path(name):
    path = MODELS / name
    if not path.is_file():
        raise FileNotFoundError(f'Missing model: {path}. Extract the entire project ZIP.')
    return str(path)


class _Detector:
    def _image(self, rgb):
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

    def _detect(self, rgb):
        image = self._image(rgb)
        if self.static:
            return self.detector.detect(image)
        self.last_ms = max(self.last_ms + 1, time.monotonic_ns() // 1_000_000)
        return self.detector.detect_for_video(image, self.last_ms)

    def close(self):
        self.detector.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class HandsTask(_Detector):
    def __init__(self, max_num_hands=2, static_image_mode=False,
                 min_detection_confidence=.5, min_tracking_confidence=.5):
        self.static = static_image_mode
        self.last_ms = -1
        options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path('hand_landmarker.task'),
                                     delegate=BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.IMAGE if self.static else vision.RunningMode.VIDEO,
            num_hands=max_num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self.detector = vision.HandLandmarker.create_from_options(options)

    def process(self, rgb):
        result = self._detect(rgb)
        return Result(
            multi_hand_landmarks=[Result(landmark=p) for p in result.hand_landmarks],
            multi_handedness=[Result(classification=[Result(label=c.category_name) for c in hand])
                              for hand in result.handedness],
        )


class PoseTask(_Detector):
    def __init__(self, min_detection_confidence=.5, min_tracking_confidence=.5):
        self.static = False
        self.last_ms = -1
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path('pose_landmarker_full.task'),
                                     delegate=BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=min_detection_confidence,
            min_pose_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_segmentation_masks=True,
        )
        self.detector = vision.PoseLandmarker.create_from_options(options)

    def process(self, rgb):
        result = self._detect(rgb)
        mask = result.segmentation_masks[0].numpy_view().copy() if result.segmentation_masks else None
        if mask is not None and mask.ndim == 3 and mask.shape[-1] == 1:
            mask = mask[..., 0]
        return Result(
            pose_landmarks=Result(landmark=result.pose_landmarks[0]) if result.pose_landmarks else None,
            segmentation_mask=mask,
        )
