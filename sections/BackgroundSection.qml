import QtQuick
import qs.Commons
import qs.Ui

// Settings view: BACKGROUND — the render size and its auto-detect.
Column {
  required property var panel
  // Panel.qml's detectBackgroundSize writes the detected size in here.
  property alias backgroundSizeField: backgroundSizeField

  width: parent.width
  spacing: Style.space(10)

  // ---- Background size: which resolution the render is fitted to.
  // Kept here rather than in the popout because it is about your
  // monitor, not about models.
  PanelSectionHeader { text: "BACKGROUND"; foreground: panel.bar.foreground }
  // ---- Background size: applies to whichever backend renders
  // the image — image_fit.py crops/scales either backend's
  // native output to this exact size afterward. -----------------
  Item {
    width: parent.width
    height: Math.max(backgroundSizeLabel.implicitHeight, backgroundSizeField.implicitHeight)

    Text {
      id: backgroundSizeLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Background"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    ClipboardSafeField {
      id: backgroundSizeField
      anchors.left: backgroundSizeLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: detectSizeBtn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      placeholderText: "auto"
      foreground: panel.bar.foreground
      font.family: panel.bar.fontFamily
      onEditingFinished: panel.commitBackgroundSize(text)
      Component.onCompleted: text = panel.configState.backgroundSize
      Connections {
        target: panel
        function onConfigStateChanged() {
          if (!backgroundSizeField.activeFocus) backgroundSizeField.text = panel.configState.backgroundSize
        }
      }
    }

    Button {
      id: detectSizeBtn
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: "Auto-detect"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: panel.detectBackgroundSize()
    }
  }

  Text {
    visible: panel.backgroundSizeError !== ""
    width: parent.width
    text: panel.backgroundSizeError
    color: panel.bar.urgent || Color.urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
