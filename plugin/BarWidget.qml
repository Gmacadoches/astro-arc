import QtQuick
import qs.Commons
import qs.Ui

// Bar icon + popup host, following the same shape as the built-in
// omarchy.weather widget: BarWidget owns the bar slot and forwards
// bar/settings/hostWidget into a lazily-loaded Panel.qml.
BarWidget {
  id: root
  moduleName: "garrett.astro-arc"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  // Shape contract for shell.summon/hide/toggle routing (Bar.findPanelWidget
  // requires open/close/opened on the bar-widget root), same as weather.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // A plain four-pointed star stands in for real zodiac/moon-phase
    // iconography until Phase 6 (polish pass) picks a proper glyph.
    text: panelLoader.item && panelLoader.item.generating ? "✵" : "✦"
    slotSize: Style.bar.statusSlot
    tooltipText: "Astro-Arc"

    onPressed: function(b) {
      root.togglePanel()
    }
  }
}
