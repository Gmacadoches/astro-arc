import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// Main view: Schedule — frequency, the hourly interval, and what a
// schedule actually promises.
Column {
  required property var panel

  width: parent.width
  spacing: Style.space(8)

  // ---- Schedule: label left, compact dropdown right — the
  // Dropdown's own default width (240px) is most of the panel,
  // so it's overridden down to fit a label beside it -------------
  Item {
    width: parent.width
    height: Math.max(scheduleLabel.implicitHeight, frequencyDropdown.implicitHeight)

    Text {
      id: scheduleLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: panel.labelColW
      text: "Schedule"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: frequencyDropdown
      anchors.left: scheduleLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(100)
      showLabel: false
      value: panel.configState.frequency
      options: Model.FREQUENCY_CHOICES.map(function(c) {
        return { value: c.key, label: c.label }
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitFrequency(value) }
    }

    // Every N hours, only under Hourly. A second small picker keeps
    // the Schedule list at five entries instead of growing one per
    // interval.
    Text {
      id: intervalEveryLabel
      anchors.left: frequencyDropdown.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      visible: panel.configState.frequency === "hourly"
      text: "every"
      color: Qt.darker(panel.bar.foreground, 1.3)
      font.family: panel.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Dropdown {
      id: intervalHoursDropdown
      anchors.left: intervalEveryLabel.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(56)
      visible: panel.configState.frequency === "hourly"
      showLabel: false
      value: String(panel.configState.intervalHours)
      options: Model.INTERVAL_HOURS_CHOICES.map(function(n) {
        return { value: String(n), label: n + "h" }
      })
      foreground: panel.bar.foreground
      onChanged: function(value) { panel.commitIntervalHours(value) }
    }
  }

  // ---- What a schedule actually promises. The scheduler is the
  // panel's own poll, so it needs the shell running: it does not
  // outlive the login session. Locking the screen does not stop it (a
  // lock is not a logout), and a boundary crossed while the machine
  // was off or asleep is caught by the first poll after it comes back,
  // within five minutes, rather than skipped. Worth stating plainly:
  // "every hour" reads as a promise the machine cannot keep while it
  // is switched off. ----------------------------------------------
  Text {
    width: parent.width
    wrapMode: Text.WordWrap
    text: panel.configState.frequency === "manual"
      ? "Nothing generates on its own — Regenerate is the only trigger."
      : "Runs only while the computer is on and you are logged in; a locked "
        + "screen still counts. Anything due while it was off, asleep or "
        + "logged out runs shortly after you are back."
    color: Qt.darker(panel.bar.foreground, 1.5)
    font.family: panel.bar.fontFamily
    font.pixelSize: Style.font.caption
  }
}
