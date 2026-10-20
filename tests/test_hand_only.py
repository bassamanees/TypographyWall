"""Regression tests for hand-only tracking and window resize handling."""
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import numpy as np
import pygame
from wall.tracking import CameraTracker
from wall.app import App
from wall.clay import make_tablet

class HandOnlyTests(unittest.TestCase):
    def test_single_detector_and_no_pose(self):
        tracker=CameraTracker();tracker._running=True
        class Grabber:
            error=None
            def wait_newer(self,last):
                tracker._running=False
                return 1,np.zeros((480,640,3),np.uint8),time.monotonic()
        tracker.grabber=Grabber()
        model=MagicMock()
        model.__enter__.return_value=model
        model.process.return_value=SimpleNamespace(multi_hand_landmarks=[])
        detectors=SimpleNamespace(HandsTask=MagicMock(return_value=model),PoseTask=MagicMock())
        tracker._hand_loop(detectors)
        detectors.HandsTask.assert_called_once()
        detectors.PoseTask.assert_not_called()
        model.process.assert_called_once()
        self.assertTrue(tracker._hands['ok'])
        self.assertEqual(tracker._hands['hands'],[])

    def test_resize_does_not_recreate_window(self):
        pygame.init();surface=pygame.display.set_mode((640,480))
        app=SimpleNamespace(fullscreen=False,size=(600,400),_resize=MagicMock())
        event=pygame.event.Event(pygame.VIDEORESIZE,w=640,h=480)
        with patch('pygame.event.get',return_value=[event]),patch('pygame.display.set_mode') as recreate:
            self.assertTrue(App._handle_events(app))
            recreate.assert_not_called()
            app._resize.assert_called_once_with((640,480))
        pygame.quit()

    def test_tablet_is_deterministic_and_textured(self):
        pygame.init();pygame.display.set_mode((640,480))
        a=pygame.surfarray.array3d(make_tablet((640,480)))
        b=pygame.surfarray.array3d(make_tablet((640,480)))
        self.assertTrue(np.array_equal(a,b))
        self.assertGreater(float(a.std()),5)
        pygame.quit()
