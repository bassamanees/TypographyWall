"""Headless integration tests: real pygame/Pillow/OpenCV, no webcam required."""
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1'
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import pygame
import numpy as np
from wall.app import App
from wall.tracking import TrackState, CameraTracker
from wall import settings, config
from wall.layout import build_layout

class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.setting=patch.object(settings,'PATH',Path(cls.temp.name)/'settings.json');cls.setting.start()
        cls.app=App(use_camera=False)

    @classmethod
    def tearDownClass(cls):
        pygame.quit();cls.setting.stop();cls.temp.cleanup()

    def setUp(self):
        self.app._reset_hands()
        self.app.transition=self.app._pending=None
        self.app.word_mode=False
        self.app.slide=0;self.app._load_item(entrance=False)
        self.app.activity=1;self.app.hand_gain=1

    def test_all_slides_render(self):
        a=self.app
        for slide in range(a.n_slides):
            a.slide=slide;a._load_item(entrance=False)
            a.update(1/60,TrackState(stamp=time.monotonic()))
            a.draw()
            self.assertGreater(a.field.n,0)
            self.assertTrue(np.isfinite(a.field.pos).all())

    def test_whole_word_layout(self):
        a=self.app
        a.word_mode=True;a._load_item(entrance=False);a.draw()
        self.assertTrue(any(len(ch)>1 for ch in a.field.chars))
        expected=len(a.page['items'][0]['text'].split())
        self.assertEqual(a.field.n,expected)

    def test_preordered_text_uses_basic_layout(self):
        from PIL import ImageFont
        font=self.app.fonts.primary.get(32,400)
        self.assertEqual(font.layout_engine,ImageFont.Layout.BASIC)

    def test_particle_transition_lifecycle(self):
        a=self.app
        a.go_slide(1);a.draw()
        self.assertIsNotNone(a.transition)
        for _ in range(90):
            a.update(1/60,TrackState(stamp=time.monotonic()))
            a.draw()
        self.assertIsNone(a.transition)
        self.assertEqual(a.slide,1)
        self.assertTrue(np.isfinite(a.field.pos).all())

    def test_mouse_two_hand_word_stretch(self):
        a=self.app
        a.word_mode=True;a._load_item(entrance=False)
        a.activity=1;a.hand_gain=1;a.near=True
        i=2;x,y=a.field.pos[i];w=a.field.widths[i]
        W,H=a.size
        def frame(left,right=None):
            hands=[(left/W,y/H,0,'left',1,.1)]
            if right is not None:hands.append((right/W,y/H,0,'right',1,.1))
            a.update(1/30,TrackState(person=True,proximity=.4,hands=hands,stamp=a.t+1))
        for _ in range(5):frame(x-w*.45)
        self.assertTrue((a.field.held>=0).any())
        for _ in range(8):frame(x-w*.45,x+w*.45)
        self.assertEqual(a.elastic.index,i)
        for k in range(30):frame(x-w*.45-k*1.5,x+w*.45+k*1.5)
        self.assertGreater(a.elastic.stretch[i],1.1)
        self.assertIsNone(a._pending)
        a.draw()

    def test_blank_text(self):
        self.assertEqual(build_layout('  ',self.app.fonts,800,600).glyphs,[])

    def test_lost_hand_releases_without_throw(self):
        a=self.app
        x,y=a.field.pos[0]
        hand=(x/a.size[0],y/a.size[1],0,'left',1,0.1)
        a._update_hands(1/30,TrackState(stamp=1,hands=[hand]),*a.size)
        self.assertTrue((a.field.held>=0).any())
        a.t+=config.HAND_LOST_TIMEOUT+.01
        a._update_hands(1/30,TrackState(stamp=2),*a.size)
        self.assertFalse((a.field.held>=0).any())
        self.assertFalse(a.hand_filters)
        np.testing.assert_array_equal(a.field.vel,0)

    def test_missing_hand_does_not_start_grab(self):
        a=self.app
        hand=(.5,.5,1,'left',0,0.1)
        a._update_hands(1/30,TrackState(stamp=1,hands=[hand]),*a.size)
        h=next(iter(a.hand_filters.values()));h.grip=1
        a.t+=config.HAND_FRESH_TIMEOUT+.01
        a._update_hands(1/30,TrackState(stamp=2),*a.size)
        self.assertFalse(h.closed)

    def test_stale_camera_results(self):
        t=CameraTracker()
        t._hands.update(hands=[(.5,.5,1,'left',0,.1)],stamp=time.monotonic()-2,ok=True)
        state=t.snapshot()
        self.assertEqual(state.hands,[]);self.assertFalse(state.person)

    def test_resize_clears_old_coordinates(self):
        a=self.app
        a._update_hands(1/30,TrackState(stamp=1,hands=[(.5,.5,1,'left',0,.1)]),*a.size)
        a._resize(a.size)
        self.assertFalse(a.hand_filters)


class GestureTests(unittest.TestCase):
    def test_spread_gesture_and_stale_rejection(self):
        from collections import deque
        from types import SimpleNamespace
        from wall.interaction import InteractionMixin
        class Harness(InteractionMixin):
            def go_slide(self,target,style):self.result=(target,style)
        a=Harness();a.t=0;a.hand_filters={};a._holds={};a.transition=a._pending=None
        a.activity=1;a._span_hist=deque();a._gesture_ready_at=0
        a._last_gesture=(-1e9,0);a.slide=0;a.result=None
        for t,l,r in [(0,400,600),(.15,320,680),(.30,240,760)]:
            a.t=t
            a.hand_filters={1:SimpleNamespace(x=l,vx=-600,closed=False,grip=0,seen_n=5,seen=t),
                            2:SimpleNamespace(x=r,vx=600,closed=False,grip=0,seen_n=5,seen=t)}
            a._two_hand_gesture(1000)
        self.assertEqual(a.result,(1,'open'))
        a.result=None;a.t+=.2
        a._two_hand_gesture(1000)
        self.assertIsNone(a.result);self.assertFalse(a._span_hist)

if __name__=='__main__':unittest.main()
