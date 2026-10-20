"""Extend thin joining strokes while leaving letter bodies unchanged.
If a word has no suitable joining stroke, use a modest whole-word stretch.
This is a visual effect, not a linguistic kashida justification engine.
"""
import numpy as np
import pygame


def stretch_ink(surface, factor):
    w,h=surface.get_size()
    factor=float(np.clip(factor,1.0,2.2))
    extra=int(round(w*(factor-1)))
    if extra<=0:return surface
    alpha=pygame.surfarray.array_alpha(surface)>100
    ink=alpha.sum(1)
    lower=alpha[:,int(h*.35):int(h*.9)].any(1)
    candidates=(ink>=2)&(ink<=max(3,h*.19))&lower
    candidates[:max(1,int(w*.15))]=False
    candidates[int(w*.85):]=False
    runs=[];start=None
    for x,ok in enumerate(np.r_[candidates,False]):
        if ok and start is None:start=x
        if not ok and start is not None:
            if x-start>=3:runs.append((start,x))
            start=None
    if not runs:
        return pygame.transform.smoothscale(surface,(w+extra,h))
    # Choose up to three thin connectors, never insert blank gaps.
    seams=sorted((a+(b-a)//2 for a,b in sorted(runs,key=lambda r:r[1]-r[0],reverse=True)[:3]))
    out=pygame.Surface((w+extra,h),pygame.SRCALPHA)
    prev=cursor=0
    for k,x in enumerate(seams):
        out.blit(surface,(cursor,0),(prev,0,x-prev,h));cursor+=x-prev
        add=extra//len(seams)+(1 if k<extra%len(seams) else 0)
        strip=pygame.transform.smoothscale(surface.subsurface((x,0,1,h)),(1+add,h))
        out.blit(strip,(cursor,0));cursor+=1+add;prev=x+1
    out.blit(surface,(cursor,0),(prev,0,w-prev,h))
    return out
