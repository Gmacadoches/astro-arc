import QtQuick
import Quickshell.Io
import qs.Ui

// Every text input in this panel is a ClipboardSafeField. Qt reads the
// whole clipboard into an *editable* text field by itself: on every
// clipboard change (to update canPaste) and whenever readOnly flips, with
// no paste, no size limit and no deadline, so a streaming clipboard owner
// can stall or exhaust the shared shell. Each of those reads, and the
// middle-click selection paste, is skipped while readOnly is true
// (qquicktextinput.cpp q_canPasteChanged/canPaste/setReadOnly/
// mousePressEvent). So this field is readOnly for its whole life and never
// flips: it does its own typing in Keys.onPressed, and reads the clipboard
// only on an explicit Ctrl+V / Shift+Insert, through wl-paste in a separate
// process with a 2 s deadline and a byte cap, so even a hostile owner can't
// block the shell. Navigation, selection, Ctrl+A, copy, Return
// (accepted/editingFinished) and focus-out editingFinished are stock
// TextInput behavior and work unchanged on a readOnly field.
TextField {
  id: safeField
  readOnly: true
  // readOnly hides the cursor; show it (solid — Qt only blinks editable
  // fields) whenever the field has focus.
  cursorVisible: activeFocus
  // Bytes taken from the clipboard per paste: room for maximumLength
  // characters of 4-byte UTF-8, at most 64 KiB.
  readonly property int pasteByteLimit: Math.min(maximumLength * 4, 65536)

  function replaceSelection(insert) {
    var start = selectionStart
    var end = selectionEnd
    var room = maximumLength - (text.length - (end - start))
    if (insert.length > room) insert = insert.slice(0, Math.max(0, room))
    var next = text.slice(0, start) + insert + text.slice(end)
    text = next
    // A field's onTextChanged may reformat (the birth fields re-insert
    // separators); the caret then goes to the end, as stock Qt did.
    cursorPosition = text === next ? start + insert.length : text.length
  }

  function deleteBack(wordwise) {
    if (selectionStart !== selectionEnd) { replaceSelection(""); return }
    var pos = cursorPosition
    if (pos === 0) return
    var from = pos - 1
    if (wordwise) {
      while (from > 0 && /\s/.test(text.charAt(from))) from--
      while (from > 0 && !/\s/.test(text.charAt(from - 1))) from--
    }
    select(from, pos)
    replaceSelection("")
  }

  function deleteForward(wordwise) {
    if (selectionStart !== selectionEnd) { replaceSelection(""); return }
    var pos = cursorPosition
    if (pos === text.length) return
    var to = pos + 1
    if (wordwise) {
      while (to < text.length && /\s/.test(text.charAt(to))) to++
      while (to < text.length && !/\s/.test(text.charAt(to))) to++
    }
    select(pos, to)
    replaceSelection("")
  }

  function pasteFromClipboard() {
    if (pasteProc.running) return
    pasteProc.command = ["sh", "-c",
      "timeout 2 wl-paste --no-newline --type text 2>/dev/null | head -c \"$1\"",
      "sh", String(safeField.pasteByteLimit)]
    pasteProc.running = true
  }

  Keys.onPressed: function(event) {
    var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
    var shift = (event.modifiers & Qt.ShiftModifier) !== 0
    if ((ctrl && event.key === Qt.Key_V) || (shift && event.key === Qt.Key_Insert)) {
      pasteFromClipboard(); event.accepted = true
    } else if (ctrl && event.key === Qt.Key_X) {
      if (echoMode === TextInput.Normal) copy()
      replaceSelection(""); event.accepted = true
    } else if (event.key === Qt.Key_Backspace) {
      deleteBack(ctrl); event.accepted = true
    } else if (event.key === Qt.Key_Delete) {
      deleteForward(ctrl); event.accepted = true
    } else if (!ctrl && (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)) === 0
               && event.text.length > 0 && !/[\x00-\x1f\x7f]/.test(event.text)) {
      replaceSelection(event.text); event.accepted = true
    }
  }

  Process {
    id: pasteProc
    stdout: StdioCollector {
      waitForEnd: true
      // Single-line field: line breaks and tabs become spaces, other
      // control characters are dropped.
      onStreamFinished: if (safeField.activeFocus && text.length > 0)
        safeField.replaceSelection(text.replace(/[\r\n\t]+/g, " ").replace(/[\x00-\x1f\x7f]/g, ""))
    }
  }
}
