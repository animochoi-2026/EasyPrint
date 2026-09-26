"""인쇄가 느린 PDF를 페이지당 이미지 한 장으로 평탄화(flatten)한다.

원인: 도형 문제지처럼 페이지마다 작은 래스터 이미지가 수십 개씩 박혀있는 PDF는
프린터 드라이버(GDI)가 페이지 하나를 그릴 때 그 이미지 개수만큼 별도로
디코딩+블릿해야 해서 매우 느려진다. Adobe Reader의 "이미지로 인쇄" 옵션과 같은
원리로, 각 페이지를 통째로 하나의 이미지로 미리 렌더링한 새 PDF를 만들어
그것을 대신 인쇄하면 페이지당 블릿 횟수가 1번으로 줄어 훨씬 빨라진다.

일반적인 PDF(이미지가 페이지당 몇 개뿐)에는 적용하지 않는다 — 평탄화 자체도
비용이 들고, 텍스트가 선명한 일반 문서를 오히려 흐리게(래스터화) 만들 이유가
없기 때문이다. `should_flatten_for_print()`로 "이미지가 비정상적으로 많은
PDF"만 골라낸다.

**캐시**: 평탄화는 몇 초씩 걸리므로, 같은 파일을 다시 인쇄할 때마다 매번 다시
만들지 않도록 원본과 같은 폴더에 "<원본이름>_빠른인쇄용.pdf"로 결과물을 저장해
둔다(원본이 NAS 공유 폴더에 있으면 다른 선생님이 같은 파일을 인쇄할 때도 이미
만들어진 캐시를 그대로 재사용할 수 있다). 원본의 수정시각+크기를 캐시 PDF의
메타데이터(Keywords)에 적어두고, 다음에 볼 때 원본과 일치하는지 확인해서
원본이 바뀌었으면 캐시를 버리고 다시 만든다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from typing import Callable

from .print_log import log_event
from .print_process import local_copy_for_print

try:
    import pymupdf
except ImportError:
    pymupdf = None

FLATTEN_DPI = 200
JPEG_QUALITY = 80

AVG_IMAGES_PER_PAGE_THRESHOLD = 5
MAX_IMAGES_ON_ANY_PAGE_THRESHOLD = 10

CACHE_TAG = "_빠른인쇄용"
CACHE_MARKER_PREFIX = "EasyPrintFastCache"


def should_flatten_for_print(path: str) -> bool:
    """이미지 개수가 비정상적으로 많은 PDF인지 판단한다 (렌더링 없이 메타데이터만 확인)."""
    if pymupdf is None:
        return False
    try:
        doc = pymupdf.open(path)
    except Exception:
        return False
    try:
        if doc.page_count == 0:
            return False
        total_images = 0
        max_on_page = 0
        for page in doc:
            count = len(page.get_images(full=False))
            total_images += count
            max_on_page = max(max_on_page, count)
        avg = total_images / doc.page_count
        return avg >= AVG_IMAGES_PER_PAGE_THRESHOLD or max_on_page >= MAX_IMAGES_ON_ANY_PAGE_THRESHOLD
    except Exception:
        return False
    finally:
        doc.close()


def flatten_pdf_for_print(path: str, extra_metadata: dict | None = None) -> str | None:
    """평탄화된 새 PDF를 임시 파일로 만들어 그 경로를 반환한다. 실패 시 None
    (호출한 쪽은 원본으로 계속 인쇄하면 된다). 반환된 파일은 호출한 쪽이 지워야 한다."""
    if pymupdf is None:
        return None
    try:
        src = pymupdf.open(path)
    except Exception:
        return None
    out_path: str | None = None
    try:
        out = pymupdf.open()
        try:
            for page in src:
                pix = page.get_pixmap(dpi=FLATTEN_DPI, alpha=False)
                data = pix.tobytes("jpg", jpg_quality=JPEG_QUALITY)
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=data)
            if extra_metadata:
                out.set_metadata(extra_metadata)
            fd, out_path = tempfile.mkstemp(suffix=".pdf", prefix="easyprint_flat_")
            os.close(fd)
            out.save(out_path)
        finally:
            out.close()
        return out_path
    except Exception:
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return None
    finally:
        src.close()


def _cache_path_for(original_path: str) -> str:
    base, ext = os.path.splitext(original_path)
    return f"{base}{CACHE_TAG}{ext}"


def _cache_marker(mtime_ns: int, size: int) -> str:
    return f"{CACHE_MARKER_PREFIX}|mtime_ns={mtime_ns}|size={size}"


def _read_cache_marker(cache_path: str) -> tuple[int, int] | None:
    if pymupdf is None:
        return None
    try:
        doc = pymupdf.open(cache_path)
    except Exception:
        return None
    try:
        keywords = (doc.metadata or {}).get("keywords") or ""
    finally:
        doc.close()
    if not keywords.startswith(f"{CACHE_MARKER_PREFIX}|"):
        return None
    try:
        fields = dict(part.split("=", 1) for part in keywords.split("|")[1:])
        return int(fields["mtime_ns"]), int(fields["size"])
    except Exception:
        return None


def _persist_cache(local_flattened_path: str, cache_path: str) -> None:
    """평탄화 결과(로컬 임시 파일)를 원본 옆 캐시 경로로 복사해 저장한다.
    실패해도(권한 없음, NAS 연결 끊김 등) 이번 인쇄 자체는 이미 진행 중이라
    무시하고 넘어간다 — 대신 원인 파악이 가능하도록 print.log에 남긴다."""
    tmp_target = f"{cache_path}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copyfile(local_flattened_path, tmp_target)
        os.replace(tmp_target, cache_path)
        log_event(f"빠른인쇄 캐시 저장 성공: {cache_path}")
    except OSError as exc:
        log_event(f"빠른인쇄 캐시 저장 실패(이번 인쇄는 정상 진행됨): {cache_path} - {exc}")
        try:
            if os.path.exists(tmp_target):
                os.remove(tmp_target)
        except OSError:
            pass


@contextmanager
def prepare_print_path(original_path: str, on_stage: Callable[[str | None], None] | None = None):
    """캐시 재사용/평탄화까지 포함해 실제로 SumatraPDF에 넘길 로컬 경로를 만든다.

    1) 원본 옆에 유효한 "_빠른인쇄용" 캐시가 있으면(원본 수정시각+크기 일치) 그걸
       로컬로 복사해서 그대로 쓴다 — 평탄화를 다시 하지 않는다.
    2) 없거나 원본이 바뀌었으면 원본을 로컬로 복사해서 이미지가 많은지 검사하고,
       필요하면 평탄화한 뒤 그 결과를 캐시로 저장해둔다.
    3) 평탄화 대상이 아니면 그냥 원본(의 로컬 복사본)을 쓴다.

    on_stage: 평탄화처럼 시간이 걸리는 구간에 들어가고 나올 때 상태 문구를 알려주는
    콜백(예: "빠른인쇄용으로 변환 중..." → None). 화면에 아무 표시가 없으면 이 구간을
    "예전처럼 느리게 인쇄 중"으로 오해해 사용자가 도중에 취소해버리는 문제가 있었다."""

    def _stage(text: str | None) -> None:
        if on_stage:
            try:
                on_stage(text)
            except Exception:
                pass

    cache_path = _cache_path_for(original_path)

    try:
        src_stat = os.stat(original_path)
    except OSError:
        src_stat = None

    if src_stat is not None and os.path.exists(cache_path):
        marker = _read_cache_marker(cache_path)
        if marker == (src_stat.st_mtime_ns, src_stat.st_size):
            with local_copy_for_print(cache_path) as local_cache:
                yield local_cache
                return

    with local_copy_for_print(original_path) as local_original:
        if not should_flatten_for_print(local_original):
            yield local_original
            return

        extra_metadata = None
        if src_stat is not None:
            extra_metadata = {"keywords": _cache_marker(src_stat.st_mtime_ns, src_stat.st_size)}

        _stage("빠른인쇄용으로 변환 중...")
        try:
            flattened = flatten_pdf_for_print(local_original, extra_metadata=extra_metadata)
        finally:
            _stage(None)
        if flattened is None:
            yield local_original
            return
        try:
            if src_stat is not None:
                _persist_cache(flattened, cache_path)
            yield flattened
        finally:
            try:
                os.remove(flattened)
            except OSError:
                pass
