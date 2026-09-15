"""Opt-in smoke checks against an open Claude window. No messages are sent.

Run `py test_live_claude.py` for discovery, or add `--select-and-restore`
to select a recommended answer, check Submit, and restore the selection.
"""
import argparse
import queue

import claude_auto_continue as app
from session_automation import discover, question_in, signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select-and-restore", action="store_true")
    parser.add_argument("--walk-sidebar", action="store_true",
                        help="Open one loaded sidebar conversation, then restore the original panes")
    parser.add_argument("--type-and-restore", action="store_true",
                        help="Exercise literal text entry in the empty Other field, then clear it")
    parser.add_argument("--composer-and-restore", action="store_true",
                        help="Exercise literal text in an idle empty composer, then clear it without sending")
    args = parser.parse_args()
    with app.auto.UIAutomationInitializerInThread():
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        windows = worker._enum_windows()
        if len(windows) != 1:
            raise RuntimeError("This smoke test needs exactly one Claude window")
        worker.hwnd = windows[0][0]
        ui = worker.engine.ui
        panes, entries = discover(ui.snapshot())
        assert panes, "No named conversation panes found"
        print(f"Found {len(panes)} conversation panes and {len(entries)} sidebar entries")
        for p in panes:
            print(f"Pane {p.kind}: composer readable, draft={bool(ui.value(p.prompt))}")
        if args.composer_and_restore:
            candidates = [p for p in panes if not question_in(p) and not
                          signals(p, app.detect_limit_banner)["busy"] and not ui.value(p.prompt)]
            assert candidates, "No idle empty composer"
            p = candidates[0]
            try:
                ui.type_into(p.prompt, "Auto-Resume local input test: {braces} + ąćęłńóśźż")
                print("Composer accepted literal text in the intended conversation")
                current = ui.resolve(p.key, navigate=False)
                sends = [n for n in current.ui_nodes() if n.type == "ButtonControl" and
                         n.name.casefold() in ("send", "send message", "wyślij")]
                assert any(n.control.IsEnabled for n in sends), "Input did not enable Send"
                print("Claude reacted to the input; Send is enabled")
            finally:
                current = ui.resolve(p.key, navigate=False)
                assert current, "Target pane changed"
                current.prompt.control.GetValuePattern().SetValue("")
                current = ui.resolve(p.key, navigate=False)
                assert not ui.value(current.prompt), "Composer restore failed"
                print("Original empty composer restored; no message sent")
        if args.walk_sidebar:
            original = {p.key for p in panes}
            target = next(e for e in entries if e["key"] not in original)
            try:
                opened = ui.resolve(target["key"])
                assert opened and opened.key == target["key"], "Wrong conversation opened"
                assert not ui.value(opened.prompt), "Test target has a draft; no typing will occur"
                print("Sidebar navigation resolved the requested title and its own composer")
            finally:
                current = {p.key for p in discover(ui.snapshot())[0]}
                for key in original - current:
                    assert ui.resolve(key), "Could not restore original pane"
                assert original.issubset({p.key for p in discover(ui.snapshot())[0]})
                print("Original conversation panes restored")
        if not (args.select_and_restore or args.type_and_restore):
            return
        questions = [(p, question_in(p)) for p in panes if question_in(p)]
        assert len(questions) == 1, "Expected exactly one live question"
        p, q = questions[0]
        print(f"Question card: {len(q['options'])} options, main button {q['submit'].name!r}")
        if ui.selection_kind(q["options"][0]) != "toggle":
            # On a single-choice card a click (or typing in Other) already picks an
            # answer that cannot be undone, so the restore checks cannot run safely.
            print("Single-choice card: select/type checks skipped; nothing was touched")
            return
        if args.type_and_restore:
            field = q["other"]
            assert field and not ui.value(field), "Other field must be empty"
            old_other = next(o for o, label in zip(q["options"], q["labels"]) if label == "Other")
            old_selected = old_other.control.GetTogglePattern().ToggleState
            try:
                ui.type_into(field, "Pick your recommended option(s). {test} + ąćęłńóśźż")
                print("Literal text, braces and Polish characters verified in the Other field")
            finally:
                current = question_in(ui.resolve(p.key, navigate=False))
                assert current, "Question changed during smoke test"
                current["other"].control.GetValuePattern().SetValue("")
                other = next(o for o, label in zip(current["options"], current["labels"]) if label == "Other")
                if other.control.GetTogglePattern().ToggleState != old_selected:
                    ui.click(other)
                assert not ui.value(current["other"])
                print("Original empty Other field restored; no answer submitted")
        if not args.select_and_restore:
            return
        assert len(q["recommended"]) == 1, "Expected one recommended option"
        option = q["recommended"][0]
        pattern = option.control.GetTogglePattern()
        assert pattern is not None and pattern.ToggleState == 0, "Answer already selected / unreadable"
        try:
            ui.click(option)
            current = question_in(ui.resolve(p.key, navigate=False))
            assert current["recommended"][0].control.GetTogglePattern().ToggleState == 1
            assert current["submit"].control.IsEnabled
            print("Recommended answer selected; Submit is enabled")
        finally:
            current = question_in(ui.resolve(p.key, navigate=False))
            if current and current["fingerprint"] == q["fingerprint"]:
                option = current["recommended"][0]
                if option.control.GetTogglePattern().ToggleState == 1:
                    ui.click(option)
                current = question_in(ui.resolve(p.key, navigate=False))
                assert current["recommended"][0].control.GetTogglePattern().ToggleState == 0
                print("Original unselected state restored; no answer submitted")


if __name__ == "__main__":
    main()
