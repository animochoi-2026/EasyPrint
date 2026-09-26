"""인쇄 리스트의 데이터 모델.

설계 계획서 1장: 대기/큐/완료/실패를 나누지 않고 단일 리스트 + 상태 필드로 관리한다.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from enum import Enum


class PrintStatus(Enum):
    WAITING = "waiting"    # 아이콘 없음: 아직 인쇄 안 된 대기 항목
    PRINTING = "printing"  # ▶ 현재 인쇄 중 (고정, 이동 불가)
    PAUSED = "paused"      # ⏸ 정지로 인해 대기 중단된 항목
    DONE = "done"          # ✅ 인쇄 완료
    FAILED = "failed"      # ❌ 인쇄 실패


# 상태별 아이콘 (계획서 3장 "상태 아이콘")
STATUS_ICONS: dict[PrintStatus, str] = {
    PrintStatus.WAITING: "",
    PrintStatus.PRINTING: "▶",
    PrintStatus.PAUSED: "⏸",
    PrintStatus.DONE: "✅",
    PrintStatus.FAILED: "❌",
}

# 워커가 "아직 처리 안 된 것"으로 간주해 다음 작업으로 고를 수 있는 상태
PENDING_STATUSES = (PrintStatus.WAITING, PrintStatus.PAUSED)

# 정리(자동/수동) 대상이 될 수 있는 상태 — 대기/인쇄중 항목은 절대 제외
CLEANUP_ELIGIBLE_STATUSES = (PrintStatus.DONE, PrintStatus.FAILED)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")
SUPPORTED_EXTENSIONS = (".pdf", ".hwp", ".hwpx", ".docx", *IMAGE_EXTENSIONS)


def supported_extension(path: str) -> str | None:
    """지원하는 확장자면 소문자 확장자를, 아니면 None을 반환."""
    ext = os.path.splitext(path)[1].lower()
    if ext in SUPPORTED_EXTENSIONS:
        return ext
    return None


@dataclass
class PrintItem:
    file_path: str
    copies: int = 1
    page_range: str = ""  # 빈 문자열 = 전체 범위
    status: PrintStatus = PrintStatus.WAITING
    fail_reason: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    page_count: int | None = None  # PDF 페이지 수 캐시 (지연 조회, 계획서 5장)
    stage_note: str | None = None  # 인쇄 전 중간 단계 안내(예: "빠른인쇄용 변환 중...") — 저장 안 함, 순전히 UI용
    page_count_checked: bool = False

    @property
    def file_name(self) -> str:
        return os.path.basename(self.file_path)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.file_path)[1].lower()

    @property
    def icon(self) -> str:
        return STATUS_ICONS[self.status]

    def is_locked_in_place(self) -> bool:
        """현재 인쇄 중인 항목만 이동/삭제 불가 (계획서 3장)."""
        return self.status == PrintStatus.PRINTING
