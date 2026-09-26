## EasyPrint 1.0.0

GitHub 업데이트를 지원하는 첫 공개 배포입니다.

- 앱 상단 버전 표시·업데이트 확인, 사용자 승인 후 다운로드·설치·재시작
- GitHub SHA-256 및 파일별 무결성 확인, 실패 시 프로그램 파일 복구 시도
- 업데이트 시 인쇄 목록·설정 유지, 인쇄 및 예약 중 설치 차단
- PDF·HWP/HWPX·DOCX·PNG/JPG/JPEG/BMP 배치 인쇄
- DOCX는 Word 직접 부분 인쇄, Word 실행 불가 시 팝업 후 전체 작업 정지
- PDF 페이지 수 자동 표시, 완료 파일부터 재인쇄, NAS 임시 복사 지원

### 처음 받는 분
`EasyPrint-1.0.0-windows-x64.zip`을 풀고 **EasyPrint 폴더 전체**를 사용하세요.
이전 버전에 업데이트 버튼이 없다면 이번 버전은 한 번 직접 내려받아야 합니다.
Windows x64용입니다. DOCX에는 데스크톱 Microsoft Word, 한글 문서에는 한컴오피스가 필요합니다.

### 확인 범위
자동 회귀 테스트와 업데이트 파일 검증·교체·복구 경로를 검사했습니다.
실제 Word/물리 프린터 조합은 사용자 PC에서 소량으로 먼저 확인해주세요.

### 소스와 라이선스
EasyPrint 소스는 아래 Source code와 이 저장소에서 제공합니다(AGPL-3.0).
동봉한 PyMuPDF/MuPDF와 별도 SumatraPDF 실행 파일의 대응 소스·빌드 정보는
[THIRD_PARTY_NOTICES.txt](https://github.com/animochoi-2026/EasyPrint/blob/main/THIRD_PARTY_NOTICES.txt)에 안내되어 있습니다.
