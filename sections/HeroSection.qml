import QtQuick
import qs.Commons
import qs.Ui

// Hero: icon · title · status — the shared PanelHero, same as every other
// built-in popup uses. The ‹ / ⚙ / ✕ controls toggle the settings view.
PanelHero {
  required property var panel

  width: parent.width
  title: panel.showSettings ? "Settings" : "Astro-Arc"
  meta: panel.showSettings ? "" : panel.heroStatus
  foreground: panel.bar.foreground
  fontFamily: panel.bar.fontFamily
  iconComponent: Component {
    Text {
      text: panel.showSettings ? "‹" : (panel.generating ? "✵" : "✦")
      color: panel.bar.foreground
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.display

      // Was purely decorative — a "‹" that looks exactly like a
      // back button but did nothing when clicked. Same action as
      // the trailing ✕ below; only live while it's actually
      // showing the back chevron, not over the ✦/✵ status icon.
      MouseArea {
        anchors.fill: parent
        enabled: panel.showSettings
        cursorShape: panel.showSettings ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: panel.toggleSettings()
      }
    }
  }
  trailingControl: Component {
    PanelActionButton {
      iconText: panel.showSettings ? "✕" : "⚙"
      fontSize: Style.font.body
      foreground: panel.bar.foreground
      tooltipText: panel.showSettings ? "Close settings" : "Settings"
      onClicked: panel.toggleSettings()
    }
  }
}
