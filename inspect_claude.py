"""Read-only accessibility diagnostic; omits conversation text by default."""
import json
import queue
import sys

import claude_auto_continue as app
from session_automation import discover, signals


def inspect():
    with app.auto.UIAutomationInitializerInThread():
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        windows = worker._enum_windows()
        result = []
        for hwnd, title in windows:
            worker.hwnd = hwnd
            if "--summary" in sys.argv:
                panes, entries = discover(worker.engine.ui.snapshot())
                result.append(dict(hwnd=hwnd, sidebar_count=len(entries), panes=[
                    dict(title=p.title, kind=p.kind,
                         question=bool(signals(p, app.detect_limit_banner)["question"]),
                         recommended=len((signals(p, app.detect_limit_banner)["question"] or {}).get("recommended", [])),
                         draft=bool(worker.engine.ui.value(p.prompt))) for p in panes]))
                continue
            win = app.auto.ControlFromHandle(hwnd)
            worker._wake_accessibility(win)
            excluded = None
            nodes = []
            for ctrl, depth in app.auto.WalkControl(win, maxDepth=100):
                if excluded is not None and depth > excluded:
                    continue
                excluded = None
                try:
                    name = ctrl.Name or ""
                    r = ctrl.BoundingRectangle
                    nodes.append(dict(depth=depth, type=ctrl.ControlTypeName,
                                      name=name, id=ctrl.AutomationId,
                                      rect=[r.left, r.top, r.right, r.bottom]))
                    if name.lower() == "chat messages":
                        excluded = depth
                except Exception:
                    pass
            result.append(dict(hwnd=hwnd, title=title, nodes=nodes))
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    inspect()
