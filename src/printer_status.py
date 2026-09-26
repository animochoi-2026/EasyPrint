"""프린터 스풀러 상태 조회 (용지 걸림/오프라인 등 하드웨어 장애 감지).

설계 계획서 4장 "프린터 장애 처리": 단순 응답 지연만으로 장애로 오판하지 않는다
(고해상도/대용량 문서는 정상적으로도 오래 걸릴 수 있음). "프로세스가 오래 응답
없음" + "스풀러가 실제 오류 상태" 두 조건이 함께 확인될 때만 장애로 판정한다.
"""
from __future__ import annotations

try:
    import win32print
except ImportError:
    win32print = None

_FAULT_FLAGS = 0
if win32print is not None:
    _FAULT_FLAGS = (
        win32print.PRINTER_STATUS_PAPER_JAM
        | win32print.PRINTER_STATUS_PAPER_OUT
        | win32print.PRINTER_STATUS_PAPER_PROBLEM
        | win32print.PRINTER_STATUS_OFFLINE
        | win32print.PRINTER_STATUS_ERROR
        | win32print.PRINTER_STATUS_NOT_AVAILABLE
        | win32print.PRINTER_STATUS_DOOR_OPEN
        | win32print.PRINTER_STATUS_NO_TONER
        | win32print.PRINTER_STATUS_OUTPUT_BIN_FULL
        | win32print.PRINTER_STATUS_OUT_OF_MEMORY
        | win32print.PRINTER_STATUS_USER_INTERVENTION
    )


def has_printer_fault(printer_name: str | None) -> bool:
    """스풀러 상태 플래그에 실제 하드웨어 오류로 볼 수 있는 비트가 있으면 True.
    조회 자체가 실패하면(권한, 이름 불일치 등) 장애로 오판하지 않고 False."""
    if win32print is None or not printer_name:
        return False
    try:
        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
        finally:
            win32print.ClosePrinter(handle)
    except Exception:
        return False
    return bool(info.get("Status", 0) & _FAULT_FLAGS)


def cancel_print_jobs(printer_name: str | None) -> int:
    """설계 계획서 4장 "강제 취소": 해당 프린터의 스풀러 대기열에 있는 잡을 전부
    삭제 시도한다. 이미 프린터 하드웨어로 넘어간 페이지는 Windows 특성상 지워지지
    않을 수 있음 — 그래도 예외를 던지지 않고 시도한 것만으로 최선을 다한다.
    반환값은 삭제 시도한 잡 개수(성공 여부와 무관)."""
    if win32print is None or not printer_name:
        return 0
    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception:
        return 0
    attempted = 0
    try:
        jobs = win32print.EnumJobs(handle, 0, -1, 1)
        for job in jobs:
            try:
                win32print.SetJob(handle, job["JobId"], 0, None, win32print.JOB_CONTROL_DELETE)
            except Exception:
                pass
            attempted += 1
    except Exception:
        pass
    finally:
        win32print.ClosePrinter(handle)
    return attempted
