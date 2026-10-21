import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// One chat-model picker, used for both stages in the popout. The two stages
// are separate controls here precisely because splitting them is the point of
// this window; the panel shows them merged as "Chat models".
Item {
  id: picker
  required property string stage
  required property var panel
  readonly property string currentModel: stage === "stage1" ? panel.configState.stage1Model : panel.configState.stage2Model

  width: parent.width
  height: Math.max(pickerLabel.implicitHeight, pickerDropdown.implicitHeight)

  Text {
    id: pickerLabel
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
    id: pickerDropdown
    anchors.left: pickerLabel.right
    anchors.leftMargin: Style.space(8)
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    showLabel: false
    value: picker.currentModel
    options: {
      var opts = Model.visibleRows(panel.modelCatalog.chat || [], panel.showAllModels).map(function(r) {
        return { value: r.model, label: Model.chatModelLabel(r) }
      })
      var cur = picker.currentModel
      if (cur && !opts.some(function(o) { return o.value === cur }))
        opts.unshift({ value: cur, label: cur + "  · not in catalog" })
      return opts
    }
    foreground: panel.bar.foreground
    onChanged: function(value) {
      if (picker.stage === "stage1") panel.commitStage1Model(value)
      else panel.commitStage2Model(value)
    }
  }
}
