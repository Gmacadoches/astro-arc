import QtQuick
import qs.Commons
import qs.Ui

// A row of thumbnails under a PreviewFrame, the size of the main view's,
// and keyed like its strip: the one showing is ringed, a click shows
// another, and focus is selection — a thumbnail moved to is the one
// showing, and ←→ go on paging past the ends of the row, which scrolls to
// follow. Enter and Space are the frame's (entered, activated).
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

  // Past either end ←→ go round to the other, rather than stopping.
  property bool wraps: false

  signal picked(int index)
  signal entered()
  signal activated()

  // The thumbnail showing, for the panel's ↑↓ to land on.
  function currentThumb() { return current >= 0 ? itemAtIndex(current) : null }

  // Page by one from a focused thumbnail and take the focus along.
  function step(from, by, event) {
    var to = from + by
    if (wraps) to = (to + count) % count
    else if (to < 0 || to >= count) { event.accepted = false; return }
    picked(to)
    positionViewAtIndex(to, ListView.Contain)
    Qt.callLater(function() { var t = strip.itemAtIndex(to); if (t) t.forceActiveFocus() })
  }

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
    readonly property bool isThumb: true
    readonly property bool showing: index === strip.current
    width: Math.round(height * 3 / 2)
    height: strip.height
    radius: Style.space(4)
    color: Qt.rgba(strip.panel.bar.foreground.r, strip.panel.bar.foreground.g, strip.panel.bar.foreground.b, 0.08)
    border.width: showing || activeFocus ? 2 : 1
    border.color: activeFocus ? strip.panel.bar.foreground
      : (showing ? Color.accent : Qt.rgba(strip.panel.bar.foreground.r, strip.panel.bar.foreground.g, strip.panel.bar.foreground.b, 0.2))
    activeFocusOnTab: true
    Keys.onLeftPressed: function(event) { strip.step(index, -1, event) }
    Keys.onRightPressed: function(event) { strip.step(index, 1, event) }
    Keys.onReturnPressed: strip.entered()
    Keys.onEnterPressed: strip.entered()
    Keys.onSpacePressed: strip.activated()
    onActiveFocusChanged: if (activeFocus) {
      strip.positionViewAtIndex(index, ListView.Contain)
      if (index !== strip.current) strip.picked(index)
    }

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
