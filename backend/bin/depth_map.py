"""Depth map for the moving background: Depth Anything V2 Small (ONNX) -> 8-bit PNG, white = near.

usage: depth_map.py MODEL.onnx IMAGE OUT.png

Unlike every other script here this one needs packages beyond the standard
library: numpy, pillow and onnxruntime. astro-arc-motion picks an interpreter
that has them, and the whole step is optional, so a missing package means a
still wallpaper and nothing else. Exits 3 when a package is missing.
"""
import os
import sys

try:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageFilter
except ImportError as e:
    print(f"depth_map.py: {e}", file=sys.stderr)
    sys.exit(3)

# The shader only shifts pixels by a few dozen at most and the map is blurred
# anyway, so a 1280-wide map holds everything the full-size one would.
MAX_WIDTH = 1280


def main():
    model, src, out = sys.argv[1:4]
    img = Image.open(src).convert("RGB")
    W, H = img.size
    # The ViT patch is 14 px; keep the short side at 518 like the reference preprocessing.
    h = 518
    w = int(round(W / H * h / 14)) * 14
    x = np.asarray(img.resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    x = x.transpose(2, 0, 1)[None].astype(np.float32)

    opts = ort.SessionOptions()
    # A wallpaper is not urgent; leave cores for whatever the user is doing.
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
    sess = ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    disp = sess.run(None, {name: x})[0].squeeze()  # relative inverse depth, larger = nearer

    lo, hi = np.percentile(disp, [2, 98])
    d = np.clip((disp - lo) / max(hi - lo, 1e-6), 0, 1)
    ow = min(W, MAX_WIDTH)
    oh = max(1, round(H * ow / W))
    dimg = Image.fromarray((d * 255).astype(np.uint8)).resize((ow, oh), Image.BICUBIC)
    # Grow the near side of every edge, then blur. Without the grow, the blur
    # straddles the silhouette and the shift smears the rim of a figure into
    # streaks; with it the figure moves whole and the stretch lands on the
    # background just outside it, spread over a wider blur where it reads as
    # soft rather than torn. Both scale with the map: 11 px and 5 px at 1280.
    r = max(1, round(ow / 256))
    dimg = dimg.filter(ImageFilter.MaxFilter(2 * r + 1)).filter(ImageFilter.GaussianBlur(radius=r))
    # Saved as RGB because the shader reads the red channel, and RGB is what
    # the motion prototype's textures were checked with.
    tmp = out + ".tmp"
    dimg.convert("RGB").save(tmp, format="PNG")
    os.replace(tmp, out)


if __name__ == "__main__":
    main()
