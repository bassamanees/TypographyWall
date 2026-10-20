"""Deterministic clay texture, built once per window size (never per frame)."""
import numpy as np
import pygame


def make_tablet(size):
    w, h = size
    rng = np.random.default_rng(2400)
    # Texture at half resolution: soft uneven clay, fine grains and worn edges.
    sw, sh = max(1, w//2), max(1, h//2)
    y, x = np.mgrid[0:sh, 0:sw]
    nx, ny = x/max(sw-1, 1), y/max(sh-1, 1)
    edge = np.minimum.reduce([nx, 1-nx, ny, 1-ny])
    grain = rng.normal(0, 1.7, (sh, sw))
    uneven = 2.8*np.sin(nx*19+ny*4) + 1.6*np.sin(ny*42+nx*7)
    light = 8*(1-ny)-4*nx - 32*np.exp(-edge*75)
    tone = grain+uneven+light
    rgb = np.clip(np.array([178, 131, 88])+tone[..., None], 0, 255).astype('uint8')
    small = pygame.surfarray.make_surface(np.ascontiguousarray(rgb.transpose(1, 0, 2)))
    bg = pygame.transform.smoothscale(small, size).convert()
    # Shallow hairline cracks at the rim, leaving the writing area uncluttered.
    for _ in range(24):
        left = rng.random() < .5
        px = int(rng.uniform(6, w*.035)) if left else int(rng.uniform(w*.965, w-6))
        py = int(rng.uniform(12, max(13, h-12)))
        points = [(px, py)]
        for j in range(4):
            px += int(rng.integers(3, 12)) * (1 if left else -1)
            py += int(rng.integers(-7, 8))
            points.append((px, py))
        pygame.draw.lines(bg, (132, 88, 56), False, points, 1)
    pygame.draw.rect(bg, (129, 83, 50), bg.get_rect().inflate(-8, -8), 2, border_radius=18)
    pygame.draw.rect(bg, (210, 164, 116), bg.get_rect().inflate(-14, -14), 1, border_radius=16)
    return bg
