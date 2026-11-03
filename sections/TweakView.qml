import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Drill-in: Tweak a style (any of them: every style is the user's file). What's off about it, optionally with
// a generation's image as an example (this style's newest unless another is picked), for the default agent (which edits the
// theme in place) or a web chat (Copy prompt). astro-arc-style backs the
// theme up first; Undo last tweak restores it. The title row is the hero's.
Column {
  id: view
  required property var panel

  readonly property color dim: Qt.darker(panel.bar.foreground, 1.3)

  width: parent.width
  spacing: Style.space(14)

  // The example: a generation's image beside its checkbox, this style's
  // newest by default. The strip under it picks another — any style's, since
  // an image can as easily show what to change towards as away from.
  Rectangle {
    width: parent.width
    visible: panel.tweakExampleChoices.all.length > 0
    height: exampleColumn.height + Style.space(16)
    radius: Style.cornerRadius
    color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.04)
    border.width: 1
    border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.15)

    Column {
      id: exampleColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.margins: Style.space(8)
      spacing: Style.space(8)

      Item {
        id: exampleRow
        width: parent.width
        height: Math.max(exampleImage.height, exampleToggle.height)

        Image {
          id: exampleImage
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(96)
          height: Style.space(64)
          source: panel.tweakExampleImage !== "" ? "file://" + panel.tweakExampleImage : ""
          fillMode: Image.PreserveAspectCrop
          asynchronous: true
          sourceSize.width: 240

          // This style has no generation yet: nothing is picked until one is.
          Rectangle {
            anchors.fill: parent
            visible: panel.tweakExample === null
            radius: Style.space(4)
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.2)
          }
        }

        // A checkbox the keyboard can reach: focus it, Space or Enter flips it.
        Item {
          id: exampleToggle
          anchors.left: exampleImage.right
          anchors.leftMargin: Style.space(12)
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          height: Math.max(Style.space(44), exampleLabel.implicitHeight)
          activeFocusOnTab: true
          Keys.onSpacePressed: panel.tweakIncludeExample = !panel.tweakIncludeExample
          Keys.onReturnPressed: panel.tweakIncludeExample = !panel.tweakIncludeExample
          Keys.onEnterPressed: panel.tweakIncludeExample = !panel.tweakIncludeExample

          Rectangle {
            id: exampleBox
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: Style.space(16)
            height: width
            radius: Style.space(3)
            color: panel.tweakIncludeExample ? Color.accent : "transparent"
            border.width: exampleToggle.activeFocus ? 2 : 1
            border.color: exampleToggle.activeFocus ? panel.bar.foreground
              : (panel.tweakIncludeExample ? Color.accent : Qt.darker(panel.bar.foreground, 1.5))

            Text {
              textFormat: Text.PlainText
              anchors.centerIn: parent
              visible: panel.tweakIncludeExample
              text: "󰄬"
              color: panel.bar.background
              font.family: panel.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            id: exampleLabel
            textFormat: Text.PlainText
            anchors.left: exampleBox.right
            anchors.leftMargin: Style.space(8)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            wrapMode: Text.WordWrap
            text: panel.tweakExample === null
              ? "No image in this style yet — pick one below to send as an example"
              : "Send this image as an example of what's off\n"
                + panel.reviewWhen(panel.tweakExample) + " · " + panel.reviewStyleLabel(panel.tweakExample)
            color: panel.bar.foreground
            font.family: panel.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: panel.tweakIncludeExample = !panel.tweakIncludeExample
          }
        }
      }

      // The picker: every generation with an image, this style's first.
      ListView {
        id: exampleStrip
        width: parent.width
        height: Style.space(40)
        orientation: ListView.Horizontal
        spacing: Style.space(6)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: panel.tweakExampleChoices.all

        delegate: Rectangle {
          id: choice
          required property var modelData
          required property int index
          readonly property bool current: panel.tweakExample !== null && modelData.id === panel.tweakExample.id
          width: Math.round(height * 3 / 2)
          height: exampleStrip.height
          radius: Style.space(4)
          color: Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.08)
          border.width: current || activeFocus ? 2 : 1
          border.color: activeFocus ? panel.bar.foreground
            : (current ? Color.accent : Qt.rgba(panel.bar.foreground.r, panel.bar.foreground.g, panel.bar.foreground.b, 0.2))
          activeFocusOnTab: true
          Keys.onReturnPressed: choice.pick()
          Keys.onEnterPressed: choice.pick()
          Keys.onSpacePressed: choice.pick()
          onActiveFocusChanged: if (activeFocus) exampleStrip.positionViewAtIndex(index, ListView.Contain)

          function pick() {
            panel.tweakExampleId = modelData.id
            panel.tweakIncludeExample = true
          }

          Image {
            anchors.fill: parent
            anchors.margins: 2
            source: "file://" + (choice.modelData.thumbPath !== "" ? choice.modelData.thumbPath : choice.modelData.imagePath)
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            sourceSize.width: 120
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: choice.pick()
          }
        }
      }
    }
  }

  Column {
    width: parent.width
    spacing: Style.space(6)

    Text {
      textFormat: Text.PlainText
      text: "What's off about it?"
      color: view.dim
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.caption
    }

    // Not bound to the draft for the reason birthDateField isn't (see
    // BirthLocationSection): typing would sever it. One-way out instead,
    // and the draft is pushed back in each time the view opens.
    ClipboardSafeArea {
      id: tweakNoteArea
      width: parent.width
      height: Math.max(implicitHeight, Style.space(104))
      placeholderText: "e.g. fewer landscapes, more character close-ups; keep the cel shading"
      foreground: panel.bar.foreground
      font.family: panel.bar.fontFamily
      onTextChanged: panel.tweakNoteDraft = text
      onVisibleChanged: if (visible) text = panel.tweakNoteDraft
    }
  }

  // The primary action, filled with the accent like Apply theme.
  PanelButton {
    width: parent.width
    visible: panel.agentCanLaunch
    text: panel.styleAgentRunning ? "Opening…" : "Tweak with " + Model.agentLabel(panel.defaultAgent)
    fontSize: Style.font.caption
    foreground: panel.bar.background
    color: hot || activeFocus ? Qt.lighter(Color.accent, 1.12) : Color.accent
    opacity: enabled ? 1 : 0.45
    enabled: !panel.styleAgentRunning && panel.tweakNoteDraft.trim() !== ""
    onClicked: panel.tweakStyleWithAgent()
  }

  Item {
    width: parent.width
    height: Math.max(tweakCopyBtn.implicitHeight, undoTweakBtn.implicitHeight)

    PanelButton {
      id: tweakCopyBtn
      anchors.left: parent.left
      width: (parent.width - Style.space(8)) / 2
      text: "Copy prompt"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: !panel.styleCopyRunning && panel.tweakNoteDraft.trim() !== ""
      opacity: enabled ? 1 : 0.45
      onClicked: panel.copyTweakPrompt()
    }

    PanelButton {
      id: undoTweakBtn
      anchors.right: parent.right
      width: tweakCopyBtn.width
      text: panel.styleUndoRunning ? "Undoing…" : "Undo last tweak"
      bordered: true
      fontSize: Style.font.caption
      foreground: panel.bar.foreground
      enabled: panel.tweakBackup !== null && !panel.styleUndoRunning
      opacity: enabled ? 1 : 0.45
      tooltipText: panel.tweakBackup && panel.tweakBackup.savedAt
        ? "Restore the version saved " + Qt.formatDateTime(new Date(panel.tweakBackup.savedAt), "MMM d, h:mm AP")
        : "Nothing to undo yet"
      onClicked: panel.undoLastTweak()
    }
  }

  Text {
    textFormat: Text.PlainText
    visible: panel.styleActionStatus !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.styleActionStatus
    color: panel.styleActionStatusIsError ? (panel.bar.urgent || Color.urgent) : view.dim
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }

  InfoNote {
    width: parent.width
    foreground: panel.bar.foreground
    fontFamily: panel.bar.fontFamily
    text: !panel.agentCanLaunch
      ? panel.agentWontLaunch + " Copy prompt puts the theme and your note on the clipboard for any chat "
        + "model; importing the reply saves the current version first."
      : Model.agentLabelStart(panel.defaultAgent) + " edits this style in a terminal, one step at a time: it "
        + "asks you before each change and before anything that uses your API key, telling you the cost first. "
        + "It sees your recent readings and the example image. The current version is saved first."
      + (panel.tweakTheme && panel.tweakTheme.starter && !panel.tweakTheme.changed
        ? " Once changed, this style stops getting Astro-Arc's updates; Revert in Styles brings them back." : "")
  }
}
