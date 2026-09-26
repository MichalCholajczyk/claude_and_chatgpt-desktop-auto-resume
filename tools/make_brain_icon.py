"""Draw assets/brain.png: a perfectly smooth brain (no wrinkles), for the launcher's
"My brain has no wrinkles left" tile. Needs Pillow; run once: py tools/make_brain_icon.py
"""
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 320           # output, px (the launcher scales it down)
SS = 4               # supersampling for smooth edges
W = SIZE * SS

PINK_TOP = (250, 190, 205)
PINK_BOTTOM = (226, 128, 158)
CEREBELLUM = (214, 112, 144)
STEM = (206, 104, 136)
OUTLINE = (150, 58, 92)


def box(cx, cy, rx, ry):
    return [(cx - rx) * W, (cy - ry) * W, (cx + rx) * W, (cy + ry) * W]


def ellipse_mask(shapes):
    mask = Image.new("L", (W, W), 0)
    draw = ImageDraw.Draw(mask)
    for shape in shapes:
        draw.ellipse(box(*shape), fill=255)
    return mask


def gradient(top, bottom, y0, y1):
    """Vertical gradient between two colours over the band y0..y1 (fractions)."""
    img = Image.new("RGB", (W, W), top)
    draw = ImageDraw.Draw(img)
    a, b = int(y0 * W), int(y1 * W)
    for y in range(W):
        t = min(1.0, max(0.0, (y - a) / max(1, b - a)))
        draw.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def outlined(layer_rgb, mask, width):
    """Paint the shape with a darker rim around it."""
    rim = mask.filter(ImageFilter.MaxFilter(width * 2 + 1))
    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (W, W), OUTLINE + (255,)), (0, 0), rim)
    out.paste(layer_rgb.convert("RGBA"), (0, 0), mask)
    return out


def main():
    canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # Brain stem and cerebellum sit behind the cerebrum.
    stem = Image.new("L", (W, W), 0)
    ImageDraw.Draw(stem).rounded_rectangle([0.505 * W, 0.58 * W, 0.595 * W, 0.86 * W], radius=0.045 * W, fill=255)
    canvas.alpha_composite(outlined(Image.new("RGB", (W, W), STEM), stem, 10))
    cerebellum = ellipse_mask([(0.69, 0.665, 0.155, 0.10)])
    canvas.alpha_composite(outlined(gradient((232, 140, 170), CEREBELLUM, 0.58, 0.76), cerebellum, 10))

    # Cerebrum: overlapping ellipses melt into one smooth, fold-free silhouette.
    cerebrum = ellipse_mask([
        (0.50, 0.44, 0.40, 0.27),    # body
        (0.27, 0.49, 0.19, 0.19),    # frontal lobe
        (0.74, 0.45, 0.18, 0.20),    # occipital lobe
        (0.48, 0.31, 0.30, 0.17),    # crown
        (0.45, 0.58, 0.25, 0.115),   # temporal lobe
    ]).filter(ImageFilter.GaussianBlur(W * 0.012)).point(lambda v: 255 if v > 128 else 0)
    canvas.alpha_composite(outlined(gradient(PINK_TOP, PINK_BOTTOM, 0.18, 0.70), cerebrum, 11))

    # Gloss: this brain is so smooth it shines.
    gloss = Image.new("L", (W, W), 0)
    ImageDraw.Draw(gloss).ellipse(box(0.36, 0.265, 0.17, 0.065), fill=150)
    gloss = gloss.filter(ImageFilter.GaussianBlur(W * 0.02))
    gloss = ImageChops.multiply(gloss, cerebrum)
    canvas.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (W, W), (255, 255, 255)).split(), gloss)))
    spark = Image.new("L", (W, W), 0)
    ImageDraw.Draw(spark).ellipse(box(0.265, 0.27, 0.028, 0.02), fill=235)
    spark = spark.filter(ImageFilter.GaussianBlur(W * 0.004))
    canvas.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (W, W), (255, 255, 255)).split(), spark)))

    out = canvas.resize((SIZE, SIZE), Image.LANCZOS)
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "brain.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path, optimize=True)
    print("wrote", path)


if __name__ == "__main__":
    main()
