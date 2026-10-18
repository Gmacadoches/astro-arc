import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Settings view: QUALITY — the preset dropdown and what it resolved to.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  // ---- Quality: one control for every model choice. ---------
  PanelSectionHeader { text: "QUALITY"; foreground: panel.bar.foreground }

  Item {
    width: parent.width
    height: Math.max(qualityPresetLabel.implicitHeight, presetDropdown.implicitHeight)

    Text {
      id: qualityPresetLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Preset"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: presetDropdown
      anchors.left: qualityPresetLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: Model.resolvePresetKey(panel.configState, panel.modelCatalog.presets)
      options: {
        var opts = (panel.modelCatalog.presets || []).map(function(p) {
          return { value: p.key, label: Model.presetLabel(p) }
        })
        // "Custom" is a readout, not a choice — you reach it by
        // changing a model further down. Listed so the dropdown can
        // display the state it is actually in.
        opts.push({ value: "custom", label: "Custom (set below)" })
        return opts
      }
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitPreset(value) }
    }
  }

  // What the preset actually resolved to. Read-only on purpose: this
  // is the answer to "what is it using?", and changing it is what the
  // per-stage pickers further down are for.
  Column {
    width: parent.width
    spacing: Style.space(2)

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Chat models: " + (panel.configState.stage1Model === panel.configState.stage2Model
        ? panel.configState.stage1Model
        : panel.configState.stage1Model + " (interpretation) · " + panel.configState.stage2Model + " (image prompt)")
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Image model: " + panel.configState.openaiModel + " · " + panel.configState.openaiQuality
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      // Projected from a FRESH period, not from the cost log. The log
      // averages real runs, and same-day regenerations in it are
      // mostly cache hits, so it reported a daily cost far below the
      // truth — the run that prompted this charged $0.07 because only
      // Stage 2 and the render actually ran, while a new day of the
      // same settings costs $0.18.
      text: {
        var e = Model.projectedMonthlyCost(panel.modelCatalog.nextRun, panel.configState.frequency, panel.configState.intervalHours)
        if (!e) return "Est. monthly: cost unknown for the current models"
        var per = Model.formatUsd(e.perRun) + " per full run ("
          + (e.source === "actual" ? "actual" : "est") + ")"
        // With nothing scheduled there is no monthly figure to give —
        // only what a run costs when you ask for one.
        if (!e.scheduled) return "No recurring cost — generates only when you click. " + per
        return "Est. " + Model.formatUsd(e.perMonth) + "/month at " + Model.scheduleLabel(panel.configState.frequency, panel.configState.intervalHours)
          + " · " + per
      }
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
