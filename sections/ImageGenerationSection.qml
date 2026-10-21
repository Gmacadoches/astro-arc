import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Advanced popout: IMAGE GENERATION — model, per-image cost ceiling, key.
Column {
  id: section
  required property var panel

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "IMAGE GENERATION"; foreground: panel.bar.foreground }

  Item {
    width: parent.width
    height: Math.max(advImageLabel.implicitHeight, advImageDropdown.implicitHeight)

    Text {
      id: advImageLabel
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Model"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: advImageDropdown
      anchors.left: advImageLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      showLabel: false
      value: Model.openaiModelDropdownValue(panel.configState.openaiModel, panel.configState.openaiQuality)
      options: {
        var rows = Model.imageRowsWithinCeiling(
          Model.visibleRows(panel.modelCatalog.image || [], panel.showAllModels),
          panel.configState.maxCostPerImage)
        var opts = rows.map(function(r) {
          return { value: Model.openaiModelDropdownValue(r.model, r.quality), label: Model.imageModelLabel(r) }
        })
        var current = Model.openaiModelDropdownValue(panel.configState.openaiModel, panel.configState.openaiQuality)
        if (current && !opts.some(function(o) { return o.value === current }))
          opts.unshift({ value: current, label: panel.configState.openaiModel + " — " + panel.configState.openaiQuality + "  · over ceiling" })
        return opts
      }
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitOpenaiModel(value) }
    }
  }

  Item {
    width: parent.width
    height: Math.max(advCeilingLabel.implicitHeight, advCeilingField.implicitHeight)

    Text {
      id: advCeilingLabel
      textFormat: Text.PlainText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Max $/image"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    ClipboardSafeField {
      id: advCeilingField
      anchors.left: advCeilingLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      placeholderText: "0.00 (no ceiling)"
      foreground: panel.bar.foreground
      font.family: panel.bar.fontFamily
      onEditingFinished: panel.commitMaxCostPerImage(text)
      Component.onCompleted: text = String(panel.configState.maxCostPerImage)
      Connections {
        target: panel
        function onConfigStateChanged() {
          if (!advCeilingField.activeFocus) advCeilingField.text = String(panel.configState.maxCostPerImage)
        }
      }
    }
  }

  Text {
    textFormat: Text.PlainText
    width: parent.width
    wrapMode: Text.WordWrap
    visible: panel.maxCostPerImageError !== ""
    text: panel.maxCostPerImageError
    color: panel.bar.urgent || Color.urgent
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  ApiKeySection { panel: section.panel; slot: "image" }
}
