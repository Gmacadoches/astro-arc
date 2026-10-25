#version 440
// Astro-Arc moving background: depth parallax over a still wallpaper.
//
// A virtual camera drifts on a slow Lissajous path, and every pixel shifts by
// that drift times its depth, so near things slide and the far background
// stays put. Lengths are in reference pixels (pixels of a 1080-line screen),
// so the look is the same at any resolution. Times are in seconds.
//
// After `qsb --qt6 -o shaders/parallax.frag.qsb shaders/parallax.frag`, commit
// the .qsb too: the plugin ships the baked shader, not a build step.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
  mat4 qt_Matrix;
  float qt_Opacity;
  float time;
  float screenAspect;   // item width / height
  float imageAspect;    // wallpaper width / height
  float parallaxPx;     // shift between nearest and farthest, ref px
  float parallaxPeriod; // seconds for the x axis; y runs 1.37 times slower
  float overscan;       // fraction of each edge hidden by zoom, covers the reach
};

layout(binding = 1) uniform sampler2D source;
layout(binding = 2) uniform sampler2D depth;

const float TAU = 6.2831853;

void main() {
  // Cover the screen the way Omarchy's own background does (PreserveAspectCrop),
  // so with parallaxPx and overscan at 0 this is the same picture.
  vec2 crop = screenAspect > imageAspect
    ? vec2(1.0, imageAspect / screenAspect)
    : vec2(screenAspect / imageAspect, 1.0);
  vec2 base = 0.5 + (qt_TexCoord0 - 0.5) * crop * (1.0 - 2.0 * overscan);

  // Reference px to uv, on the image's visible height.
  vec2 pxToUv = vec2(1.0 / (1080.0 * screenAspect), 1.0 / 1080.0) * crop;

  vec2 cam = vec2(sin(TAU * time / parallaxPeriod),
                  sin(TAU * time / (parallaxPeriod * 1.37) + 1.3)) * vec2(1.0, 0.6);
  vec2 camUv = cam * parallaxPx * pxToUv;

  // Backward warp: find the source point whose shifted position lands here.
  // Three fixed-point steps on the blurred depth converge for shifts this small.
  vec2 p = base;
  for (int i = 0; i < 3; i++) {
    p = base - camUv * texture(depth, p).r;
  }

  fragColor = vec4(texture(source, p).rgb, 1.0) * qt_Opacity;
}
