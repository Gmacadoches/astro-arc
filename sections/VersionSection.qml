import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Settings view: VERSION — the release this checkout is on.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "VERSION"; foreground: panel.bar.foreground }

  Item {
    width: parent.width
    height: versionRowLabel.implicitHeight

    Text {
      id: versionRowLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Version"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    // The release this checkout is on, linked to its GitHub page.
    // "v1.0.1 (a3f9c21)" on a release; "v1.0.1 +3 (a3f9c21)" on a
    // development copy that is 3 commits past it. The link is the
    // whole update story now: it lands on the release page, which
    // says to run `omarchy plugin update`.
    Text {
      id: versionText
      anchors.left: versionRowLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      elide: Text.ElideRight
      text: panel.versionLabel === ""
        ? ""
        : panel.versionLabel
          + (panel.versionAhead > 0 ? " +" + panel.versionAhead : "")
          + (panel.versionCommit !== "" ? " (" + panel.versionCommit + ")" : "")
      color: panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.underline: panel.versionUrl !== "" && versionMouse.containsMouse

      MouseArea {
        id: versionMouse
        anchors.fill: parent
        enabled: panel.versionUrl !== ""
        hoverEnabled: true
        cursorShape: panel.versionUrl !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: Quickshell.execDetached(["xdg-open", panel.versionUrl])
      }
    }

  }

  // Updating is a terminal command on purpose — see the Updates note
  // above the version properties in Panel.qml for why the panel does not do it.
  Text {
    width: parent.width
    wrapMode: Text.WordWrap
    text: "Update with: omarchy plugin update"
    color: Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
