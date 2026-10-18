import QtQuick
import qs.Commons
import qs.Ui

// Hero: the shared PanelHero, same as every other built-in popup uses.
// Main view: ✦ (✵ while generating) · Astro-Arc · status, and a gear that
// opens the settings view. Settings view: a back button and "Settings" —
// nothing trailing, since back is the one way out.
PanelHero {
  required property var panel

  width: parent.width
  title: panel.showSettings ? "Settings" : "Astro-Arc"
  meta: panel.showSettings ? "" : panel.heroStatus
  foreground: panel.bar.foreground
  fontFamily: panel.bar.fontFamily
  iconComponent: Component {
    Item {
      implicitWidth: panel.showSettings ? backBtn.implicitWidth : starText.implicitWidth
      implicitHeight: panel.showSettings ? backBtn.implicitHeight : starText.implicitHeight

      Text {
        id: starText
        visible: !panel.showSettings
        text: panel.generating ? "✵" : "✦"
        color: Color.accent
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.display
      }

      PanelActionButton {
        id: backBtn
        visible: panel.showSettings
        iconText: "󰅁"
        bordered: true
        fontSize: Style.font.body
        foreground: panel.bar.foreground
        tooltipText: "Back"
        onClicked: panel.toggleSettings()
      }
    }
  }
  trailingControl: Component {
    PanelActionButton {
      visible: !panel.showSettings
      iconText: "󰒓"
      bordered: true
      fontSize: Style.font.body
      foreground: panel.bar.foreground
      tooltipText: "Settings"
      onClicked: panel.toggleSettings()
    }
  }
}
