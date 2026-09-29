"""Public GitHub Releases updates. No credentials, telemetry or document uploads."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .version import APP_VERSION, UPDATE_REPOSITORY

MAX_ZIP = 300 * 1024 * 1024
MAX_EXPANDED = 1200 * 1024 * 1024
MANIFEST = "update-manifest.json"
ROOT_FILES = {"EasyPrint.exe", "README.txt", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt", MANIFEST}


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("지원하지 않는 버전 형식입니다.")
    numbers = tuple(int(n) for n in version.removeprefix("v").split("."))
    return numbers + (0,) * (4 - len(numbers))


def asset_url_allowed(url):
    if not isinstance(url, str):
        return False
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.netloc == "github.com"
            and parsed.path.startswith(f"/{UPDATE_REPOSITORY}/releases/download/")
            and not parsed.query and not parsed.fragment)


class _GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            "github.com", "api.github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com", "github-releases.githubusercontent.com",
        } or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("GitHub 외부로 연결되는 다운로드를 차단했습니다.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": f"EasyPrint/{APP_VERSION}",
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/octet-stream",
    })
    return urllib.request.build_opener(_GitHubRedirect()).open(request, timeout=20)


@dataclass(frozen=True)
class Release:
    version: str
    page_url: str
    asset_url: str
    sha256: str
    size: int
    notes: str


def parse_release(data, current=APP_VERSION):
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name", "")
    if current is not None and version_tuple(tag) <= version_tuple(current):
        return None
    version_tuple(tag)
    version = tag.removeprefix("v")
    name = f"EasyPrint-{version}-windows-x64.zip"
    assets = [a for a in data.get("assets", []) if a.get("name") == name and a.get("state") == "uploaded"]
    if len(assets) != 1:
        raise ValueError("새 버전의 Windows 배포 파일이 아직 준비되지 않았습니다.")
    asset = assets[0]
    url = asset.get("browser_download_url", "")
    expected = f"https://github.com/{UPDATE_REPOSITORY}/releases/download/{tag}/{name}"
    if not asset_url_allowed(url) or url != expected:
        raise ValueError("배포 파일 주소가 올바르지 않습니다.")
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("GitHub 파일 검증값이 없어 업데이트를 중지했습니다.")
    size = asset.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_ZIP:
        raise ValueError("업데이트 파일 크기가 올바르지 않습니다.")
    return Release(version, f"https://github.com/{UPDATE_REPOSITORY}/releases/tag/{tag}",
                   url, digest[7:].lower(), size, str(data.get("body") or "")[:6000])


def check_for_update(current=APP_VERSION):
    try:
        with _open(f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest") as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("업데이트 정보가 너무 큽니다.")
        return parse_release(json.loads(content), current)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("아직 공개된 배포 버전이 없거나 배포페이지에 접근할 수 없습니다.") from exc
        if exc.code in (403, 429):
            raise ValueError("GitHub 접속 한도에 도달했습니다. 잠시 후 다시 확인해주세요.") from exc
        raise


@dataclass(frozen=True)
class VersionChoices:
    latest: Release | None
    current: str
    previous: Release | None

    @property
    def retained_versions(self):
        return {self.current} | {r.version for r in (self.latest, self.previous) if r}


def version_choices(releases, current=APP_VERSION):
    ordered = sorted(releases, key=lambda r: version_tuple(r.version), reverse=True)
    previous = next((r for r in ordered if version_tuple(r.version) < version_tuple(current)), None)
    return VersionChoices(ordered[0] if ordered else None, current, previous)


def list_releases():
    """Read the entire stable catalog before choosing a predecessor or pruning."""
    releases = {}
    for page in range(1, 21):
        with _open(f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases?per_page=100&page={page}") as response:
            content = response.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError("버전 목록이 너무 큽니다. 자동 정리를 하지 않습니다.")
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("버전 목록 형식이 올바르지 않습니다.")
        for item in data:
            if item.get("draft") or item.get("prerelease"):
                continue
            try:
                version_tuple(item.get("tag_name"))
            except ValueError:
                continue
            # Invalid stable assets are an error, not permission to prune backups.
            release = parse_release(item, None)
            releases[version_tuple(release.version)] = release
        if len(data) < 100:
            return sorted(releases.values(), key=lambda r: version_tuple(r.version), reverse=True)
    raise ValueError("전체 버전 목록을 확인하지 못해 자동 정리를 하지 않습니다.")


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(name):
    """Accept only application-owned files, including Windows path rules."""
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or path.is_absolute() or str(path) != name:
        raise ValueError("안전하지 않은 업데이트 경로입니다.")
    for part in path.parts:
        if part in (".", "..") or part.endswith((".", " ")) or any(ord(c) < 32 for c in part):
            raise ValueError("안전하지 않은 업데이트 경로입니다.")
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError("Windows 예약 파일명을 차단했습니다.")
    if name not in ROOT_FILES and not (len(path.parts) > 1 and path.parts[0] == "_internal"):
        raise ValueError("설정이나 문서를 덮어쓰는 업데이트를 차단했습니다.")
    return path


def verify_payload(folder, expected_version):
    folder = Path(folder)
    if folder.is_symlink() or folder.is_junction():
        raise ValueError("업데이트 폴더 링크는 허용하지 않습니다.")
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("product") != "EasyPrint" or manifest.get("version") != expected_version or manifest.get("schema") != 1:
        raise ValueError("업데이트 제품/버전 정보가 일치하지 않습니다.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not {"EasyPrint.exe", "_internal/EasyPrintUpdater.exe"}.issubset(files):
        raise ValueError("업데이트 필수 파일이 없습니다.")
    actual = set()
    for path in folder.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("업데이트에 파일 링크가 포함되어 있습니다.")
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            safe_member(name)
            if name != MANIFEST:
                actual.add(name)
    if actual != set(files):
        raise ValueError("업데이트 파일 목록이 일치하지 않습니다.")
    for name, digest in files.items():
        safe_member(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or file_hash(folder / name) != digest:
            raise ValueError(f"업데이트 파일 검증 실패: {name}")
    return manifest


def extract_payload(archive_path, output, version):
    output = Path(output)
    output.mkdir()  # Never extract over an existing tree.
    with zipfile.ZipFile(archive_path) as archive:
        members = []
        seen = set()
        total = 0
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            if not entry.filename.startswith("EasyPrint/"):
                raise ValueError("EasyPrint 폴더 밖의 압축 항목입니다.")
            name = entry.filename[len("EasyPrint/"):]
            relative = safe_member(name)
            if name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("중복 경로나 심볼릭 링크를 차단했습니다.")
            seen.add(name.casefold())
            total += entry.file_size
            if total > MAX_EXPANDED or len(seen) > 10000:
                raise ValueError("업데이트 압축 해제 크기 제한을 초과했습니다.")
            members.append((entry, relative))
        for entry, relative in members:
            target = output.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
    verify_payload(output, version)


def download_update(release, on_progress=None, cancel=None):
    if not asset_url_allowed(release.asset_url):
        raise ValueError("업데이트 주소가 올바르지 않습니다.")
    work = Path(tempfile.mkdtemp(prefix="easyprint_update_"))
    try:
        archive = work / "release.zip"
        digest = hashlib.sha256()
        received = 0
        with _open(release.asset_url) as response, archive.open("xb") as destination:
            while chunk := response.read(256 * 1024):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("업데이트가 취소되었습니다.")
                received += len(chunk)
                if received > release.size or received > MAX_ZIP:
                    raise ValueError("다운로드 크기가 배포 정보와 다릅니다.")
                destination.write(chunk)
                digest.update(chunk)
                if on_progress:
                    on_progress(received, release.size)
        if received != release.size or digest.hexdigest() != release.sha256:
            raise ValueError("다운로드 파일의 SHA-256 검증에 실패했습니다. 설치하지 않습니다.")
        if cancel is not None and cancel.is_set():
            raise RuntimeError("업데이트가 취소되었습니다.")
        extract_payload(archive, work / "payload", release.version)
        return work
    except Exception:
        shutil.rmtree(work, ignore_errors=True)  # Only this call's private temp dir.
        raise


def discard_download(work):
    """Only called with a private work directory returned by download_update."""
    work = Path(work)
    temp = Path(tempfile.gettempdir()).resolve()
    if (work.is_symlink() or work.is_junction() or work.resolve().parent != temp
            or not work.name.startswith('easyprint_update_')):
        return
    shutil.rmtree(work, ignore_errors=True)
