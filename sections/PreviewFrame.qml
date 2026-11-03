import QtQuick
import qs.Commons
import qs.Ui

// A big 3:2 picture to page through, drawn like the main view's: ‹ › on it,
// a caption chip bottom-left and "n of m" bottom-right. Focused, ←→ page.
// The Tweak and Styles drill-ins use it; the main view keeps its own.
Rectangle {
  id: frame
  required property var panel

  property string source: ""
  property string caption: ""
  // Shown when there is no image to show.
  property string emptyText: "No image"
  // -1 when nothing is showing; the count chip hides then.
  property int index: -1
  property int count: 0
  // Past either end ‹ › go round to the other, rather than stopping.
  property bool wraps: false
  // Set (and bump) for a file rewritten in place, like a style's preview:
  // it is then never served from the cache.
  property int version: -1

  signal previous()
  signal next()
  signal activated()

  readonly property bool canPrevious: count > 1 && (wraps || index > 0)
  readonly property bool canNext: count > 0 && (wraps || index < count - 1)
  readonly property color scrim: Qt.rgba(panel.bar.background.r, panel.bar.background.g, panel.bar.background.b, 0.78)

  width: parent ? parent.width : 0
  height: Math.round(width * 2 / 3)
  radius: Style.cornerRadius
  color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.06)
  border.width: activeFocus ? 2 : 1
  border.color: activeFocus ? panel.bar.foreground
    : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.25)

  activeFocusOnTab: count > 0
  Keys.onLeftPressed: function(event) { if (frame.canPrevious) frame.previous(); else event.accepted = false }
  Keys.onRightPressed: function(event) { if (frame.canNext) frame.next(); else event.accepted = false }
  Keys.onReturnPressed: frame.activated()
  Keys.onEnterPressed: frame.activated()
  Keys.onSpacePressed: frame.activated()

  Image {
    id: image
    anchors.fill: parent
    anchors.margins: 2
    visible: status === Image.Ready
    source: frame.source !== "" ? "file://" + frame.source + (frame.version >= 0 ? "?v=" + frame.version : "") : ""
    fillMode: Image.PreserveAspectCrop
    asynchronous: true
    cache: frame.version < 0
    // The full-size background can be 4K; the panel needs a fraction.
    sourceSize.width: 760
  }

  Text {
    textFormat: Text.PlainText
    anchors.centerIn: parent
    width: parent.width - Style.space(32)
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.WordWrap
    visible: !image.visible
    text: image.status === Image.Loading ? "Loading…" : frame.emptyText
    color: frame.panel.bar.foreground
    font.family: frame.panel.bar.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  MouseArea {
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onClicked: frame.activated()
  }

  // ‹ previous, › next. Mouse targets on the image; from the keyboard the
  // focused frame takes ←→ itself.
  Repeater {
    model: [{ forward: false }, { forward: true }]

    Rectangle {
      required property var modelData
      readonly property bool live: modelData.forward ? frame.canNext : frame.canPrevious
      visible: frame.count > 1 || (frame.index < 0 && frame.count > 0 && modelData.forward)
      anchors.verticalCenter: parent.verticalCenter
      x: modelData.forward ? parent.width - width - Style.space(8) : Style.space(8)
      width: Style.space(34)
      height: Style.space(42)
      radius: Style.cornerRadius
      color: frame.scrim
      opacity: live ? 1 : 0.35

      Text {
        textFormat: Text.PlainText
        anchors.centerIn: parent
        text: parent.modelData.forward ? "󰅂" : "󰅁"
        color: frame.panel.bar.foreground
        font.family: frame.panel.bar.fontFamily
        font.pixelSize: Style.font.body
      }

      MouseArea {
        anchors.fill: parent
        enabled: parent.live
        cursorShape: Qt.PointingHandCursor
        onClicked: parent.modelData.forward ? frame.next() : frame.previous()
      }
    }
  }

  Item {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.margins: Style.space(8)
    height: captionChip.height

    Rectangle {
      id: captionChip
      anchors.left: parent.left
      visible: frame.caption !== ""
      width: Math.min(captionText.implicitWidth + Style.space(16),
                      parent.width - (countChip.visible ? countChip.width + Style.space(8) : 0))
      height: captionText.implicitHeight + Style.space(8)
      radius: Style.space(4)
      color: frame.scrim

      Text {
        id: captionText
        textFormat: Text.PlainText
        anchors.fill: parent
        anchors.leftMargin: Style.space(8)
        anchors.rightMargin: Style.space(8)
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: frame.caption
        color: frame.panel.bar.foreground
        font.family: frame.panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    Rectangle {
      id: countChip
      anchors.right: parent.right
      visible: frame.index >= 0 && frame.count > 1
      width: countText.implicitWidth + Style.space(16)
      height: captionChip.height
      radius: Style.space(4)
      color: frame.scrim

      Text {
        id: countText
        textFormat: Text.PlainText
        anchors.centerIn: parent
        text: (frame.index + 1) + " of " + frame.count
        color: frame.panel.bar.foreground
        font.family: frame.panel.bar.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }
}
