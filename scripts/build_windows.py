"""Build and exercise the self-contained Windows installer on Windows x64.

Run with the hashed requirements-windows-build.lock installed. No system-wide
Python/Tesseract installation is performed by this script or the installer.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build/windows"
TESSERACT_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
# Published independently by Microsoft winget-pkgs:
# manifests/u/UB-Mannheim/TesseractOCR/5.4.0.20240606/
# UB-Mannheim.TesseractOCR.installer.yaml
TESSERACT_SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
MAX_DOWNLOAD = 100 * 1024 * 1024


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def download_verified(url: str, path: Path, expected: str) -> None:
    if path.is_file() and digest(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            count = 0
            with urllib.request.urlopen(url, timeout=90) as response:
                while data := response.read(1024 * 1024):
                    count += len(data)
                    if count > MAX_DOWNLOAD:
                        raise RuntimeError("Tesseract download exceeds the expected size limit.")
                    stream.write(data)
        if digest(temporary) != expected:
            raise RuntimeError("Tesseract checksum mismatch; downloaded runtime will not be used.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def find_tool(name: str, candidates: list[Path]) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(f"Build tool {name} is missing. Use the Windows installer GitHub Actions workflow.")


def prepare_tesseract() -> Path:
    seven_zip = find_tool("7z", [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "7-Zip/7z.exe"])
    archive = STAGE / "downloads/tesseract-installer.exe"
    download_verified(TESSERACT_URL, archive, TESSERACT_SHA256)
    destination = STAGE / "tesseract"
    with tempfile.TemporaryDirectory(prefix="extract-", dir=STAGE) as temporary:
        extracted = Path(temporary)
        subprocess.run([seven_zip, "x", "-y", f"-o{extracted}", str(archive)], check=True)
        if not (extracted / "tesseract.exe").is_file() or not (extracted / "libtesseract-5.dll").is_file():
            raise RuntimeError("Unexpected Tesseract distribution layout.")
        if not (extracted / "doc/LICENSE").is_file():
            raise RuntimeError("Tesseract license is missing from its distribution.")
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        for source in [extracted / "tesseract.exe", *extracted.glob("*.dll")]:
            shutil.copy2(source, destination / source.name)
        shutil.copytree(extracted / "doc", destination / "doc")
    (destination / "distribution.json").write_text(json.dumps({
        "url": TESSERACT_URL, "sha256": TESSERACT_SHA256,
        "files": {p.name: digest(p) for p in destination.glob("*") if p.is_file()},
    }, indent=2), encoding="utf-8")
    return destination


def collect_licenses() -> None:
    destination = STAGE / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        directory = destination / safe_name
        directory.mkdir(exist_ok=True)
        # Preserve the installed text instead of serializing email.Message:
        # legacy metadata (e.g. altgraph) can have multiline Description headers
        # that email's serializer rejects as an embedded-header injection.
        package_metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO")
        if package_metadata is None:
            package_metadata = json.dumps(list(distribution.metadata.items()), ensure_ascii=False, indent=2)
        directory.joinpath("METADATA.txt").write_text(package_metadata, encoding="utf-8")
        for entry in distribution.files or []:
            if not any(part.lower().startswith(("license", "licence", "copying", "notice", "copyright")) for part in entry.parts):
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                relative = Path(*[part for part in entry.parts if part not in ("..", ".")])
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        inventory.append({"name": name, "version": distribution.version})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The Python runtime license could not be found.")
    shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")
    destination.joinpath("packages.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    # Ship the exact application source along with notices and upstream links.
    subprocess.run(["git", "archive", "--format=zip", f"--output={destination / 'protokol-source.zip'}", "HEAD"],
                   cwd=ROOT, check=True)


def check_bundle(executable: Path, directory: Path, *, window: bool) -> None:
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "TESSDATA_PREFIX"):
        environment.pop(name, None)
    # The bundled executable must work without Python, Tesseract, Qt or build
    # tools on PATH. Windows system components remain available.
    windows = Path(os.environ["SystemRoot"])
    environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    environment["PROTOKOL_DATA_DIR"] = str(directory / "data")
    environment["HTTP_PROXY"] = environment["HTTPS_PROXY"] = "http://127.0.0.1:9"
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    subprocess.run([str(executable), "--smoke-test" if window else "--check"],
                   cwd=executable.parent, env=environment, check=True, timeout=240)


def require_clean_install_profile() -> None:
    """Do not overwrite another installation's uninstaller registration."""
    import winreg

    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A71497EF-82C6-4400-AB15-5CBFE75D8D86}_is1"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view):
                    pass
            except FileNotFoundError:
                continue
            raise RuntimeError("Protokol is already installed. Test installers on a clean Windows VM or GitHub runner.")


