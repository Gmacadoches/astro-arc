import QtQuick
import qs.Commons
import qs.Ui

// A row of thumbnails under a PreviewFrame, the size of the main view's:
// the one showing is ringed, a click or Enter shows another, and the row
// scrolls to keep the one showing in sight.
ListView {
  id: strip
  required property var panel

  // Image paths, one per thumbnail; "" draws an empty tile.
  property var sources: []
  // Tooltips, one per thumbnail, or none.
  property var tips: []
  property int current: -1
  // As PreviewFrame's: set for files rewritten in place.
  property int version: -1

  signal picked(int index)

  width: parent ? parent.width : 0
  height: Style.space(36)
  orientation: ListView.Horizontal
  spacing: Style.space(6)
  clip: true
  boundsBehavior: Flickable.StopAtBounds
  model: sources.length

  onCurrentChanged: if (current >= 0) positionViewAtIndex(current, ListView.Contain)
  onCountChanged: if (current >= 0) positionViewAtIndex(current, ListView.Contain)

  delegate: Rectangle {
    id: thumb
    required property int index
    readonly property bool showing: index === strip.current
    width: Math.round(height * 3 / 2)
    height: strip.height
    radius: Style.space(4)
    color: Qt.rgba(strip.panel.bar.foreground.r, strip.panel.bar.foreground.g, strip.panel.bar.foreground.b, 0.08)
    border.width: showing || activeFocus ? 2 : 1
    border.color: activeFocus ? strip.panel.bar.foreground
      : (showing ? Color.accent : Qt.rgba(strip.panel.bar.foreground.r, strip.panel.bar.foreground.g, strip.panel.bar.foreground.b, 0.2))
    activeFocusOnTab: true
    Keys.onReturnPressed: strip.picked(index)
    Keys.onEnterPressed: strip.picked(index)
    Keys.onSpacePressed: strip.picked(index)
    onActiveFocusChanged: if (activeFocus) strip.positionViewAtIndex(index, ListView.Contain)

    Image {
      anchors.fill: parent
      anchors.margins: 2
      readonly property string path: strip.sources[thumb.index] || ""
      source: path !== "" ? "file://" + path + (strip.version >= 0 ? "?v=" + strip.version : "") : ""
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      cache: strip.version < 0
      sourceSize.width: 96
    }

    MouseArea {
      id: thumbMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: strip.picked(thumb.index)
    }

    PanelToolTip {
      visible: thumbMouse.containsMouse && (strip.tips[thumb.index] || "") !== ""
      text: strip.tips[thumb.index] || ""
    }
  }
}
