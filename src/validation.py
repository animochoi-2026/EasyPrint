"""페이지 범위 입력값 검증.

설계 계획서 5장 "페이지 범위 입력 검증 규칙" 표를 그대로 구현한다.
"""
from __future__ import annotations


def parse_page_range(text: str, page_count: int | None) -> tuple[bool, str | None]:
    """(정상 여부, 오류 사유) 반환. 오류가 없으면 사유는 None.

    page_count가 None이면(아직 페이지 수를 모르면) "범위 초과" 검사는 건너뛴다.
    """
    text = text.strip()
    if text == "":
        return True, None  # 빈 값 = 전체범위

    for part in text.split(","):
        if part == "":
            return False, "빈 항목이 포함된 잘못된 구분자입니다"

        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                return False, "올바른 범위 형식이 아닙니다 (예: 1-3)"
            start_s, end_s = bounds
            if not start_s.isdigit() or not end_s.isdigit():
                return False, "숫자가 아닌 문자가 포함되어 있습니다"
            start, end = int(start_s), int(end_s)
            if start < 1 or end < 1:
                return False, "페이지 번호는 1 이상이어야 합니다"
            if start > end:
                return False, "시작 페이지가 끝 페이지보다 큽니다"
            if page_count is not None and end > page_count:
                return False, f"문서의 실제 페이지 수({page_count}쪽)를 초과했습니다"
        else:
            if not part.isdigit():
                return False, "숫자가 아닌 문자가 포함되어 있습니다"
            page = int(part)
            if page < 1:
                return False, "페이지 번호는 1 이상이어야 합니다"
            if page_count is not None and page > page_count:
                return False, f"문서의 실제 페이지 수({page_count}쪽)를 초과했습니다"

    return True, None
