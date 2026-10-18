import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Advanced settings popout. A real overlay window rather than an expanded
// panel, following the image-picker plugin's pattern (PanelWindow +
// WlrLayershell overlay): the bar panel is narrow, and this view has to hold
// every model the key can reach plus three key rows without either being
// cramped.
PanelWindow {
  id: advancedWindow
  required property var panel
  // Panel.qml's detectBackgroundSize writes the detected size in here.
  property alias backgroundSizeField: backgroundSection.backgroundSizeField

  visible: panel.advancedOpen
  anchors { top: true; bottom: true; left: true; right: true }
  color: "transparent"
  WlrLayershell.namespace: "astro-arc-advanced-settings"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: panel.advancedOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
  exclusionMode: ExclusionMode.Ignore

  // Click-outside-to-close, with the card itself swallowing clicks so a
  // mis-aimed click inside doesn't dismiss a form being filled in.
  MouseArea {
    anchors.fill: parent
    onClicked: panel.advancedOpen = false
  }

  Rectangle {
    anchors.fill: parent
    color: "#cc000000"
    z: -1
  }

  Rectangle {
    id: advancedCard
    anchors.centerIn: parent
    width: Math.min(parent.width - Style.space(16) * 2, 720)
    height: Math.min(parent.height - Style.space(16) * 2, 860)
    radius: 14
    color: panel.bar ? panel.bar.background : "#11111b"
    border.color: Qt.darker(panel.bar ? panel.bar.foreground : "#cdd6f4", 2.4)
    border.width: 1

    // Escape-to-close lives here, not on the PanelWindow. PanelWindow has no
    // `focus` property at all — assigning one aborts creation of the window
    // AND of its parent, which is what stopped the whole widget from opening.
    // Keys is an attached property of Item, so it needs an Item to attach to;
    // the card is one, and the window's Exclusive keyboardFocus is what routes
    // Wayland key events here in the first place.
    focus: panel.advancedOpen
    Keys.onEscapePressed: panel.advancedOpen = false

    MouseArea { anchors.fill: parent }

    Column {
      id: advancedHeader
      anchors { top: parent.top; left: parent.left; right: parent.right; margins: Style.space(14) }
      spacing: Style.space(4)

      Item {
        width: parent.width
        height: Math.max(advancedTitle.implicitHeight, advancedCloseBtn.implicitHeight)

        Text {
          id: advancedTitle
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: "Advanced settings"
          color: panel.bar ? panel.bar.foreground : "#cdd6f4"
          font.family: panel.bar ? panel.bar.fontFamily : "sans"
          font.pixelSize: Style.font.body
          font.bold: true
        }

        Button {
          id: advancedCloseBtn
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: "Close"
          foreground: panel.bar ? panel.bar.foreground : "#cdd6f4"
          onClicked: panel.advancedOpen = false
        }
      }

      Text {
        width: parent.width
        wrapMode: Text.WordWrap
        text: "Everything the panel itself doesn't need. A model picked under a stage overrides the quality preset, which will then read \"Custom\".Costs marked (est) are published rates applied to token counts measured from real runs; (actual) means this exact combination has been measured on this machine."
        color: Qt.darker(panel.bar ? panel.bar.foreground : "#cdd6f4", 1.3)
        font.family: panel.bar ? panel.bar.fontFamily : "sans"
        font.pixelSize: Style.font.caption
      }
    }

    Flickable {
      anchors { top: advancedHeader.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; margins: Style.space(14) }
      contentHeight: advancedContent.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: advancedContent
        width: parent.width
        spacing: Style.space(10)

        GeneralSettingsSection { panel: advancedWindow.panel }
        PanelSeparator { foreground: panel.bar.foreground }
        QualitySection { panel: advancedWindow.panel }
        PanelSeparator { foreground: panel.bar.foreground }
        BackgroundSection { id: backgroundSection; panel: advancedWindow.panel }
        PanelSeparator { foreground: panel.bar.foreground }
        ThemeSettingsSection { panel: advancedWindow.panel }
        PanelSeparator { foreground: panel.bar.foreground }
        UsageSection { panel: advancedWindow.panel }
        PanelSeparator { foreground: panel.bar.foreground }
        VersionSection { panel: advancedWindow.panel }

        PanelSeparator { foreground: panel.bar.foreground }
        ModelListSection { panel: advancedWindow.panel }

        PanelSeparator { foreground: panel.bar.foreground }
        PanelSectionHeader { text: "STAGE 1 · INTERPRETATION"; foreground: panel.bar.foreground }

        AdvancedChatPicker { panel: advancedWindow.panel; stage: "stage1" }
        ApiKeySection { panel: advancedWindow.panel; slot: "stage1" }

        PanelSeparator { foreground: panel.bar.foreground }
        PanelSectionHeader { text: "STAGE 2 · IMAGE PROMPT"; foreground: panel.bar.foreground }

        AdvancedChatPicker { panel: advancedWindow.panel; stage: "stage2" }
        ApiKeySection { panel: advancedWindow.panel; slot: "stage2" }

        PanelSeparator { foreground: panel.bar.foreground }
        ImageGenerationSection { panel: advancedWindow.panel }

        PanelSeparator { foreground: panel.bar.foreground }
        DestructiveSection { panel: advancedWindow.panel }
      }
    }
  }
}
