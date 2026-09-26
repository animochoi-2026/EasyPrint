"""Images use PDF printing; DOCX uses installed Word's direct print API."""
from __future__ import annotations

import io
import multiprocessing
import os
import shutil
import tempfile
import time

from .models import IMAGE_EXTENSIONS
from .pdf_utils import get_pdf_page_count
from .printing import print_pdf
from .print_log import log_event
from .printer_status import has_printer_fault
from .validation import parse_page_range


def image_to_pdf(source: str, destination: str) -> None:
    """One image per A4 page, with EXIF orientation and a white alpha background."""
    import pymupdf
    from PIL import Image, ImageOps

    with Image.open(source) as original:
        oriented = ImageOps.exif_transpose(original)
        rgba = oriented.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        white.alpha_composite(rgba)
        data = io.BytesIO()
        white.convert("RGB").save(data, format="PNG")
        width, height = (842, 595) if rgba.width > rgba.height else (595, 842)
    with pymupdf.open() as output:
        page = output.new_page(width=width, height=height)
        page.insert_image(pymupdf.Rect(24, 24, width - 24, height - 24), stream=data.getvalue(), keep_proportion=True)
        output.save(destination, deflate=True)


class WordUnavailableError(RuntimeError):
    """The entire batch must stop, rather than skip Word documents."""


class WordPrinterFaultError(RuntimeError):
    pass


WORD_UNAVAILABLE_MESSAGE = (
    "Microsoft Word가 설치되어 있지 않거나 실행할 수 없습니다.\n\n"
    "DOCX 직접 인쇄에는 데스크톱 Microsoft Word가 필요합니다.\n"
    "Word 설치 후 한 번 직접 실행하여 초기 설정/인증을 완료해주세요.\n"
    "이미 설치되어 있다면 Office 복구 및 실행 권한을 확인해주세요.\n\n"
    "인쇄를 중지했습니다. 남은 파일은 목록에 유지됩니다."
)


def _word_page_spec(document, page_range, page_count, cancel_event=None):
    """Translate physical (PDF-style) page indices to Word p#s# addresses.

    Never fall back to all pages when a range cannot be resolved. Word's plain
    page numbers can match multiple sections when numbering restarts.
    """
    ok, reason = parse_page_range(page_range, page_count)
    if not ok:
        raise ValueError(reason)
    if not page_range.strip():
        return ""
    runs = []
    for part in page_range.strip().split(","):
        bounds = [int(value) for value in part.split("-")]
        for physical in range(bounds[0], bounds[-1] + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("사용자 요청으로 취소됨")
            cursor = document.GoTo(What=1, Which=1, Count=physical)
            actual = int(cursor.Information(3))  # wdActiveEndPageNumber
            adjusted = int(cursor.Information(1))  # wdActiveEndAdjustedPageNumber
            section = int(cursor.Information(2))  # wdActiveEndSectionNumber
            if actual != physical or adjusted < 1 or section < 1:
                raise ValueError(f"{physical}쪽의 Word 인쇄 범위를 확인할 수 없습니다. Word에서 직접 인쇄해주세요.")
            if runs and runs[-1][1] + 1 == physical and runs[-1][3] + 1 == adjusted and runs[-1][4] == section:
                runs[-1][1] = physical
                runs[-1][3] = adjusted
            else:
                runs.append([physical, physical, adjusted, adjusted, section])
    return ",".join(
        f"p{start}s{section}" if start == end else f"p{start}s{section}-p{end}s{section}"
        for _, _, start, end, section in runs
    )


def _word_print(source, printer, copies, page_range, pipe, cancel_event=None):
    """Own Word instance only; called in a spawned process so cancellation works."""
    import pythoncom
    import win32com.client
    import win32process

    pythoncom.CoInitialize()
    word = document = None
    old_options = {}
    result = ("ok", None)
    try:
        try:
            word = win32com.client.DispatchEx("Word.Application")
        except Exception as exc:
            raise WordUnavailableError(WORD_UNAVAILABLE_MESSAGE) from exc
        pipe.send(("pid", win32process.GetWindowThreadProcessId(word.Hwnd)[1]))
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        # A source-less job checks real COM activation before any file in a
        # mixed PDF/DOCX batch is submitted. It never opens/prints a document.
        if source is not None:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("사용자 요청으로 취소됨")
            if not isinstance(copies, int) or not 1 <= copies <= 32767:
                raise ValueError("인쇄 매수는 1~32767 사이의 정수여야 합니다.")
            for name in ("UpdateLinksAtOpen", "UpdateFieldsAtPrint", "UpdateLinksAtPrint", "PrintReverse"):
                old_options[name] = getattr(word.Options, name)
                setattr(word.Options, name, False)
            if printer:
                # Unlike ActivePrinter=..., this doesn't change Windows default.
                word.WordBasic.FilePrintSetup(Printer=printer, DoNotSetAsSysDefault=1)
            pipe.send(("stage", "Word 문서 여는 중…"))
            document = word.Documents.Open(
                FileName=os.path.abspath(source), ReadOnly=True, AddToRecentFiles=False,
                ConfirmConversions=False, Visible=False, NoEncodingDialog=True,
            )
            pipe.send(("stage", "Word 페이지 확인 중…"))
            document.Repaginate()
            count = int(document.ComputeStatistics(2))  # wdStatisticPages
            if count < 1:
                raise ValueError("Word 문서의 전체 페이지 수를 확인할 수 없습니다.")
            pages = _word_page_spec(document, page_range, count, cancel_event)
            pipe.send(("pages", (count, pages)))
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("사용자 요청으로 취소됨")
            pipe.send(("stage", "프린터로 직접 전송 중…"))
            document.PrintOut(
                Background=False, Range=4 if pages else 0, Pages=pages,
                Copies=copies, Collate=True, Item=0, PageType=0,
                PrintToFile=False, ManualDuplexPrint=False,
                PrintZoomColumn=1, PrintZoomRow=1,
            )
    except WordUnavailableError as exc:
        result = ("unavailable", str(exc))
    except Exception as exc:
        result = ("error", str(exc))
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass
        if word is not None:
            try:
                for name, value in old_options.items():
                    try:
                        setattr(word.Options, name, value)
                    except Exception:
                        pass
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        pythoncom.CoUninitialize()
        pipe.send(result)
        pipe.close()


def _run_word_job(source=None, printer=None, copies=1, page_range="", abort_event=None, on_stage=None):
    import win32api
    import win32con
    import win32event

    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    cancel_event = ctx.Event()
    child = ctx.Process(target=_word_print, args=(source, printer, copies, page_range, sender, cancel_event), daemon=True)
    word_handle = None
    success, error, unavailable = False, None, False
    sending_since = None
    try:
        if abort_event is not None and abort_event.is_set():
            raise RuntimeError("사용자 요청으로 취소됨")
        child.start()
        sender.close()
        while child.is_alive() or receiver.poll():
            if abort_event is not None and abort_event.is_set():
                cancel_event.set()
                raise RuntimeError("사용자 요청으로 취소됨")
            if sending_since is not None and time.monotonic() - sending_since > 15 and has_printer_fault(printer):
                raise WordPrinterFaultError("프린터 오류 감지 (용지 걸림/오프라인 등)")
            if receiver.poll(0.1):
                try:
                    kind, value = receiver.recv()
                except EOFError:
                    break
                if kind == "pid":
                    # Keep an actual process handle so a recycled PID is never killed.
                    word_handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE, False, value)
                elif kind == "ok":
                    success = True
                elif kind == "error":
                    error = value
                elif kind == "unavailable":
                    unavailable, error = True, value
                elif kind == "stage":
                    if value == "프린터로 직접 전송 중…":
                        sending_since = time.monotonic()
                    if on_stage:
                        on_stage(value)
                elif kind == "pages":
                    count, pages = value
                    log_event(f"Word 직접 인쇄 전달: {source} | 프린터={printer or '기본'} | 전체={count}쪽 | 범위={page_range or '전체'} | Word범위={pages or '전체'} | 매수={copies}")
        if unavailable:
            raise WordUnavailableError(error)
        if error or not success:
            raise RuntimeError(error or "Word 인쇄 작업을 완료하지 못했습니다.")
    finally:
        if child.is_alive() or not success:
            cancel_event.set()
            if word_handle is not None and win32event.WaitForSingleObject(word_handle, 0) == win32event.WAIT_TIMEOUT:
                win32api.TerminateProcess(word_handle, 1)
            if child.is_alive():
                child.terminate()
        if child.pid is not None:
            child.join(timeout=3)
        if word_handle is not None:
            word_handle.Close()
        receiver.close()
        sender.close()


