import QtQuick
import qs.Commons
import qs.Ui

// The kit's Button, reachable from the keyboard: Tab and the arrows give it
// focus (with the kit's focus ring), Enter or Space presses it. Every button
// inside the KeyboardPanel is one of these.
Button {
  id: button
  focusable: true

  // A bordered (secondary) button rests on glass, so the orbits behind the
  // panel show through it faintly; hover and focus tint on top of it.
  PanelGlass { target: button }
}
