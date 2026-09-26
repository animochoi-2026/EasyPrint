"""Entry point for the small, independent updater executable."""
import ctypes
import sys
import traceback
from pathlib import Path
from src.update_installer import apply_job

if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("업데이트 작업 정보가 없습니다.")
        apply_job(sys.argv[1])
    except Exception:
        detail = traceback.format_exc()
        if len(sys.argv) == 2:
            Path(sys.argv[1]).with_name("update-error.log").write_text(detail, encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None,
            "업데이트를 완료하지 못했습니다.\n기존 프로그램과 설정은 보존됩니다.\n"
            "EasyPrint를 다시 실행하거나 GitHub 배포페이지에서 새 버전을 내려받아주세요.",
            "EasyPrint 업데이트 실패", 0x10)
        sys.exit(1)
