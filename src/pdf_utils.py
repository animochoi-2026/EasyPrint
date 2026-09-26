"""PDF 메타데이터 조회 (페이지 수만, 지연 조회용).

설계 계획서 5장: 파일 추가 시점에 전부 미리 읽지 않고, 페이지범위 입력창을
실제로 사용하는 순간에만 pypdf로 조회한다.
"""
from __future__ import annotations


def get_pdf_page_count(path: str) -> int | None:
    try:
        from pypdf import PdfReader

        with open(path, "rb") as stream:
            reader = PdfReader(stream)
            return len(reader.pages)
    except Exception:
        return None
