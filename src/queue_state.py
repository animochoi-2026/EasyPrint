"""인쇄 리스트 스냅샷 (비정상 종료 복구용).

설계 계획서 4장 "프로그램 종료/재시작": 진행 중일 때 리스트가 바뀔 때마다
queue_state.json에 현재 상태를 스냅샷으로 남기고, 정상 종료 시 삭제한다.
다음 실행 시 이 파일이 남아있으면 비정상 종료로 간주해 복구 여부를 물어본다.
"""
from __future__ import annotations

import json
import os

from .constants import get_queue_snapshot_path
from .models import PrintItem, PrintStatus


def save_queue_snapshot(items: list[PrintItem]) -> None:
    data = [
        {
            "file_path": i.file_path,
            "copies": i.copies,
            "page_range": i.page_range,
            "status": i.status.value,
            "fail_reason": i.fail_reason,
        }
        for i in items
    ]
    try:
        with open(get_queue_snapshot_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def load_queue_snapshot() -> list[PrintItem] | None:
    """스냅샷 파일이 없으면 None. 읽는 데 실패해도(손상된 JSON 등) None —
    비정상 종료로 오판해 계속 복구 팝업을 띄우는 것보다 조용히 새로 시작하는 편이 낫다."""
    path = get_queue_snapshot_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    items: list[PrintItem] = []
    for entry in data:
        try:
            status = PrintStatus(entry["status"])
        except (KeyError, ValueError):
            status = PrintStatus.WAITING
        if status == PrintStatus.PRINTING:
            # 크래시 시점에 인쇄 중이던 항목은 실제로 끝났는지 알 수 없다.
            # 워커가 없으면 영원히 ▶로 남을 수 있으므로 다시 대기시켜 재시도되게 한다.
            status = PrintStatus.WAITING
        items.append(
            PrintItem(
                file_path=entry.get("file_path", ""),
                copies=entry.get("copies", 1),
                page_range=entry.get("page_range", ""),
                status=status,
                fail_reason=entry.get("fail_reason"),
            )
        )
    return items


def clear_queue_snapshot() -> None:
    try:
        os.remove(get_queue_snapshot_path())
    except OSError:
        pass
