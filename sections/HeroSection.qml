import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Hero: the shared PanelHero, same as every other built-in popup uses, and
// the one row every view keeps. Main view: ✦ (✵ while generating) ·
// Astro-Arc · state · schedule · last run, and a gear into Settings. Any
// other view: a back button, the view's title and a line about it — nothing
// trailing, since back is the way out (Escape works too).
PanelHero {
  id: hero
  required property var panel

  readonly property bool drilledIn: panel.view !== "main"
  readonly property string agentName: Model.agentLabel(panel.defaultAgent)

  // Where the main view's ✦ sits, for the sun of the orbits behind the
  // panel. Taken on the main view and kept, so a back button in its place
  // doesn't move the sun; measured once from any view so it starts somewhere.
  property point starCenter: Qt.point(0, 0)
  function latchStar() {
    if (!drilledIn || starCenter.x === 0)
      starCenter = Qt.point(starMetrics.advanceWidth / 2, height / 2)
  }
  onHeightChanged: latchStar()
  onDrilledInChanged: latchStar()
  Component.onCompleted: latchStar()

  TextMetrics {
    id: starMetrics
    text: "✦"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.display
    onAdvanceWidthChanged: hero.latchStar()
  }

  width: parent.width
  title: {
    switch (panel.view) {
    case "settings": return "Settings"
    case "tweak": return "Tweak " + (panel.tweakTheme ? panel.tweakTheme.label : "style")
    case "create": return panel.styleCreateMode === "import" ? "Import a theme" : "Create a new style"
    case "browse": return "History"
    case "fix": return "Fix " + (panel.failedStyle ? panel.failedStyle.label : "style")
    default: return "Astro-Arc"
    }
  }
  meta: {
    switch (panel.view) {
    case "settings": return ""
    case "tweak": return "Custom style · with " + (panel.agentCanLaunch ? agentName : "a chat model")
    case "create": return panel.styleCreateMode === "import" ? "From a chat model's reply"
      : (panel.agentCanLaunch ? "With " + agentName + " or any chat model" : "With any chat model")
    case "browse": return panel.reviewsIndex.length + " image" + (panel.reviewsIndex.length === 1 ? "" : "s")
    case "fix": return "Failed validation"
    default: return panel.heroStatus
    }
  }
  foreground: panel.bar.foreground
  fontFamily: panel.bar.fontFamily
  iconComponent: Component {
    Item {
      implicitWidth: hero.drilledIn ? backBtn.implicitWidth : starText.implicitWidth
      implicitHeight: hero.drilledIn ? backBtn.implicitHeight : starText.implicitHeight

      Text {
        id: starText
        textFormat: Text.PlainText
        visible: !hero.drilledIn
        text: panel.generating ? "✵" : "✦"
        color: Color.accent
        font.family: panel.bar.fontFamily
        font.pixelSize: Style.font.display
      }

      PanelIconButton {
        id: backBtn
        visible: hero.drilledIn
        iconText: "󰅁"
        bordered: true
        focusable: true
        fontSize: Style.font.body
        foreground: panel.bar.foreground
        tooltipText: "Back"
        onClicked: panel.goBack()
      }
    }
  }
  trailingControl: Component {
    PanelIconButton {
      visible: !hero.drilledIn
      iconText: "󰒓"
      bordered: true
      focusable: true
      fontSize: Style.font.body
      foreground: panel.bar.foreground
      tooltipText: "Settings"
      onClicked: panel.openView("settings")
    }
  }
}
