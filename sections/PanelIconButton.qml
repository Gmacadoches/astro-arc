import QtQuick
import qs.Ui

// The kit's PanelActionButton, resting on the same glass as PanelButton when
// it is bordered.
PanelActionButton {
  id: button

  PanelGlass { target: button }
}
