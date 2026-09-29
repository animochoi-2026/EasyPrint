# EasyPrint

Windows에서 문서와 사진을 목록 순서대로 인쇄하는 프로그램입니다.

**[최신 버전 다운로드](https://github.com/animochoi-2026/EasyPrint/releases/latest)** ·
[배포페이지](https://animochoi-2026.github.io/EasyPrint/) ·
[문제 신고](https://github.com/animochoi-2026/EasyPrint/issues)

## 설치와 업데이트

1. Releases의 `EasyPrint-버전-windows-x64.zip`을 내려받습니다.
2. ZIP 안의 **EasyPrint 폴더 전체**를 쓰기 가능한 위치에 압축 해제합니다.
3. `EasyPrint.exe`를 실행합니다. Python은 따로 설치하지 않아도 됩니다.
4. 이후 앱 상단 **업데이트** 버튼에서 **최신 버전 / 현재 버전 유지 / 이전 버전(롤백)** 중 선택할 수 있습니다.

이전 버전은 현재보다 바로 앞선 정식 릴리즈이며, 별도 확인 후 다운로드·검증·설치합니다.
최신과 현재가 같으면 재설치하지 않고, 이전 릴리즈가 없으면 롤백을 비활성화합니다.
1.0.1 이하로 롤백하면 버전 선택 화면도 없어지므로, 다시 올릴 때는 기존 업데이트 버튼을 사용하세요.

앱 시작 시 새 버전을 확인하며, 다운로드·설치는 반드시 사용자 확인 후 진행됩니다.
인쇄 중이거나 예약이 켜져 있으면 설치할 수 없습니다. 새 파일은 GitHub SHA-256과
파일별 매니페스트를 검증합니다. 설정·목록·인쇄 로그는 보존하고 프로그램 파일만 교체합니다.
인터넷 연결이 안 되어도 기존 인쇄는 사용할 수 있습니다. 업데이트 기능이 없는 예전
배포본은 이번 버전을 한 번 직접 내려받아야 합니다.

자동 업데이트는 공개 GitHub Releases만 조회합니다. GitHub 로그인이나 토큰은 필요 없으며,
문서·프린터 목록·개인 설정은 서버로 전송하지 않습니다. 코드 서명은 되어 있지 않습니다.

## 지원 형식

| 파일 | 인쇄 방식 | 별도 설치 |
|---|---|---|
| PDF | 포함된 SumatraPDF | 없음 |
| HWP/HWPX | 한글 인쇄 관리자, 전체 인쇄 | 한컴오피스 한/글 |
| DOCX | Word 직접 인쇄, 페이지 지정 지원 | 데스크톱 Microsoft Word |
| PNG/JPG/JPEG/BMP | A4 한 장으로 준비하여 인쇄 | 없음 |

- NAS 문서는 로컬 임시 복사본을 사용합니다. 원본은 수정하지 않습니다.
- PDF 전체 페이지 수 표시, 지정 페이지 인쇄, 완료 파일 재인쇄를 지원합니다.
- 복잡한 PDF에는 `_빠른인쇄용.pdf` 캐시를 생성·재사용할 수 있습니다.
- DOCX가 포함된 작업에서 Word를 실행할 수 없으면 안내 후 작업 전체를 정지합니다.
- 문서 인쇄는 시간 경과만으로 자동 중단하지 않습니다.
- 우클릭 메뉴는 앱에서 선택적으로 등록합니다.

Word 구역별 쪽 번호를 실제 페이지 순서에 대응시켜 인쇄합니다. 특수 문서는 Word에서
직접 출력한 결과와 먼저 비교하세요. 물리 프린터·드라이버마다 결과가 다를 수 있습니다.

## 업데이트 복구

이전 프로그램 파일은 설치 폴더의 `.easyprint_update_.../backup`에 남습니다.
버전 목록 확인 및 설치 성공 후 **최신·현재·이전 버전**에 해당하지 않는 검증된 로컬 백업은
자동 삭제합니다. 삭제된 버전은 GitHub에서 다시 받을 수 있습니다. 성공한 다운로드 임시 파일도
정리합니다. 복구 중/손상된 폴더, 알 수 없는 파일이 섞인 폴더, 문서·설정·목록·로그와 GitHub 릴리즈는
삭제하지 않습니다. 인터넷 오류로 전체 버전 목록을 확인하지 못하면 백업 정리도 하지 않습니다.
파일 교체 실패 시 자동 복구를 시도하며, 파일이 잠겨 있다면 앱을 모두 닫고 다시 시도하세요.
관리자 권한이 필요한 폴더에서는 자동 설치가 실패할 수 있으므로 사용자 소유 폴더를 권장합니다.
`update.log` 또는 임시 업데이트 폴더의 `update-error.log`로 오류를 확인할 수 있습니다.
목록은 업데이트 후 복원되지만 **자동으로 인쇄를 다시 시작하지 않습니다**.

## 개발·빌드 (Windows x64 / Python 3.13)

```powershell
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r build_requirements.txt
.\venv\Scripts\python.exe scripts\fetch_sumatra.py
.\venv\Scripts\python.exe -B -m unittest discover -s scripts -p "test_*.py"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

`src/version.py`가 앱 표시, 태그, 파일 이름, 업데이트 매니페스트의 기준입니다.
빌드는 독립 업데이트 도우미와 앱을 만들고 ZIP 무결성을 검사합니다.
SumatraPDF는 공식 사이트의 고정 버전과 SHA-256을 확인한 후 받습니다.

## 다음 버전 배포

`src/version.py`와 `RELEASE_NOTES.md`를 수정하고 테스트 후 커밋합니다.
Actions의 **Build release**를 수동 실행하면 테스트·빌드 후 **초안 Release**가 생성됩니다.
초안의 ZIP·검증값·변경사항을 확인하고 Publish하면 기존 앱에 새 버전이 안내됩니다.
같은 버전 파일을 덮어쓰지 말고 항상 새 버전으로 배포하세요.

## 라이선스

EasyPrint: **AGPL-3.0**. 포함된 라이브러리는 각 원래 라이선스를 따릅니다.
[전체 라이선스](LICENSE.txt)와 [의존성 소스·고지](THIRD_PARTY_NOTICES.txt)를 확인하세요.
Microsoft Word와 한컴오피스는 포함하지 않으며 사용자가 적법하게 설치해야 합니다.
