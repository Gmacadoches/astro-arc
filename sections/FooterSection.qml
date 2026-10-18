import QtQuick
import qs.Commons
import "../Model.js" as Model

// Main view, last: the two settings a reading runs on — when, and where —
// and the way to change them.
Item {
  required property var panel

  readonly property string scheduleText: {
    var f = panel.configState.frequency
    if (f === "manual") return "Manual"
    if (f === "hourly") {
      var s = Model.scheduleLabel(f, panel.configState.intervalHours)
      return s.charAt(0).toUpperCase() + s.slice(1)
    }
    var choice = Model.FREQUENCY_CHOICES.filter(function(c) { return c.key === f })[0]
    return choice ? choice.label : f
  }

  width: parent.width
  height: Math.max(summaryText.implicitHeight, settingsLink.implicitHeight)

  Text {
    id: summaryText
    anchors.left: parent.left
    anchors.right: settingsLink.left
    anchors.rightMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    elide: Text.ElideRight
    text: parent.scheduleText + " · " + (panel.configState.locationName || "No location set")
    color: Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    id: settingsLink
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    text: "Edit settings →"
    color: panel.bar.foreground
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
    font.underline: settingsMouse.containsMouse

    MouseArea {
      id: settingsMouse
      anchors.fill: parent
      anchors.margins: -Style.space(6)
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: panel.toggleSettings()
    }
  }
}
