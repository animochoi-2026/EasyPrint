"""HWP/HWPX 인쇄 엔진.

COM 자동화(win32com.client.Dispatch("HWPFrame.HwpObject"))로 시도했으나
이 PC에서는 (1) RegisterModule("FilePathCheckerModuleExample", ...)가 억제해야 할
"파일 접근 시도" 보안 확인창이 실제로는 억제되지 않고(그 모듈이 시스템에 아예
등록되어 있지 않음), (2) 확인창을 수동으로 넘겨도 한/글 자체가 .NET
TargetInvocationException으로 죽는 문제가 있어 포기했다.

대신 탐색기에서 .hwp 파일을 우클릭 → "한글 문서(Hwp) 인쇄"를 선택했을 때 실행되는
것과 동일한 명령(레지스트리의 Hwp.Document.100\\shell\\print\\command에 등록된
HwpPrnMng.exe /p)을 그대로 사용한다. 이 경로는 COM 자동화가 아니라서 그 보안
확인창을 타지 않고, 한컴 공식 도움말(HwpPrnMng.chm)에 따르면 대화상자가 잠깐
보였다가 자동으로 인쇄되는 방식이라 사람이 클릭할 필요가 없다.

**대신 제약이 있다**: HwpPrnMng.exe /p는 명령줄로 프린터·매수·페이지범위를 지정할
방법이 없고, 항상 "그 순간의 Windows 기본 프린터"로 "문서 전체 1부"만 인쇄한다.
그래서 여기서는:
  - 프린터: 인쇄 직전 Windows 기본 프린터를 원하는 프린터로 잠깐 바꿨다가 되돌린다
    (앱 자체가 프린터 선택 시 이미 Windows 기본 프린터를 바꾸도록 되어 있어
    보통은 이미 일치하지만, 그 사이 바뀌었을 가능성에 대비한 안전장치).
  - 매수: HwpPrnMng.exe를 매수만큼 반복 실행한다.
  - 페이지범위: 지원 불가 — 항상 전체 문서를 인쇄한다 (요청한 범위는 무시됨).
"""
from __future__ import annotations

import os
import shlex
import time
import winreg

from .print_process import local_copy_for_print, run_print_process

try:
    import win32print
except ImportError:
    win32print = None

PRINT_TIMEOUT_SEC = None  # Wait until completion, cancellation, or printer fault.

_cached_exe_path: str | None = None


def _print_command_for_progid(progid: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{progid}\\shell\\print\\command") as key:
            cmd = winreg.QueryValueEx(key, None)[0]
        return shlex.split(cmd, posix=False)[0].strip('"')
    except (OSError, IndexError):
        return None


def get_hwp_prn_mng_path() -> str | None:
    """레지스트리에서 실제 설치된 한/글 인쇄 관리자(HwpPrnMng.exe) 경로를 읽어온다.

    ProgID는 ".hwp" 확장자 연결을 통해 알아낸다(탐색기가 "한글 문서(Hwp) 인쇄"를
    찾는 방식과 동일) — "Hwp.Document.100"은 한컴오피스 NEO 계열(2018 등)의
    ProgID일 뿐이고, 한컴오피스 2010 같은 구버전은 "Hwp.Document.8"처럼 다른
    번호를 쓴다. 확장자 연결 조회가 실패할 때만 과거 하드코딩 값을 마지막
    수단으로 시도한다."""
    global _cached_exe_path
    if _cached_exe_path is not None:
        return _cached_exe_path

    exe = None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ".hwp") as key:
            progid = winreg.QueryValueEx(key, None)[0]
        if progid:
            exe = _print_command_for_progid(progid)
    except OSError:
        pass

    if exe is None:
        exe = _print_command_for_progid("Hwp.Document.100")

    if exe is None or not os.path.exists(exe):
        return None
    _cached_exe_path = exe
    return exe


def _temporary_default_printer(printer: str | None):
    """printer로 Windows 기본 프린터를 잠깐 바꾸는 컨텍스트 매니저.
    실패해도 예외를 던지지 않고 그냥 원래 프린터로 진행한다."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        if not printer or win32print is None:
            yield
            return
        try:
            original = win32print.GetDefaultPrinter()
        except Exception:
            yield
            return
        if original == printer:
            yield
            return
        try:
            win32print.SetDefaultPrinter(printer)
        except Exception:
            yield
            return
        try:
            yield
        finally:
            try:
                win32print.SetDefaultPrinter(original)
            except Exception:
                pass

    return _cm()


def print_hwp(
    path: str, printer: str | None, copies: int = 1, page_range: str = "", abort_event=None
) -> tuple[bool, str | None, bool]:
    """한/글 인쇄 관리자(HwpPrnMng.exe /p)로 인쇄. (성공 여부, 실패 사유, 프린터 장애 여부) 반환.

    page_range는 이 엔진이 지원하지 않아 무시되고 항상 전체 문서가 인쇄된다.
    abort_event가 세팅되면(강제 취소) 진행 중인 프로세스를 즉시 종료하고 남은 매수는 건너뛴다.
    """
    if page_range.strip():
        return False, "한글 문서는 전체 페이지 인쇄만 지원합니다. 범위를 비워주세요.", False
    exe = get_hwp_prn_mng_path()
    if exe is None:
        return (
            False,
            "한글 인쇄 관리자(HwpPrnMng.exe)를 찾을 수 없습니다. 한컴오피스가 설치되어 있는지 확인하세요.",
            False,
        )

    with _temporary_default_printer(printer), local_copy_for_print(path) as print_path:
        for copy_index in range(max(1, copies)):
            ok, reason, is_fault = run_print_process(
                [exe, "/p", print_path], PRINT_TIMEOUT_SEC, printer, abort_event=abort_event
            )
            if not ok:
                suffix = f" ({copy_index + 1}/{copies}부)" if copies > 1 else ""
                return False, f"{reason}{suffix}", is_fault

            if copies > 1 and copy_index < copies - 1:
                time.sleep(1.0)  # 이전 인쇄 잡이 스풀에 먼저 들어가도록 약간 간격

    return True, None, False
