import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1'
import unittest
from types import SimpleNamespace as NS
import numpy as np
import pygame
from wall.living import LivingMotion,ElasticWords,sample_path,Wave
from wall.particles import ParticleField
from wall.transitions import ParticleTransition
from wall.elastic_ink import stretch_ink
from wall import config


def make_field(words=False):
    return ParticleField([NS(ch='word' if words else 'A',x=200+i*80,y=240,adv=120 if words else 30)
                          for i in range(5)],(800,600),entrance=False)


def hand(slot,x,y=240,closed=False,vx=0,vy=0):
    return NS(slot=slot,dx=x,dy=y,x=x,y=y,closed=closed,vx=vx,vy=vy,caught=0)


class MotionTests(unittest.TestCase):
    def test_path_arc_length(self):
        p=sample_path([(0,0),(0,0),(10,0),(10,10)],[0,5,15,100])
        np.testing.assert_allclose(p,[(10,10),(10,5),(5,0),(0,0)])

    def test_slow_brush_pulls_to_path(self):
        f=make_field();m=LivingMotion();h=hand(1,200,200,vx=70)
        for i in range(10):
            h.dx=170+i*4;m.update(i*.03,[h],f)
        force=m.forces(f,[h],.3,1)
        self.assertLess(force[0,1],0)
        self.assertTrue(np.isfinite(force).all())

    def test_still_palm_returns_to_order(self):
        f=make_field();m=LivingMotion();h=hand(1,200,200,vx=0)
        for i in range(10):m.update(i*.03,[h],f)
        np.testing.assert_allclose(m.forces(f,[h],.3,1),0)

    def test_waves_are_bounded_and_expire(self):
        f=make_field();m=LivingMotion();h=hand(1,200,vx=1000)
        for i in range(100):m.update(i*.3,[h],f)
        self.assertGreater(len(m.waves),0);self.assertLessEqual(len(m.waves),6)
        m.update(40,[],f)
        self.assertFalse(m.paths);self.assertFalse(m.waves)
        self.assertFalse(m.last_wave)

    def test_image_open_push_closed_pull(self):
        f=make_field();m=LivingMotion();h=hand(1,150,vx=200)
        self.assertGreater(m.forces(f,[h],0,1,image=True)[0,0],0)
        h.closed=True
        self.assertLess(m.forces(f,[h],0,1,image=True)[0,0],0)

    def test_ribbon_assigns_free_followers(self):
        f=make_field();f.held[0]=1;m=LivingMotion();h=hand(1,200,closed=True,vx=150)
        for i in range(60):
            h.dx=200+i*4;m.update(i*.016,[h],f)
        self.assertGreater(len(m.ribbon_ids[1]),0)
        force=m.forces(f,[h],1,1)
        np.testing.assert_allclose(force[0],0)
        self.assertTrue(np.any(np.abs(force[1:])>0))
        self.assertLessEqual(np.linalg.norm(force,axis=1).max(),config.MAX_EFFECT_FORCE+.01)

    def test_contour_calm_attraction(self):
        f=make_field();m=LivingMotion();mask=np.zeros((60,80),np.float32)
        mask[10:50,30:50]=1
        gy,gx=np.gradient(mask)
        m.update_contour((mask,gx,gy),1,(800,600))
        self.assertIsNotNone(m.contour)
        force=m.forces(f,[],3,1)
        self.assertGreater(force[0,0],0)
        m.update_contour(None,4,(800,600));self.assertIsNone(m.contour)

    def test_stretch_and_release(self):
        f=make_field(words=True);f.held[0]=1
        a,b=hand(1,140,closed=True),hand(2,260,closed=True)
        e=ElasticWords();e.update(f,[a,b],.016,True)
        self.assertEqual(e.index,0)
        a.dx=90;b.dx=310
        for _ in range(30):e.update(f,[a,b],.016,True)
        self.assertGreater(e.stretch[0],1.7)
        self.assertEqual(e.center,(200,240))
        b.closed=False;e.update(f,[a,b],.016,True)
        self.assertIsNone(e.index);self.assertEqual(f.held[0],1)
        for _ in range(100):e.update(f,[a,b],.016,True)
        self.assertAlmostEqual(float(e.stretch[0]),1,places=2)

    def test_open_hands_cannot_stretch(self):
        f=make_field(words=True);e=ElasticWords();f.held[0]=1
        e.update(f,[hand(1,140),hand(2,260)],.016,True)
        self.assertIsNone(e.index)

    def test_lost_both_hands_ends_stretch(self):
        f=make_field(words=True);e=ElasticWords();f.held[0]=1
        e.update(f,[hand(1,140,closed=True),hand(2,260,closed=True)],.016,True)
        e.update(f,[],.016,True)
        self.assertIsNone(e.index);self.assertEqual(f.held[0],-1)
        np.testing.assert_array_equal(f.vel[0],0)


class VisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):pygame.init();pygame.display.set_mode((320,240))
    @classmethod
    def tearDownClass(cls):pygame.quit()

    def test_transition_endpoints_and_budget(self):
        a=pygame.Surface((320,240));a.fill((0,0,0));pygame.draw.circle(a,(220,100,20),(80,120),30)
        b=pygame.Surface((320,240));b.fill((10,20,30));pygame.draw.rect(b,(30,220,200),(180,70,60,100))
        effect=ParticleTransition(a,b,(0,0,0),(10,20,30))
        dst=a.copy();effect.render(dst,0)
        np.testing.assert_array_equal(pygame.surfarray.array3d(dst),pygame.surfarray.array3d(a))
        effect.render(dst,.7);self.assertLessEqual(len(effect.a),1500)
        effect.render(dst,effect.duration)
        np.testing.assert_array_equal(pygame.surfarray.array3d(dst),pygame.surfarray.array3d(b))

    def test_ink_connector_preserves_letter_bodies(self):
        s=pygame.Surface((100,50),pygame.SRCALPHA)
        pygame.draw.rect(s,(255,255,255),(0,10,25,30))
        pygame.draw.rect(s,(255,255,255),(75,10,25,30))
        pygame.draw.line(s,(255,255,255),(25,30),(75,30),4)
        out=stretch_ink(s,2)
        self.assertEqual(out.get_size(),(200,50))
        arr=pygame.surfarray.array_alpha(out)
        np.testing.assert_array_equal(arr[:25],pygame.surfarray.array_alpha(s)[:25])
        np.testing.assert_array_equal(arr[-25:],pygame.surfarray.array_alpha(s)[-25:])
        self.assertTrue((arr[:,30]>0).all())

if __name__=='__main__':unittest.main()
