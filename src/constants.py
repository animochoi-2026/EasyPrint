"""공통 상수 및 경로 유틸리티.

개발 중(python main.py로 실행)과 PyInstaller --onedir로 빌드된 이후를 구분해
리소스(SumatraPDF.exe 등) 경로를 올바르게 찾아준다.

빌드 후 "쓰기 가능한" 파일(settings.json/print.log 등)은 .exe와 같은 폴더에
남아야 사용자가 찾기 쉬우므로 get_base_dir()(=exe 폴더)을 기준으로 삼는다.
반면 SumatraPDF.exe처럼 "읽기 전용으로 번들된" 리소스는 PyInstaller 6.x부터
_internal 서브폴더에 들어가는데, 그 폴더 이름은 버전마다 바뀔 수 있어 하드코딩하지
않고 PyInstaller가 공식 제공하는 sys._MEIPASS(번들 경로)를 기준으로 찾는다.
"""
from __future__ import annotations

import os
import sys

APP_TITLE = "Easy Print"

# 최근 유지할 완료(✅) 항목 개수 (계획서 3장 "리스트 길이 관리")
MAX_DONE_ITEMS = 20

# 실패 후 파일 잠금 자동 재시도 횟수 (계획서 4장 "파일 잠금 처리")
FILE_LOCK_RETRY_COUNT = 3


def is_frozen() -> bool:
    """PyInstaller로 빌드되어 실행 중인지 여부."""
    return bool(getattr(sys, "frozen", False))


def get_base_dir() -> str:
    """실행 파일(빌드 후) 또는 프로젝트 루트(개발 중) 경로 — 설정/로그처럼
    사용자가 직접 찾아볼 수도 있는 쓰기 가능한 파일들의 기준 폴더."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    # src/ 의 부모 = 프로젝트 루트
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_bundled_resource_dir() -> str:
    """SumatraPDF.exe처럼 읽기 전용으로 번들된 리소스를 찾을 기준 경로."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return get_base_dir()


def get_sumatra_path() -> str:
    """SumatraPDF 포터블 실행파일 경로.

    개발 중: <프로젝트 루트>/assets/SumatraPDF.exe
    빌드 후: <PyInstaller 번들 경로>/SumatraPDF.exe (--add-binary로 포함됨)
    """
    base = get_bundled_resource_dir()
    if is_frozen():
        return os.path.join(base, "SumatraPDF.exe")
    return os.path.join(base, "assets", "SumatraPDF.exe")


def get_settings_path() -> str:
    return os.path.join(get_base_dir(), "settings.json")


def get_queue_snapshot_path() -> str:
    return os.path.join(get_base_dir(), "queue_state.json")


def get_log_path() -> str:
    return os.path.join(get_base_dir(), "print.log")
