"""배치 파일(easyprint.bat)이 pythonw.exe로 실행하면 콘솔 창이 아예 없어서
표준 에러 출력이 갈 곳이 없다 — 시작 중 예외가 나도 아무 창도 안 뜨고 조용히
꺼져버리면 사용자도 나도 원인을 알 수 없으므로, 처리되지 않은 예외는 항상
crash.log에 남긴다."""
from __future__ import annotations

import os
import traceback
import sys
import multiprocessing

from src.constants import get_base_dir


def main() -> None:
    # Non-printing diagnostic path also exercises the frozen spawn worker.
    # Exit 0: Word available; 2: unavailable. No queue/UI or documents opened.
    if sys.argv[1:] == ["--check-word"]:
        from src.document_printing import check_word_available, WordUnavailableError
        from src.print_log import log_event
        try:
            check_word_available()
        except WordUnavailableError as exc:
            log_event(f"Word 확인 실패 (인쇄 없음): {exc}")
            raise SystemExit(2)
        log_event("Word 확인 성공 (인쇄 없음)")
        return
    from src.app import App
    from src.instance import InstanceInbox
    inbox = InstanceInbox()
    try:
        paths = [os.path.abspath(p) for p in sys.argv[1:] if p != "--"]
        if paths or not inbox.primary:
            inbox.send(paths)
        if not inbox.primary:
            return
        app = App()
        def poll_inbox():
            received, paths = inbox.receive()
            if paths:
                app.add_files(paths)
            if received:
                app.deiconify()
                app.lift()
            app.after(250, poll_inbox)
        app.after(100, poll_inbox)
        app.mainloop()
    finally:
        inbox.close()


if __name__ == "__main__":
    try:
        multiprocessing.freeze_support()
        main()
    except Exception:
        crash_log = os.path.join(get_base_dir(), "crash.log")
        with open(crash_log, "a", encoding="utf-8") as f:
            f.write("\n" + traceback.format_exc())
        raise
