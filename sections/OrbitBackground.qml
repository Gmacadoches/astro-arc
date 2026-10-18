import QtQuick
import QtQuick.Effects
import QtQuick.Shapes

// The solar system behind every view of the panel (the "Solar-system
// background" frame of the redesign canvas). Panel.qml makes exactly one, as
// the bottom layer of the card, so it never moves, rescales or restarts when
// the view on top changes: the scene is a fixed-size item pinned to the
// card's top-left, and the card's size only moves the clip.
//
// The sun sits where the main view's ✦ is. Six orbits share one shape
// (eccentricity 0.75, the sun at a focus, the long axis at 52° toward the
// bottom-right) at growing scale, so they never cross. Opening the panel
// bursts them in; after that the system only sways. Closing stops every
// animation; the next open bursts again from the start.
//
// Every shape is built once. Only transforms and opacity animate: positions
// along the orbits come from PathInterpolator, everything else is rotation,
// scale and opacity. Nothing repaints per frame. The 45 bodies on orbits step
// on a 30 Hz clock (orbitTime); the burst, comet, sway and stars animate per
// frame.
Item {
  id: root

  // The panel is open. Animations run only while it is.
  property bool active: false
  // Off draws the scene still, in its settled state.
  property bool animated: true
  property color accent
  property color foreground
  // The sun, in this item's coordinates.
  property real sunX: 27
  property real sunY: 36
  // Pixels per design pixel (the shell's spacing scale), so the system grows
  // with the panel. The scene is laid out in the frame's own pixels.
  property real unit: 1
  // The card's corner radius; anything but 0 clips through a mask.
  property real radius: 0

  // How fast the orbits and the comet go. One knob, not a setting.
  readonly property real timeScale: 1.0

  readonly property bool running: active && animated

  // Planets, asteroids and moons step on this clock (ms since the panel
  // opened) at 30 Hz rather than on per-frame animations. They move well
  // under a pixel a step, and at 60 Hz the 45 of them cost twice the CPU.
  property real orbitTime: 0
  Timer {
    id: orbitClock
    property real startedAt: 0
    interval: 33
    repeat: true
    running: root.running
    onRunningChanged: if (running) { startedAt = Date.now(); root.orbitTime = 0 }
    onTriggered: root.orbitTime = Date.now() - startedAt
  }

  // Puts everything in its settled state (animations off).
  signal settle()
  onAnimatedChanged: if (!animated) settle()
  onActiveChanged: if (!animated) settle()
  onSettle: orbitTime = 0

  function tint(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
  readonly property color lineColor: tint(accent, 0.18)
  readonly property color planetColor: tint(accent, 0.75)
  readonly property color starColor: tint(foreground, 0.7)

  readonly property real ecc: 0.75
  function period(a) { return 16000 * Math.pow(a / 58, 1.5) / timeScale }

  // Each orbit's semi-major axis and its planets: size, starting phase (a
  // fraction of the way round, from aphelion), and what it carries.
  readonly property var orbitModel: [
    { a: 58,  planets: [{ s: 3, ph: 0.12 }] },
    { a: 100, planets: [{ s: 4.5, ph: 0.58, moon: true }] },
    { a: 150, planets: [{ s: 3.5, ph: 0.3 }, { s: 2.5, ph: 0.82 }] },
    { a: 210, planets: [{ s: 6, ph: 0.68, saturn: true }] },
    { a: 300, planets: [{ s: 5, ph: 0.16 }, { s: 3, ph: 0.6 }] },
    { a: 390, planets: [{ s: 7, ph: 0.42, moon: true }, { s: 3, ph: 0.9 }] }
  ]

  // 26 asteroids, alternating between two close paths.
  readonly property var beltModel: {
    var dots = []
    for (var j = 0; j < 26; j++) {
      dots.push({
        inner: j % 2 === 0,
        size: 1.3 + ((j * 13) % 10) / 10,
        alpha: Math.min(0.9, 0.18 * (1.8 + (j % 3) * 0.7)),
        ph: ((j / 26) + ((j * 37) % 10) / 120) % 1
      })
    }
    return dots
  }

  // Faint stars, in the frame's pixels.
  readonly property var starModel: [
    [300, 18], [352, 70], [250, 34], [362, 300], [318, 372], [356, 452], [292, 520],
    [348, 582], [210, 560], [120, 596], [40, 470], [18, 330], [268, 410], [180, 470]
  ]

  // The mask has to sit beside what it masks, not inside it.
  Rectangle {
    id: clipMask
    width: root.width
    height: root.height
    radius: root.radius
    visible: false
    layer.enabled: root.radius > 0
  }

  // ---- Pieces ----------------------------------------------------------

  // A full ellipse from aphelion, clockwise on screen (down first). The sun
  // is at the origin, the left focus.
  component OrbitPath: Path {
    property real a
    property real b
    property real c
    startX: c + a
    startY: 0
    PathArc { x: c - a; y: 0; radiusX: a; radiusY: b; direction: PathArc.Clockwise }
    PathArc { x: c + a; y: 0; radiusX: a; radiusY: b; direction: PathArc.Clockwise }
  }

  // An outline ellipse around the sun at its left focus.
  component Ellipse: Shape {
    id: ellipse
    property real a
    property real b
    property real c
    property color stroke
    property int dash: 0
    x: c - a
    y: -b
    width: 2 * a
    height: 2 * b
    preferredRendererType: Shape.CurveRenderer
    ShapePath {
      strokeColor: ellipse.stroke
      strokeWidth: 1
      fillColor: "transparent"
      strokeStyle: ellipse.dash > 0 ? ShapePath.DashLine : ShapePath.SolidLine
      dashPattern: [ellipse.dash, ellipse.dash]
      PathAngleArc { centerX: ellipse.a; centerY: ellipse.b; radiusX: ellipse.a; radiusY: ellipse.b; sweepAngle: 360 }
    }
  }

  // A soft disc: `color` at the center, fading out by 70% of the box's
  // half-diagonal, like the frame's radial-gradient.
  component Glow: Shape {
    id: glow
    property real r
    property color color
    width: 2 * r
    height: 2 * r
    preferredRendererType: Shape.CurveRenderer
    ShapePath {
      strokeWidth: -1
      strokeColor: "transparent"
      fillGradient: RadialGradient {
        centerX: glow.r; centerY: glow.r; centerRadius: glow.r * Math.SQRT2
        focalX: glow.r; focalY: glow.r
        GradientStop { position: 0; color: glow.color }
        GradientStop { position: 0.7; color: root.tint(glow.color, 0) }
      }
      PathAngleArc { centerX: glow.r; centerY: glow.r; radiusX: glow.r; radiusY: glow.r; sweepAngle: 360 }
    }
  }

  // The opening burst: from a speck, spun ±170°, out to full size.
  component Burst: SequentialAnimation {
    id: burst
    property Item item
    property real spinFrom: -170
    property int delay: 0
    property int duration: 1500
    running: root.running
    PropertyAction { target: burst.item; property: "scale"; value: 0.04 }
    PropertyAction { target: burst.item; property: "rotation"; value: burst.spinFrom }
    PropertyAction { target: burst.item; property: "opacity"; value: 0 }
    PauseAnimation { duration: burst.delay }
    ParallelAnimation {
      NumberAnimation { target: burst.item; property: "scale"; to: 1; duration: burst.duration; easing.type: Easing.OutExpo }
      NumberAnimation { target: burst.item; property: "rotation"; to: 0; duration: burst.duration; easing.type: Easing.OutExpo }
      NumberAnimation { target: burst.item; property: "opacity"; to: 1; duration: burst.duration / 4; easing.type: Easing.OutExpo }
    }
  }

  // Something carried round the orbit of semi-major axis `a`, starting
  // `phase` of the way round, once per Kepler period; its children are drawn
  // about (0, 0).
  component Body: Item {
    id: body
    property real a
    property real phase
    readonly property real periodMs: root.period(a)
    x: interpolator.x
    y: interpolator.y
    PathInterpolator {
      id: interpolator
      progress: (body.phase + root.orbitTime / body.periodMs) % 1
      path: OrbitPath { a: body.a; b: body.a * Math.sqrt(1 - root.ecc * root.ecc); c: body.a * root.ecc }
    }
  }

  component Dot: Rectangle {
    property real size
    x: -size / 2
    y: -size / 2
    width: size
    height: size
    radius: size / 2
  }

  // A stretch of the comet's progress, by the frame's keyframes: `to` at
  // `t1` of the cycle, having started at `t0`.
  component CometLeg: NumberAnimation {
    property real t0
    property real t1
    target: cometPos
    property: "progress"
    duration: (t1 - t0) * comet.periodMs
  }

  // ---- Scene, in the frame's pixels -----------------------------------

  Item {
    width: root.width
    height: root.height
    clip: true
    layer.enabled: root.radius > 0
    layer.effect: MultiEffect {
      maskEnabled: true
      maskSource: clipMask
      maskThresholdMin: 0.5
      maskSpreadAtMin: 1.0
    }

    Item {
      id: scene
      width: 380
      height: 1200
      scale: root.unit
      transformOrigin: Item.TopLeft

      readonly property real sunX: root.sunX / root.unit
      readonly property real sunY: root.sunY / root.unit

      // ---- Stars ----
      Repeater {
        model: root.starModel
        delegate: Dot {
          id: star
          required property var modelData
          required property int index
          x: modelData[0]
          y: modelData[1]
          size: index % 4 === 0 ? 2 : 1.4
          color: root.starColor
          opacity: 0.5

          SequentialAnimation {
            running: root.running
            PropertyAction { target: star; property: "opacity"; value: 0.5 }
            PauseAnimation { duration: star.index * 700 }
            NumberAnimation { target: star; property: "opacity"; to: 0.8; duration: (3 + star.index % 5) * 500; easing.type: Easing.InOutSine }
            SequentialAnimation {
              loops: Animation.Infinite
              NumberAnimation { target: star; property: "opacity"; to: 0.12; duration: (3 + star.index % 5) * 1000; easing.type: Easing.InOutSine }
              NumberAnimation { target: star; property: "opacity"; to: 0.8; duration: (3 + star.index % 5) * 1000; easing.type: Easing.InOutSine }
            }
          }
          Connections { target: root; function onSettle() { star.opacity = 0.5 } }
        }
      }

      // ---- Shooting star: one streak every 11s ----
      Item {
        x: 335
        y: 300
        rotation: 150

        Rectangle {
          id: streak
          width: 64
          height: 1.5
          y: -height / 2
          opacity: 0
          gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: root.tint(root.foreground, 0) }
            GradientStop { position: 1; color: root.starColor }
          }

          SequentialAnimation {
            running: root.running
            PropertyAction { target: streak; property: "opacity"; value: 0 }
            PauseAnimation { duration: 3000 }
            SequentialAnimation {
              loops: Animation.Infinite
              PropertyAction { target: streak; property: "x"; value: 0 }
              ParallelAnimation {
                NumberAnimation { target: streak; property: "x"; to: 200; duration: 550 }
                SequentialAnimation {
                  NumberAnimation { target: streak; property: "opacity"; to: 1; duration: 110 }
                  NumberAnimation { target: streak; property: "opacity"; to: 0; duration: 440 }
                }
              }
              PauseAnimation { duration: 11000 - 550 }
            }
          }
          Connections { target: root; function onSettle() { streak.opacity = 0 } }
        }
      }

      // ---- Sun ----
      Glow {
        id: sun
        r: 42
        x: scene.sunX - r
        y: scene.sunY - r
        color: root.tint(root.accent, 0.38)

        SequentialAnimation {
          running: root.running
          loops: Animation.Infinite
          ParallelAnimation {
            NumberAnimation { target: sun; property: "scale"; from: 0.85; to: 1.15; duration: 5000; easing.type: Easing.InOutSine }
            NumberAnimation { target: sun; property: "opacity"; from: 0.7; to: 1; duration: 5000; easing.type: Easing.InOutSine }
          }
          ParallelAnimation {
            NumberAnimation { target: sun; property: "scale"; to: 0.85; duration: 5000; easing.type: Easing.InOutSine }
            NumberAnimation { target: sun; property: "opacity"; to: 0.7; duration: 5000; easing.type: Easing.InOutSine }
          }
        }
        Connections { target: root; function onSettle() { sun.scale = 1; sun.opacity = 1 } }
      }

      // ---- Planets, orbits and the belt; the whole system sways ±3° ----
      Item {
        id: system
        x: scene.sunX
        y: scene.sunY

        SequentialAnimation {
          running: root.running
          PropertyAction { target: system; property: "rotation"; value: -3 }
          PauseAnimation { duration: 2400 }
          SequentialAnimation {
            loops: Animation.Infinite
            NumberAnimation { target: system; property: "rotation"; to: 3; duration: 46000; easing.type: Easing.InOutSine }
            NumberAnimation { target: system; property: "rotation"; to: -3; duration: 46000; easing.type: Easing.InOutSine }
          }
        }
        Connections { target: root; function onSettle() { system.rotation = 0 } }

        Item {
          rotation: 52

          Repeater {
            model: root.orbitModel
            delegate: Item {
              id: orbit
              required property var modelData
              required property int index
              readonly property real a: modelData.a
              readonly property real b: a * Math.sqrt(1 - root.ecc * root.ecc)
              readonly property real c: a * root.ecc

              Burst {
                item: orbit
                spinFrom: orbit.index % 2 ? 170 : -170
                delay: orbit.index * 60
                duration: 1500 + orbit.index * 140
              }
              Connections { target: root; function onSettle() { orbit.scale = 1; orbit.rotation = 0; orbit.opacity = 1 } }

              Ellipse { a: orbit.a; b: orbit.b; c: orbit.c; stroke: root.lineColor }

              Repeater {
                model: orbit.modelData.planets
                delegate: Body {
                  id: planet
                  required property var modelData
                  a: orbit.a
                  phase: modelData.ph

                  Dot { size: planet.modelData.s; color: root.planetColor }

                  // Saturn's ring.
                  Loader {
                    active: !!planet.modelData.saturn
                    sourceComponent: Shape {
                      x: -10
                      y: -3.5
                      width: 20
                      height: 7
                      rotation: -28
                      preferredRendererType: Shape.CurveRenderer
                      ShapePath {
                        strokeColor: root.planetColor
                        strokeWidth: 1
                        fillColor: "transparent"
                        PathAngleArc { centerX: 10; centerY: 3.5; radiusX: 10; radiusY: 3.5; sweepAngle: 360 }
                      }
                    }
                  }

                  // A moon, round every 6s.
                  Loader {
                    active: !!planet.modelData.moon
                    sourceComponent: Item {
                      rotation: (root.orbitTime / 6000 * 360) % 360
                      Dot { x: planet.modelData.s / 2 + 5 - size / 2; size: 2.5; color: root.planetColor }
                    }
                  }
                }
              }
            }
          }

          // ---- Asteroid belt ----
          Item {
            id: belt

            Burst { item: belt; delay: 200; duration: 2200 }
            Connections { target: root; function onSettle() { belt.scale = 1; belt.rotation = 0; belt.opacity = 1 } }

            Repeater {
              model: root.beltModel
              delegate: Body {
                id: rock
                required property var modelData
                a: modelData.inner ? 240 : 254
                phase: modelData.ph
                Dot { size: rock.modelData.size; color: root.tint(root.accent, rock.modelData.alpha) }
              }
            }
          }
        }
      }

      // ---- Comet: its own long orbit, fast near the sun ----
      Item {
        id: comet
        x: scene.sunX
        y: scene.sunY
        rotation: 18

        readonly property real a: 330
        readonly property real e: 0.93
        readonly property real b: a * Math.sqrt(1 - e * e)
        readonly property real c: a * e
        readonly property real periodMs: 42000 / root.timeScale
        readonly property OrbitPath path: OrbitPath { a: comet.a; b: comet.b; c: comet.c }

        PathInterpolator { id: cometPos; path: comet.path; progress: 0.2 }

        Item {
          id: cometLayer

          SequentialAnimation {
            running: root.running
            PropertyAction { target: cometLayer; property: "opacity"; value: 0 }
            PauseAnimation { duration: 1600 }
            NumberAnimation { target: cometLayer; property: "opacity"; to: 1; duration: 1400; easing.type: Easing.InOutQuad }
          }
          Connections { target: root; function onSettle() { cometLayer.opacity = 1; cometPos.progress = 0.2 } }

          Ellipse { a: comet.a; b: comet.b; c: comet.c; stroke: root.tint(root.accent, 0.126); dash: 4 }

          // The head, turned along its direction of travel so the tail trails.
          Item {
            x: cometPos.x
            y: cometPos.y
            rotation: cometPos.angle

            Rectangle {
              x: -51
              y: -3
              width: 50
              height: 6
              radius: 3
              gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: root.tint(root.accent, 0) }
                GradientStop { position: 1; color: root.tint(root.accent, 0.45) }
              }
            }
            Rectangle {
              x: -87
              y: -1
              width: 86
              height: 2
              radius: 1
              gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: root.tint(root.accent, 0) }
                GradientStop { position: 1; color: root.tint(root.accent, 0.85) }
              }
            }
            Glow { x: -r; y: -r; r: 9; color: root.tint(root.accent, 0.45) }
            Dot { size: 6; color: root.foreground }
          }
        }

        // The frame's keyframes, linear between them: slow out at aphelion,
        // a quick swing round the sun at the half. The first lap starts 30%
        // in, where the frame shows it.
        SequentialAnimation {
          running: root.running
          PropertyAction { target: cometPos; property: "progress"; value: 0.3 / 0.38 * 0.28 }
          CometLeg { t0: 0.3; t1: 0.38; to: 0.28 }
          CometLeg { t0: 0.38; t1: 0.45; to: 0.38 }
          CometLeg { t0: 0.45; t1: 0.485; to: 0.45 }
          CometLeg { t0: 0.485; t1: 0.5; to: 0.5 }
          CometLeg { t0: 0.5; t1: 0.515; to: 0.55 }
          CometLeg { t0: 0.515; t1: 0.55; to: 0.62 }
          CometLeg { t0: 0.55; t1: 0.62; to: 0.72 }
          CometLeg { t0: 0.62; t1: 1; to: 1 }
          SequentialAnimation {
            loops: Animation.Infinite
            CometLeg { t0: 0; t1: 0.38; from: 0; to: 0.28 }
            CometLeg { t0: 0.38; t1: 0.45; to: 0.38 }
            CometLeg { t0: 0.45; t1: 0.485; to: 0.45 }
            CometLeg { t0: 0.485; t1: 0.5; to: 0.5 }
            CometLeg { t0: 0.5; t1: 0.515; to: 0.55 }
            CometLeg { t0: 0.515; t1: 0.55; to: 0.62 }
            CometLeg { t0: 0.55; t1: 0.62; to: 0.72 }
            CometLeg { t0: 0.62; t1: 1; to: 1 }
          }
        }
      }
    }
  }
}
