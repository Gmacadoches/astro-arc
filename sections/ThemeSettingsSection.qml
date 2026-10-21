import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Settings view: THEME — palette generator and history retention.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "THEME"; foreground: panel.bar.foreground }

  // ---- Theme generator: which tool turns the rendered image
  // into colors.toml/icons.theme/etc. "Built-in" is
  // palette_extract.py (default, no external dependency);
  // "Aether" shells out to Omarchy's own richer generator and
  // silently falls back to built-in if it's missing or fails —
  // see astro-arc-generate's theming block. --------------------
  Item {
    width: parent.width
    height: Math.max(themeGeneratorLabel.implicitHeight, themeGeneratorDropdown.implicitHeight)

    Text {
      id: themeGeneratorLabel
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Generator"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: themeGeneratorDropdown
      anchors.left: themeGeneratorLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: panel.configState.themeGenerator
      options: Model.THEME_GENERATOR_CHOICES.map(function(c) {
        return { value: c.key, label: c.label }
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitThemeGenerator(value) }
    }
  }


  // ---- History retention: days of "Themes Generated" entries
  // to keep (astro-arc-prune-history runs automatically every
  // generation with this value). Estimate is real-data-based —
  // the average size of entries that actually have a recorded
  // sizeBytes, times how many the window is projected to hold at
  // the current frequency — never a guessed number before
  // there's any size history to average (see
  // Model.estimateHistorySpace). --------------------------------
  Item {
    width: parent.width
    height: Math.max(historyRetentionLabel.implicitHeight, historyRetentionField.implicitHeight)

    Text {
      id: historyRetentionLabel
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "History"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    ClipboardSafeField {
      id: historyRetentionField
      anchors.left: historyRetentionLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: historyRetentionDaysLabel.left
      anchors.rightMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
      placeholderText: "30"
      foreground: panel.bar.foreground
      font.family: panel.bar.fontFamily
      onEditingFinished: panel.commitHistoryRetentionDays(text)
      Component.onCompleted: text = String(panel.configState.historyRetentionDays)
      Connections {
        target: panel
        function onConfigStateChanged() {
          if (!historyRetentionField.activeFocus) historyRetentionField.text = String(panel.configState.historyRetentionDays)
        }
      }
    }

    Text {
      id: historyRetentionDaysLabel
      textFormat: Text.PlainText
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: "days"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  Text {
    textFormat: Text.PlainText
    visible: panel.historyRetentionError !== ""
    width: parent.width
    text: panel.historyRetentionError
    color: panel.bar.urgent || Color.urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    textFormat: Text.PlainText
    width: parent.width
    wrapMode: Text.WordWrap
    readonly property var estimate: Model.estimateHistorySpace(panel.reviewsIndex, panel.configState.historyRetentionDays, panel.configState.frequency, panel.configState.intervalHours)
    text: {
      var e = estimate
      var basis = " (based on " + e.knownCount + " saved generation" + (e.knownCount === 1 ? "" : "s") + ")"
      if (e.estimatedBytes !== null)
        return "Est. space for " + panel.configState.historyRetentionDays + " days: ~"
          + Model.formatBytes(e.estimatedBytes) + basis
      // No cadence: how much this ends up using depends on how often
      // you click, so give the per-generation figure — which IS known
      // — rather than the "not enough history" line, which would be
      // untrue whenever there is history but nothing scheduled.
      if (e.noCadence)
        return "Est. space: ~" + Model.formatBytes(e.avgBytes) + " per generation"
          + " — no total, since nothing is scheduled" + basis
      return "Est. space: not enough history yet to estimate"
    }
    color: Qt.darker(panel.bar.foreground, 1.5)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
