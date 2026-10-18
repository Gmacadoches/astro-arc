import QtQuick
import qs.Commons

// A dim caption with an ⓘ beside it: what a control promises or costs,
// stated where the control is. Used under Schedule and in Create a new style.
Item {
  id: note
  property string text: ""
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  implicitHeight: Math.max(icon.implicitHeight, body.implicitHeight)

  Text {
    id: icon
    anchors.left: parent.left
    anchors.top: parent.top
    text: "󰋽"
    color: Qt.darker(note.foreground, 1.5)
    font.family: note.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    id: body
    anchors.left: icon.right
    anchors.leftMargin: Style.space(6)
    anchors.right: parent.right
    anchors.top: parent.top
    wrapMode: Text.WordWrap
    text: note.text
    color: Qt.darker(note.foreground, 1.5)
    font.family: note.fontFamily
    font.pixelSize: Style.font.caption
    lineHeight: 1.2
  }
}
