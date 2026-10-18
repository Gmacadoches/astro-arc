import QtQuick
import qs.Commons
import qs.Ui

// Reusable key-management block for one API slot (stage1/stage2/image):
// masked-reference display, replace/remove, password input, and
// validation status. Used identically for all three settings sections
// instead of tripling ~90 lines of near-identical QML — that duplication is
// exactly how the earlier null-guard bug (one copy fixed, a second one
// wouldn't have been) would have happened again with three copies instead
// of one. All state lives in Panel.qml; `panel` is that root, and the
// slot-parameterized functions on it do the work.
Column {
  id: section
  required property string slot
  required property var panel
  // When true this row represents the ONE key the simple view shows, and
  // saving writes it to every slot. `slot` still names which slot's status is
  // displayed, since in that mode all three hold the same key anyway.
  property bool storeAll: false
  readonly property var keyState: panel.apiKeyState[slot]

  width: parent.width
  spacing: Style.space(6)

  // ---- Key present, not replacing: masked reference only. The full
  // key is never displayed. ------------------------------------------
  Item {
    width: parent.width
    visible: section.keyState.present && !section.keyState.replacing
    height: Math.max(statusText.implicitHeight, replaceBtn.implicitHeight)

    Text {
      id: statusText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      text: section.keyState.masked
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
      color: panel.bar.foreground
    }

    Row {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(6)

      Button {
        id: replaceBtn
        text: "Replace"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: {
          panel.setApiKeyField(section.slot, "replacing", true)
          panel.setApiKeyField(section.slot, "validation", null)
        }
      }
      Button {
        text: "Remove"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: panel.clearApiKey(section.slot)
      }
    }
  }

  // ---- No key yet, or actively replacing it: masked/password input.
  // Field text is never bound to the draft the normal way, for the same
  // reason birthDateField isn't (see its comment) — onTextChanged
  // pushes one way, and the field is cleared imperatively once saved.
  Column {
    width: parent.width
    spacing: Style.space(6)
    visible: !section.keyState.present || section.keyState.replacing

    Row {
      width: parent.width
      spacing: Style.space(8)

      ClipboardSafeField {
        id: keyField
        width: parent.width - saveKeyBtn.width - Style.space(8)
        password: true
        enabled: !section.keyState.saving
        placeholderText: "sk-…"
        foreground: panel.bar.foreground
        font.family: panel.bar.fontFamily
        onTextChanged: {
          if (section.slot === "stage1") panel.stage1Draft = text
          else if (section.slot === "stage2") panel.stage2Draft = text
          else panel.imageDraft = text
        }
        Keys.onReturnPressed: section.storeAll ? panel.saveApiKeyEverywhere() : panel.saveApiKey(section.slot)
      }

      Button {
        id: saveKeyBtn
        text: section.keyState.saving ? "Saving…" : "Save"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        enabled: !section.keyState.saving && panel.draftFor(section.slot).length > 0
        onClicked: section.storeAll ? panel.saveApiKeyEverywhere() : panel.saveApiKey(section.slot)
      }
    }

    Button {
      visible: section.keyState.replacing
      text: "Cancel"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      onClicked: {
        panel.setApiKeyField(section.slot, "replacing", false)
        keyField.text = ""
        panel.clearDraft(section.slot)
      }
    }
  }

  // ---- Validation status — never shows the key itself, only whether
  // the last save authenticated. ---------------------------------------
  Text {
    visible: section.keyState.validating || section.keyState.validation !== null
    width: parent.width
    wrapMode: Text.WordWrap
    text: section.keyState.validating
      ? "Validating…"
      : section.keyState.validation
        ? (section.keyState.validation.ok ? "✓ " : "✗ ") + section.keyState.validation.message
        : ""
    color: section.keyState.validating
      ? Qt.darker(panel.bar.foreground, 1.5)
      : (section.keyState.validation && section.keyState.validation.ok ? Color.accent : (panel.bar.urgent || Color.urgent))
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
