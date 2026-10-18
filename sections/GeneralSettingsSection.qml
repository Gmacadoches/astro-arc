import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Advanced popout, top: Background only, Provider, and the one API key
// that is written to every slot (the per-stage rows further down override it).
Column {
  id: section
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  // First, because it decides what a generation does to the desktop
  // at all — more consequential than any model choice below.
  Toggle {
    width: parent.width
    label: panel.configState.backgroundOnly ? "Background only (on)" : "Background only (off)"
    description: panel.configState.backgroundOnly
      ? "Do not update theme colors"
      : "Theme colors will update from the background's palette"
    checked: panel.configState.backgroundOnly
    foreground: panel.bar.foreground
    fontFamily: panel.bar.fontFamily
    onClicked: panel.commitBackgroundOnly(!panel.configState.backgroundOnly)
  }

  // ---- Provider: the global setting everything else is scoped
  // to. The key you paste, which models are offered, what they cost
  // — all of it follows from this. Only OpenAI today; the control
  // exists so a second provider is a data change, not a redesign.
  // (This replaced a "Backend" picker that lived under IMAGE
  // GENERATION and offered local Stable Diffusion. Local is no longer
  // offered here, but a config that still says "local" is honored —
  // see astro-arc-generate.)
  Item {
    width: parent.width
    height: Math.max(providerLabel.implicitHeight, providerDropdown.implicitHeight)

    Text {
      id: providerLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Provider"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: providerDropdown
      anchors.left: providerLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: panel.configState.provider
      options: Model.PROVIDER_CHOICES.map(function(c) {
        return { value: c.key, label: c.label }
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitProvider(value) }
    }

  }

  // One key, written to all three slots. The per-stage split lives in
  // the popout; showing three key rows here made the common case —
  // one account, one key — look like a three-step setup.
  //
  // Titled, because a stored key renders as a masked reference
  // ("sk-...CxYA") and an unlabelled row of characters gives no clue
  // what it is or why it is there.
  Text {
    width: parent.width
    text: "API key"
    color: Qt.darker(panel.bar.foreground, 1.3)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ApiKeySection { panel: section.panel; slot: "image"; storeAll: true }
}
