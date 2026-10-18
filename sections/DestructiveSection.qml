import QtQuick
import qs.Commons
import qs.Ui

// Advanced popout: DESTRUCTIVE — Clear History, two clicks to confirm.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "DESTRUCTIVE"; foreground: panel.bar.foreground }

  // Moved out of the main panel: it sat next to "Save Selected Theme",
  // one row apart from the thing it destroys, and a mis-click there
  // deletes every generation. Two clicks are still required (the button
  // arms, then confirms) — this just puts it somewhere you have to mean
  // to go.
  Column {
    width: parent.width
    spacing: Style.space(4)

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Deletes every generation in History immediately, ignoring the retention window. Saved themes are not affected."
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Button {
      id: advClearHistoryBtn
      text: panel.clearHistoryArmed ? "Confirm Clear?" : "Clear History"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.clearHistoryArmed ? (panel.bar.urgent || Color.urgent) : panel.bar.foreground
      onClicked: panel.clearHistoryClicked()
    }
  }
}
