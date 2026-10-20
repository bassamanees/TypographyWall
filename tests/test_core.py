import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from wall import settings, config
from wall.filters import OneEuro, HandFilter
from wall.particles import ParticleField
from wall.timing import FixedStepper

def field():
    return ParticleField([SimpleNamespace(ch='A', x=120, y=90)], (800, 600), entrance=False)

class CoreTests(unittest.TestCase):
    def test_invalid_settings(self):
        for value in [[], None, 42, 'bad']:
            self.assertEqual(settings.validate(value), {})
        self.assertEqual(settings.validate({'slide':'oops','calibrated':True,'near_threshold':float('nan')}), {})
        self.assertEqual(settings.validate({'calibrated':True}), {})

    def test_atomic_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as td, patch.object(settings, 'PATH', Path(td)/'settings.json'):
            self.assertTrue(settings.save({'slide':4, 'word_mode':True}))
            self.assertEqual(settings.load(), {'slide':4,'word_mode':True})
            Path(settings.PATH).write_text('{broken')
            with self.assertLogs('wall.settings', level='ERROR'):
                self.assertEqual(settings.load(), {})

    def test_gentle_release_is_zero(self):
        f=field();self.assertEqual(f.catch(1,120,90,20,1),1)
        f.release(1,fling=0)
        np.testing.assert_array_equal(f.vel, [[0,0]])
        self.assertEqual(f.held[0],-1)

    def test_throw_clamped(self):
        f=field();f.catch(1,120,90,20,1);f.release(1,1e6,1e6)
        self.assertLessEqual(np.linalg.norm(f.vel[0]), config.MAX_THROW_SPEED+0.001)

    def test_exclusive_grab(self):
        f=field();f.catch(1,120,90,20,1)
        self.assertEqual(f.catch(2,120,90,20,1),0)

    def test_physics_fps_independence(self):
        outputs=[]
        for fps in (30,60,144):
            f=field();f.pos[0]+=[100,40];stepper=FixedStepper();t=0
            def tick(dt):
                nonlocal t
                t+=dt;f.step(dt,t,0,0,[],None)
            for _ in range(fps*2):stepper.advance(1/fps,tick)
            outputs.append(f.pos.copy())
        for value in outputs[1:]:np.testing.assert_allclose(value,outputs[0],atol=1e-5)

    def test_stall_budget(self):
        s=FixedStepper();steps=[]
        self.assertEqual(s.advance(10,steps.append),12)
        self.assertGreater(s.dropped_time,9)
        self.assertLess(s.remainder,s.step)

    def test_velocity_constant_motion(self):
        h=HandFilter(1000,0)
        for i in range(60):h.update(i*10,0,1,1/60)
        self.assertAlmostEqual(h.vx,600,delta=2)
        self.assertAlmostEqual(h.vy,0)

    def test_one_euro_raw_derivative(self):
        f=OneEuro(1,0)
        f(0,.1);f(10,.1);f(10,.1)
        self.assertEqual(f.raw,10)
        self.assertLess(f.dx,40)


class RecorderTests(unittest.TestCase):
    def test_video_is_readable(self):
        import cv2
        from wall.recorder import Recorder
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/'sample.mp4')
            r=Recorder(path,(64,48),30)
            self.assertTrue(r.ok)
            for i in range(6):
                r.push(np.full((48,64,3),i*30,np.uint8).tobytes(),(64,48))
            self.assertTrue(r.close())
            cap=cv2.VideoCapture(path)
            self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),6)
            ok,frame=cap.read();cap.release()
            self.assertTrue(ok);self.assertEqual(frame.shape,(48,64,3))

    def test_worker_failure_does_not_deadlock_close(self):
        from wall.recorder import Recorder
        with tempfile.TemporaryDirectory() as td:
            r=Recorder(str(Path(td)/'broken.mp4'),(64,48),30)
            with self.assertLogs('wall.recorder',level='ERROR'):
                r.push(b'bad bytes',(64,48))
                self.assertFalse(r.close())
            self.assertFalse(r._thread.is_alive())

if __name__=='__main__':unittest.main()
