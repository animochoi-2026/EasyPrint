"""인쇄용 서브프로세스를 실행하면서 프린터 장애를 함께 감지하는 공용 러너.

SumatraPDF(printing.py)와 HwpPrnMng.exe(hwp_printing.py) 양쪽에서 재사용한다.
설계 계획서 4장: 응답이 느려지고(slow_threshold_sec 이상) + 스풀러가 실제 오류
상태일 때만 "프린터 장애"로 판정해서 개별 파일 실패와 구분한다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager

from .printer_status import has_printer_fault

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@contextmanager
def local_copy_for_print(path: str):
    """인쇄 프로그램(SumatraPDF/HwpPrnMng.exe)에 넘길 로컬 임시 복사본을 만든다.

    한컴오피스 2010의 HwpPrnMng.exe가 NAS/UNC 네트워크 경로를 받으면 파일을 못
    찾고 "검색된 인쇄 가능한 파일이 없습니다"를 띄우는 문제가 있어서 도입했다.
    같은 NAS 환경에서 SumatraPDF도 동일하게 실패한다는 사용자 확인이 있어
    PDF 인쇄에도 동일하게 적용한다. 원본 경로가 어디든 상관없이 항상 로컬 임시
    폴더에 복사한 뒤 그 복사본 경로로 인쇄하고, 끝나면 삭제한다. 복사에 실패하면
    (예: 원본이 이미 지워짐) 원본 경로를 그대로 쓴다."""
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="easyprint_print_")
        local_path = os.path.join(temp_dir, os.path.basename(path))
        shutil.copyfile(path, local_path)
    except OSError:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        yield path
        return
    try:
        yield local_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_print_process(
    args: list[str],
    timeout_sec: float | None,
    printer: str | None,
    slow_threshold_sec: float = 15.0,
    poll_interval_sec: float = 1.0,
    abort_event: threading.Event | None = None,
) -> tuple[bool, str | None, bool]:
    """(성공 여부, 실패 사유, 프린터 장애 여부)를 반환.

    실패 사유가 있어도 printer_fault=False면 "이 파일만의 문제"이고,
    printer_fault=True면 "프린터 자체 장애"라 호출한 쪽이 전체 큐를 멈춰야 한다.
    abort_event가 세팅되면(강제 취소) 다음 폴링 주기 안에 프로세스를 죽이고 즉시 반환한다.
    """
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW
        )
    except OSError as exc:
        return False, f"실행 오류: {exc}", False

    start = time.monotonic()
    while True:
        if abort_event is not None and abort_event.is_set():
            proc.kill()
            proc.wait()
            return False, "사용자 요청으로 취소됨", False
        try:
            returncode = proc.wait(timeout=poll_interval_sec)
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            if timeout_sec is not None and elapsed > timeout_sec:
                proc.kill()
                proc.wait()
                return False, "인쇄 시간 초과", False
            if elapsed > slow_threshold_sec and has_printer_fault(printer):
                proc.kill()
                proc.wait()
                return False, "프린터 오류 감지 (용지 걸림/오프라인 등)", True
            continue
        else:
            if returncode != 0:
                return False, f"종료 코드 {returncode}", False
            return True, None, False