def check_word_available(abort_event=None):
    """Check Word in a cancellable child process without printing anything."""
    try:
        _run_word_job(abort_event=abort_event)
    except WordUnavailableError:
        raise
    except Exception as exc:
        if abort_event is not None and abort_event.is_set():
            raise
        raise WordUnavailableError(WORD_UNAVAILABLE_MESSAGE) from exc


def print_document(path, printer, copies=1, page_range="", abort_event=None, on_stage=None):
    try:
        with tempfile.TemporaryDirectory(prefix="easyprint_document_") as work:
            if abort_event is not None and abort_event.is_set():
                return False, "사용자 요청으로 취소됨", False
            ext = os.path.splitext(path)[1].lower()
            local = os.path.join(work, "source" + ext)
            if on_stage:
                on_stage("로컬로 복사 중…")
            shutil.copyfile(path, local)
            pdf = os.path.join(work, "print.pdf")
            if ext in IMAGE_EXTENSIONS:
                if on_stage:
                    on_stage("사진 인쇄 준비 중…")
                image_to_pdf(local, pdf)
            elif ext == ".docx":
                if on_stage:
                    on_stage("Word 직접 인쇄 준비 중…")
                _run_word_job(local, printer, copies, page_range, abort_event, on_stage)
                return True, None, False
            else:
                return False, "지원하지 않는 문서 형식입니다.", False
            count = get_pdf_page_count(pdf)
            if count is None:
                return False, "변환된 PDF를 읽을 수 없습니다.", False
            ok, reason = parse_page_range(page_range, count)
            if not ok:
                return False, reason, False
            if abort_event is not None and abort_event.is_set():
                return False, "사용자 요청으로 취소됨", False
            if on_stage:
                on_stage("프린터로 전송 중…")
            return print_pdf(pdf, printer, copies, page_range, abort_event, on_stage)
    except WordUnavailableError:
        raise  # App must pause the batch and show a main-thread popup.
    except WordPrinterFaultError as exc:
        return False, str(exc), True
    except Exception as exc:
        return False, f"인쇄 준비 실패: {exc}", False
    finally:
        if on_stage:
            on_stage(None)
