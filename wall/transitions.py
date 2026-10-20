"""Bounded particle dissolve between supplied slide representations."""
import numpy as np
import pygame

class ParticleTransition:
    def __init__(self, old, new, old_bg, new_bg, duration=1.4):
        self.old,self.new = old.copy(),new.copy()
        self.old_bg,self.new_bg = np.array(old_bg,float),np.array(new_bg,float)
        self.duration = duration
        self.size = old.get_size()
        a,ac = self._sample(old,old_bg)
        b,bc = self._sample(new,new_bg)
        n = max(len(a),len(b))
        if n:
            ia = np.linspace(0,len(a)-1,n).astype(int)
            ib = np.linspace(0,len(b)-1,n).astype(int)
            self.a,self.b,self.ac,self.bc = a[ia],b[ib],ac[ia],bc[ib]
        else:
            self.a=self.b=np.empty((0,2));self.ac=self.bc=np.empty((0,3))
        rng = np.random.default_rng(7)
        self.arc = rng.uniform(-1,1,(n,2))*np.array(self.size)*.12

    @staticmethod
    def _sample(surface,bg):
        rgb = pygame.surfarray.array3d(surface)
        w,h = surface.get_size()
        step = max(4,int((w*h/18000)**.5))
        x,y = np.meshgrid(np.arange(step//2,w,step),np.arange(step//2,h,step))
        pos = np.column_stack((x.ravel(),y.ravel()))
        colors = rgb[pos[:,0],pos[:,1]]
        # Text and image colors, avoiding most background/panel pixels.
        keep = np.linalg.norm(colors.astype(float)-np.array(bg),axis=1)>65
        pos,colors = pos[keep],colors[keep]
        if not len(pos):
            pos=np.array([[w/2,h/2]]);colors=np.array([bg])
        order=np.lexsort((pos[:,1],pos[:,0]))
        if len(order)>1400:
            order=order[np.linspace(0,len(order)-1,1400).astype(int)]
        return pos[order].astype(float),colors[order].astype(float)

    def render(self, screen, elapsed):
        p=float(np.clip(elapsed/self.duration,0,1))
        if p<=0:
            self.old.set_alpha(255);screen.blit(self.old,(0,0));return
        if p>=1:
            self.new.set_alpha(255);screen.blit(self.new,(0,0));return
        e=p*p*(3-2*p)
        if getattr(self, "background", None) is not None:
            screen.blit(self.background, (0, 0))
        else:
            screen.fill(tuple((self.old_bg*(1-e)+self.new_bg*e).astype(int)))
        old_alpha=max(0,1-p/.32)
        new_alpha=max(0,(p-.68)/.32)
        if old_alpha:
            self.old.set_alpha(round(255*old_alpha));screen.blit(self.old,(0,0))
        if new_alpha:
            self.new.set_alpha(round(255*new_alpha));screen.blit(self.new,(0,0))
        pos=self.a*(1-e)+self.b*e+self.arc*np.sin(np.pi*p)
        color=self.ac*(1-e)+self.bc*e
        opacity=min(1,p/.15,(1-p)/.15)
        layer=pygame.Surface(self.size,pygame.SRCALPHA)
        for xy,c in zip(pos,color):
            pygame.draw.circle(layer,(*c.astype(int),int(220*opacity)),xy.astype(int),3)
        screen.blit(layer,(0,0))
