import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Settings view: SCHEDULE — how often a generation runs, the interval under
// Hourly, and what a schedule actually promises.
Column {
  required property var panel
  // Panel.qml re-syncs these when the config file changes underneath them.
  property alias frequencyDropdown: frequencyDropdown
  property alias intervalHoursDropdown: intervalHoursDropdown

  readonly property bool hourly: panel.configState.frequency === "hourly"

  width: parent.width
  spacing: Style.space(10)

  PanelSectionHeader { text: "SCHEDULE"; foreground: panel.bar.foreground }

  // The picker takes the row; under Hourly an "every N h" picker joins it,
  // which keeps the Schedule list at five entries instead of one per
  // interval.
  Item {
    width: parent.width
    height: frequencyDropdown.implicitHeight

    PanelPicker {
      id: frequencyDropdown
      anchors.left: parent.left
      anchors.right: hourly ? intervalEveryLabel.left : parent.right
      anchors.rightMargin: hourly ? Style.space(8) : 0
      anchors.verticalCenter: parent.verticalCenter
      bounds: panel.contentArea
      foreground: panel.bar.foreground
      fontFamily: panel.bar.fontFamily
      value: panel.configState.frequency
      options: Model.FREQUENCY_CHOICES.map(function(c) {
        return { value: c.key, label: c.label }
      })
      onChanged: function(value) { panel.commitFrequency(value) }
    }

    Text {
      id: intervalEveryLabel
      textFormat: Text.PlainText
      anchors.right: intervalHoursDropdown.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      visible: hourly
      text: "every"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    PanelPicker {
      id: intervalHoursDropdown
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(64)
      visible: hourly
      bounds: panel.contentArea
      foreground: panel.bar.foreground
      fontFamily: panel.bar.fontFamily
      value: String(panel.configState.intervalHours)
      options: Model.INTERVAL_HOURS_CHOICES.map(function(n) {
        return { value: String(n), label: n + "h" }
      })
      onChanged: function(value) { panel.commitIntervalHours(value) }
    }
  }

  // The scheduler is the panel's own poll, so it needs the shell running: it
  // does not outlive the login session. Locking the screen does not stop it
  // (a lock is not a logout), and a boundary crossed while the machine was
  // off or asleep is caught by the first poll after it comes back, within
  // five minutes, rather than skipped. Worth stating plainly: "every hour"
  // reads as a promise the machine cannot keep while it is switched off.
  InfoNote {
    width: parent.width
    foreground: panel.bar.foreground
    fontFamily: panel.bar.fontFamily
    text: panel.configState.frequency === "manual"
      ? "Nothing generates on its own — Regenerate is the only trigger."
      : "Runs while you're logged in (a locked screen counts). Missed runs catch up when you're back."
  }
}
