"""PDF 인쇄 엔진 연동 (SumatraPDF 포터블).

HWP/HWPX 인쇄는 src/hwp_printing.py에서 별도로 처리한다 (한글 인쇄 관리자
HwpPrnMng.exe 기반 — win32com 자동화는 포기한 이유는 그 파일 상단 docstring 참고).
"""
from __future__ import annotations

import os
import time

from .constants import FILE_LOCK_RETRY_COUNT, get_sumatra_path
from .pdf_flatten import prepare_print_path
from .print_log import log_event
from .print_process import run_print_process

FILE_LOCK_RETRY_DELAY_SEC = 0.5
PRINT_TIMEOUT_SEC = None  # Wait until completion, cancellation, or printer fault.


def check_file_ready(path: str) -> tuple[bool, str | None]:
    """설계 계획서 4장 "파일 잠금(File Lock) / 원본 파일 오류 처리" 참고.

    - 파일 없음: 즉시 실패
    - 다른 프로그램이 잠금: 짧게 재시도 후 실패
    - 읽기 권한 없음: 즉시 실패
    """
    if not os.path.exists(path):
        return False, "파일이 존재하지 않습니다"

    last_reason: str | None = None
    attempts = FILE_LOCK_RETRY_COUNT + 1
    for attempt in range(attempts):
        try:
            fd = os.open(path, os.O_RDONLY)
            os.close(fd)
            return True, None
        except PermissionError as exc:
            if getattr(exc, "winerror", None) == 5:
                return False, "파일 접근 권한이 없습니다"
            last_reason = "파일이 다른 프로그램에서 사용 중입니다"
        except OSError:
            last_reason = "파일에 접근할 수 없습니다"
        if attempt < attempts - 1:
            time.sleep(FILE_LOCK_RETRY_DELAY_SEC)
    return False, last_reason or "파일에 접근할 수 없습니다"


def print_pdf(
    path: str,
    printer: str | None,
    copies: int = 1,
    page_range: str = "",
    abort_event=None,
    on_stage=None,
) -> tuple[bool, str | None, bool]:
    """SumatraPDF 포터블로 PDF 한 건을 인쇄. (성공 여부, 실패 사유, 프린터 장애 여부) 반환.

    copies/page_range는 SumatraPDF의 -print-settings 인자로 전달한다
    (예: "1-3,5,2x" = 1-3쪽과 5쪽을 2부씩). page_range는 이미 검증된 값이라고 가정한다.
    abort_event가 세팅되면(강제 취소) 진행 중인 프로세스를 즉시 종료한다.
    """
    sumatra = get_sumatra_path()
    if not os.path.exists(sumatra):
        return False, f"SumatraPDF.exe를 찾을 수 없습니다: {sumatra}", False

    args = [sumatra, "-silent", "-exit-when-done"]
    args += ["-print-to", printer] if printer else ["-print-to-default"]

    settings_parts: list[str] = []
    if page_range.strip():
        settings_parts.append(page_range.strip())
    if copies > 1:
        settings_parts.append(f"{copies}x")
    if settings_parts:
        args += ["-print-settings", ",".join(settings_parts)]

    with prepare_print_path(path, on_stage=on_stage) as print_path:
        args.append(print_path)
        log_event(f"PDF 인쇄 전달: {path} | 범위={page_range.strip() or '전체'} | 매수={copies} | 실제 파일={print_path}")
        ok, reason, is_fault = run_print_process(args, PRINT_TIMEOUT_SEC, printer, abort_event=abort_event)

    if not ok and not is_fault and reason and reason.startswith("종료 코드"):
        reason = f"SumatraPDF {reason}"
    return ok, reason, is_fault