def build(version: str) -> Path:
    require_clean_install_profile()
    prepare_tesseract()
    collect_licenses()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(ROOT / "packaging/protokol.spec")], cwd=ROOT, check=True)
    binary = ROOT / "dist/Protokol/Protokol.exe"
    check_bundle(binary, STAGE / "portable-check", window=False)
    check_bundle(binary, STAGE / "portable-check", window=True)
    compiler = find_tool("ISCC", [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 6/ISCC.exe",
    ])
    output = ROOT / "dist/installer"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([compiler, f"/DAppVersion={version}", f"/DSourceDir={binary.parent}",
                    f"/DOutputDir={output}", str(ROOT / "packaging/protokol.iss")], cwd=ROOT, check=True)
    installer = output / f"Protokol-Setup-{version}-win64.exe"
    with tempfile.TemporaryDirectory(prefix="protokol-install-") as temporary:
        installed = Path(temporary) / "Протокол з пробілами"
        subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                        f"/DIR={installed}", f"/LOG={STAGE / 'install.log'}"], check=True, timeout=240)
        preserved = STAGE / "installed-check/data/preserve-user-data.txt"
        preserved.parent.mkdir(parents=True, exist_ok=True)
        preserved.write_text("Keep library and numbering on uninstall.", encoding="utf-8")
        default_data = Path(os.environ["LOCALAPPDATA"]) / "Protokol/data"
        default_data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=default_data, prefix="installer-validation-", delete=False) as marker:
            default_marker = Path(marker.name)
        try:
            try:
                check_bundle(installed / "Protokol.exe", STAGE / "installed-check", window=False)
                check_bundle(installed / "Protokol.exe", STAGE / "installed-check", window=True)
            finally:
                subprocess.run([str(installed / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                               check=True, timeout=120)
                deadline = time.monotonic() + 60
                while installed.exists() and time.monotonic() < deadline:
                    time.sleep(0.25)
                if installed.exists():
                    raise RuntimeError("The test installation was not fully removed.")
            if not preserved.is_file() or not default_marker.is_file():
                raise RuntimeError("Uninstall removed persistent user data.")
        finally:
            default_marker.unlink(missing_ok=True)
    checksum = digest(installer)
    (output / "SHA256SUMS.txt").write_text(f"{checksum}  {installer.name}\n", encoding="ascii")
    (output / "windows-validation.json").write_text(json.dumps({
        "version": version, "installer": installer.name, "sha256": checksum,
        "checks": ["bundled OCR and Excel", "native window and file download", "silent installation",
                   "installed OCR and Excel", "installed native window and download", "uninstall preserves user data"],
    }, indent=2), encoding="utf-8")
    return installer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Verify and stage the private Tesseract distribution.")
    parser.add_argument("--version", default="1.7.0")
    args = parser.parse_args(argv)
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("Build on 64-bit Windows; use the Windows installer GitHub Actions workflow.")
    if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", args.version):
        parser.error("Version must contain three numeric components, e.g. 1.0.0.")
    STAGE.mkdir(parents=True, exist_ok=True)
    if args.prepare_only:
        print(prepare_tesseract())
    else:
        print(build(args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
