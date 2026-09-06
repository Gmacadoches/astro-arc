#!/usr/bin/env python3
"""Astro-Arc's image generation step (Phase 3): renders one prompt into an
image using a pinned, local Stable Diffusion checkpoint. CPU-only — this is
a batch job that runs at most once a day, so a couple of minutes here is
fine in exchange for zero cost and full offline control over the style.

Pinned model: stabilityai/sd-turbo. It's a distilled SD variant built for
1-4 step inference, which matters far more than usual right now: while
we're iterating on the style/prompt itself we want each test render to
come back in seconds, not minutes. Swappable later — once the symbolic
prompt side is dialed in, nothing else in the pipeline cares which
checkpoint this loads, just that it stays pinned so the look doesn't drift.

First run downloads the model into ~/.local/share/omarchy/astro-arc/models
(a few GB); every run after that is fully offline.

Usage:
    image_gen.py "<prompt>" <output_path> [--seed N] [--steps N]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from image_fit import fit_cover  # noqa: E402

MODEL_ID = "stabilityai/sd-turbo"
CACHE_DIR = Path.home() / ".local/share/omarchy/astro-arc/models"

_pipe = None


def get_pipeline():
    global _pipe
    if _pipe is None:
        # Imported lazily: these are heavy (torch et al.), and other
        # scripts in bin/ that don't need image generation (astro_engine.py,
        # symbol_map.py) shouldn't pay that import cost.
        import torch
        from diffusers import AutoPipelineForText2Image

        _pipe = AutoPipelineForText2Image.from_pretrained(
            MODEL_ID, torch_dtype=torch.float32, cache_dir=str(CACHE_DIR)
        )
        _pipe.to("cpu")
    return _pipe


def _fit_to_clip_budget(pipe, prompt, max_tokens=77):
    """CLIP (this pipeline's text encoder) silently truncates at 77 tokens
    — diffusers only warns on stderr, it doesn't error — so a too-long
    prompt fails quiet and ugly rather than loud. This mattered less when
    prompts came from the old fixed-vocabulary symbol_map.py (kept
    deliberately short); llm_pipeline.py's Stage 2 output (25-50 words)
    plus the configured style suffix routinely runs well past 77 CLIP
    tokens. Trim from the end — the style suffix is the most expendable,
    constant part — rather than let the real per-reading imagery get
    silently cut instead, backwards from what matters.
    """
    tokenizer = getattr(pipe, "tokenizer", None)
    if tokenizer is None:
        return prompt
    token_count = len(tokenizer(prompt)["input_ids"])
    if token_count <= max_tokens:
        return prompt

    parts = prompt.split(", ")
    while len(parts) > 1 and len(tokenizer(", ".join(parts))["input_ids"]) > max_tokens:
        parts.pop()
    trimmed = ", ".join(parts)
    print(
        f"Warning: prompt was {token_count} CLIP tokens (limit {max_tokens}); "
        f"trimmed to {len(tokenizer(trimmed)['input_ids'])}.",
        file=sys.stderr,
    )
    return trimmed


def _native_render_size(target_width, target_height, max_edge=640, min_edge=384):
    """sd-turbo is CPU-bound and trained around ~512px — rendering directly
    at an arbitrary desktop resolution (a 3440x1440 ultrawide, say) would be
    slow and well outside what it was trained to compose at. Render at a
    bounded size that still matches the target's aspect ratio, then
    fit_cover() below scales/crops to the real target afterward. Rounded to
    a multiple of 8: required by the SD U-Net's downsampling."""
    ratio = target_width / target_height
    if ratio >= 1:
        w, h = max_edge, max(min_edge, round(max_edge / ratio))
    else:
        h, w = max_edge, max(min_edge, round(max_edge * ratio))
    return (w // 8) * 8, (h // 8) * 8


def generate(prompt, output_path, seed=None, steps=2, target_width=1920, target_height=1080):
    import torch

    pipe = get_pipeline()
    prompt = _fit_to_clip_budget(pipe, prompt)
    generator = torch.Generator("cpu")
    if seed is not None:
        generator = generator.manual_seed(seed)

    render_w, render_h = _native_render_size(target_width, target_height)

    # sd-turbo is trained for guidance-free single/few-step sampling —
    # guidance_scale must stay 0.0, it isn't a "weaker CFG" knob here.
    image = pipe(
        prompt=prompt,
        num_inference_steps=steps,
        guidance_scale=0.0,
        width=render_w,
        height=render_h,
        generator=generator,
    ).images[0]

    fitted = fit_cover(image.convert("RGB"), target_width, target_height)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate one Astro-Arc image.")
    parser.add_argument("prompt")
    parser.add_argument("output_path")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--cost-out", help="write {cost, costSource, usage} JSON here for astro-arc-generate's cost log")
    args = parser.parse_args()

    start = time.time()
    out = generate(args.prompt, args.output_path, args.seed, args.steps, args.width, args.height)
    elapsed = time.time() - start
    print(f"Saved {out} in {elapsed:.1f}s", file=sys.stderr)

    if args.cost_out:
        # Local generation is always free — written in the same shape as
        # openai_image_gen.py's --cost-out so astro-arc-generate can read
        # either backend's result the same way.
        Path(args.cost_out).write_text(json.dumps({"cost": 0.0, "costSource": "local-free", "usage": None}, indent=2))


if __name__ == "__main__":
    main()
