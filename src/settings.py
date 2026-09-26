"""설정 저장 (재시작 시 유지).

설계 계획서 4장 "설정 저장": 창 크기만 저장한다. 예약 시각·완료 후 자동 처리
체크박스는 App이 실행될 때마다 새로 만들어지므로 별도 처리 없이 자연히 초기화된다.
프린터 선택은 Windows 기본 프린터 자체를 바꾸는 방식으로 전환해(app.py 참고)
여기서는 더 이상 다루지 않는다.
"""
from __future__ import annotations

import json
import os

from .constants import get_settings_path


def load_settings() -> dict:
    path = get_settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        with open(get_settings_path(), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
