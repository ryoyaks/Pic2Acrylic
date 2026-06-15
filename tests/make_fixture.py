"""Generate a synthetic transparent character + mask so stage 1 can be self-tested
without any real assets.

  python tests/make_fixture.py        # writes parts/char.png + parts/char_mask.png
  python prep_masks.py parts -o parts_prep
"""

import pathlib

import numpy as np
from PIL import Image, ImageDraw

out = pathlib.Path("parts")
out.mkdir(exist_ok=True)

img = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse([180, 120, 420, 360], fill=(255, 224, 189, 255))                      # head
d.rounded_rectangle([210, 340, 390, 720], radius=60, fill=(80, 140, 230, 255))  # body
d.rounded_rectangle([150, 360, 230, 640], radius=40, fill=(80, 140, 230, 255))  # left arm
d.rounded_rectangle([370, 360, 450, 640], radius=40, fill=(80, 140, 230, 255))  # right arm
d.rounded_rectangle([250, 700, 300, 860], radius=30, fill=(40, 40, 60, 255))    # left leg
d.rounded_rectangle([300, 700, 350, 860], radius=30, fill=(40, 40, 60, 255))    # right leg
img.save(out / "char.png")

a = np.array(img)[..., 3]
m = np.zeros((*a.shape, 4), np.uint8)
m[a > 10] = 255
Image.fromarray(m).save(out / "char_mask.png")

print("fixture ->", out.resolve())
