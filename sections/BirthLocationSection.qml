import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Settings view: BIRTH & LOCATION — the only inputs a reading needs.
// Labels sit above their fields here (unlike the label-left rows in the
// Advanced popout): Date and Time share a row, Place gets its own.
Column {
  id: section
  required property var panel
  // Panel.qml pushes text into these and reads the location query back.
  property alias birthDateField: birthDateField
  property alias birthTimeField: birthTimeField
  property alias locationField: locationField

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)

  width: parent.width
  spacing: Style.space(10)

  component FieldLabel: Text {
    color: section.dim
    font.family: section.panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  PanelSectionHeader { text: "BIRTH & LOCATION"; foreground: panel.bar.foreground }

  // Date and time. Blank time = unknown (no separate toggle); both auto-save
  // on blur/Enter.
  Item {
    width: parent.width
    height: Math.max(dateColumn.implicitHeight, timeColumn.implicitHeight)

    Column {
      id: dateColumn
      anchors.left: parent.left
      anchors.right: timeColumn.left
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(4)

      FieldLabel { text: "Date" }

      // text is intentionally not bound to panel.draftBirthDate: a
      // persistent binding would be severed the moment the user types (Qt
      // Quick sets `text` imperatively as you edit, which drops any
      // declarative binding on it for good). syncDraftsFromConfig pushes
      // text in on load/reopen instead; onTextChanged is the only path the
      // other way.
      //
      // onTextChanged also re-formats as-you-type: only digits are
      // meaningful input, and Model.formatDateDigits re-inserts the "-"
      // separators, so typing "19900615" displays as "1990-06-15" without
      // the user typing the dashes. Reassigning `text` re-enters
      // onTextChanged; the `formatted !== text` guard makes that second pass
      // a no-op instead of a loop.
      ClipboardSafeField {
        id: birthDateField
        width: parent.width
        maximumLength: 10
        placeholderText: "YYYY-MM-DD"
        foreground: panel.bar.foreground
        font.family: panel.bar.fontFamily
        onTextChanged: {
          var formatted = Model.formatDateDigits(text)
          if (formatted !== text) { text = formatted; return }
          panel.draftBirthDate = text
        }
        onEditingFinished: panel.commitBirth()
      }
    }

    Column {
      id: timeColumn
      anchors.right: parent.right
      width: Style.space(110)
      spacing: Style.space(4)

      FieldLabel { text: "Time" }

      ClipboardSafeField {
        id: birthTimeField
        width: parent.width
        maximumLength: 5
        placeholderText: "HH:MM"
        foreground: panel.bar.foreground
        font.family: panel.bar.fontFamily
        onTextChanged: {
          var formatted = Model.formatTimeDigits(text)
          if (formatted !== text) { text = formatted; return }
          panel.draftBirthTime = text
        }
        onEditingFinished: panel.commitBirth()
      }
    }
  }

  Text {
    visible: panel.birthError !== ""
    width: parent.width
    text: panel.birthError
    color: panel.bar.urgent || "#f38ba8"
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  // Place: the current location and Change; swaps to a search field in place
  // while editing.
  Column {
    width: parent.width
    spacing: Style.space(4)

    FieldLabel { text: "Place" }

    Item {
      width: parent.width
      visible: !panel.editingLocation
      height: Math.max(placeBox.height, changeBtn.implicitHeight)

      Rectangle {
        id: placeBox
        anchors.left: parent.left
        anchors.right: changeBtn.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        height: changeBtn.implicitHeight
        radius: Style.cornerRadius
        color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.04)
        border.width: 1
        border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.18)

        Text {
          anchors.left: parent.left
          anchors.leftMargin: Style.spacing.controlPaddingX
          anchors.right: parent.right
          anchors.rightMargin: Style.spacing.controlPaddingX
          anchors.verticalCenter: parent.verticalCenter
          text: panel.configState.locationName || "Not set"
          elide: Text.ElideRight
          color: panel.configState.locationName ? panel.bar.foreground : section.dim
          font.family: panel.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
      }

      PanelButton {
        id: changeBtn
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Change"
        bordered: true
        fontSize: Style.font.caption
        foreground: panel.bar.foreground
        onClicked: panel.startEditingLocation()
      }
    }

    Column {
      width: parent.width
      spacing: Style.space(4)
      visible: panel.editingLocation

      Row {
        width: parent.width
        spacing: Style.space(8)

        ClipboardSafeField {
          id: locationField
          width: parent.width - cancelBtn.width - Style.space(8)
          enabled: !panel.savingLocation
          placeholderText: "Search city, state, country"
          foreground: panel.bar.foreground
          font.family: panel.bar.fontFamily
          onTextChanged: if (panel.editingLocation && !panel.savingLocation) panel.restartGeocodeDebounce()
          Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Escape) {
              panel.cancelEditingLocation(); event.accepted = true
            } else if (event.key === Qt.Key_Down) {
              if (panel.suggestionIndex < panel.locationSuggestions.length - 1) panel.suggestionIndex++
              event.accepted = true
            } else if (event.key === Qt.Key_Up) {
              if (panel.suggestionIndex > 0) panel.suggestionIndex--
              event.accepted = true
            } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
              panel.commitLocation(); event.accepted = true
            }
          }
        }

        PanelButton {
          id: cancelBtn
          text: panel.savingLocation ? "…" : "✕"
          bordered: true
          foreground: panel.bar.foreground
          fontSize: Style.font.bodySmall
          enabled: !panel.savingLocation
          onClicked: panel.cancelEditingLocation()
        }
      }

      Repeater {
        model: panel.locationSuggestions

        Rectangle {
          required property var modelData
          required property int index
          width: parent.width
          height: suggestionRow.implicitHeight + Style.space(8)
          radius: Style.cornerRadius
          color: index === panel.suggestionIndex ? Style.hoverFillFor(panel.bar.foreground, Color.accent) : "transparent"

          Row {
            id: suggestionRow
            anchors.left: parent.left
            anchors.leftMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(8)

            Text {
              text: modelData.name
              color: panel.bar.foreground
              font.family: panel.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Text {
              visible: text !== ""
              text: modelData.description
              color: Qt.darker(panel.bar.foreground, 1.5)
              font.family: panel.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onPositionChanged: panel.suggestionIndex = index
            onClicked: panel.pickSuggestion(modelData)
          }
        }
      }
    }

    Text {
      visible: panel.configState.latitude !== null
      text: panel.configState.latitude !== null
        ? ("Coordinates: " + panel.configState.latitude.toFixed(4) + ", " + panel.configState.longitude.toFixed(4))
        : ""
      color: Qt.darker(panel.bar.foreground, 1.5)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
