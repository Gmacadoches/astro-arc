import QtQuick
import qs.Commons

// The surface a bordered button rests on: the panel's own color at 55%, so
// the orbits behind it show through faintly. Drawn under the button's own
// fill (z -1), which keeps the kit's hover and focus tints on top.
Rectangle {
  required property Item target

  z: -1
  anchors.fill: target
  radius: target.radius
  visible: target.bordered
  color: Util.alpha(Color.popups.background, 0.55)
}
