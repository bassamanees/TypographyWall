"""Real native Tasks inference with bundled models. Requires Ubuntu libgles2."""
import unittest
import numpy as np
from wall.landmarkers import HandsTask, PoseTask

class TasksTests(unittest.TestCase):
    def test_hand_video_and_crop_modes(self):
        rgb=np.zeros((240,320,3),np.uint8)
        for static in (True,False):
            with HandsTask(static_image_mode=static) as task:
                result=task.process(rgb)
                first=task.last_ms
                result=task.process(rgb)
                self.assertEqual(result.multi_hand_landmarks,[])
                if not static:self.assertGreater(task.last_ms,first)

    def test_pose_video_and_mask_contract(self):
        with PoseTask() as task:
            rgb=np.zeros((240,320,3),np.uint8)
            first=task.process(rgb)
            result=task.process(rgb)
            self.assertIsNone(result.pose_landmarks)
            self.assertIsNone(result.segmentation_mask)

if __name__=='__main__':unittest.main()
