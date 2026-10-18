import QtQuick
import qs.Commons
import qs.Ui

// Settings view, last: the way into the Advanced popout, where everything a
// reading doesn't need lives — provider, keys, models, quality, theme, usage.
PanelButton {
  required property var panel

  width: parent.width
  leftAlign: true
  bordered: true
  text: "Advanced settings · models & API keys"
  fontSize: Style.font.bodySmall
  foreground: panel.bar.foreground
  onClicked: panel.advancedOpen = true

  Text {
    anchors.right: parent.right
    anchors.rightMargin: Style.spacing.controlPaddingX
    anchors.verticalCenter: parent.verticalCenter
    text: "󰅂"
    color: Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.body
  }
}
