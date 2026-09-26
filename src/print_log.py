"""print.log 기록.

설계 계획서 4장 "로그 파일 기록": 이벤트 발생 시점에만 한 줄씩 append하고,
전체 로그를 매번 다시 쓰지 않는다 (리소스 최소화 원칙, 계획서 7장).
"""
from __future__ import annotations

from datetime import datetime

from .constants import get_log_path


def log_event(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
    try:
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
