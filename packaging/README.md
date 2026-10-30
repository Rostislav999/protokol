# Building the standalone Windows application

The end-user installer includes CPython (via PyInstaller), Python dependencies,
QtWebEngine/PySide6, a private Tesseract executable and its runtime DLLs, the
three verified OCR models, templates, and application assets. It does not run
pip, install Tesseract globally, or download components on the user's machine.
The application works in its own window. Microsoft Word is optional and is
needed only for the existing direct-to-printer feature.

## GitHub Actions

`.github/workflows/windows-installer.yml` runs on relevant pushes to `main` or
manually with **Run workflow**. It builds on Windows Server 2022 with x64 Python
3.12.10, 7-Zip and Inno Setup 6 supplied by the hosted runner. Application and
build packages are pinned with SHA-256 hashes in `requirements-windows-build.lock`.
That lock preserves the application dependency versions in `requirements.lock`.

Only a successful workflow uploads **Protokol-Windows-Installer**. It contains
`Protokol-Setup-1.0.0-win64.exe`, `SHA256SUMS.txt`, and
`windows-validation.json`. The artifact remains available for 30 days.
This workflow does not publish a GitHub release or sign the installer.

The build verifies the Tesseract download against the SHA-256 published by
Microsoft winget, extracts it without executing its installer, and bundles all
its sibling DLLs. `scripts/build_windows.py` documents the exact upstream URL
and checksum. It preserves the upstream notices and packages Python/Qt/package
licenses and the application's exact source archive.

## Local build (Windows x64)

The build machine needs Python 3.12, Git, 7-Zip and Inno Setup 6. These tools are
only build prerequisites. Use a clean Windows VM/profile, since validation
performs a real installation and uninstallation with shortcuts and registry
entries. The script refuses to run if Protokol is already installed. From the
existing checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-windows-build.lock
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe scripts/build_windows.py --version 1.0.0
```

Generated files go under ignored `build/` and `dist/`. Do not add `.venv`, user
data, build outputs, or installer EXEs to the source repository. Do not create
a Git worktree unless explicitly requested. Build from a committed checkout:
the installer contains `git archive HEAD` as its source archive.

## Required validation

The script builds a PyInstaller directory bundle, then exercises both the
portable executable and a silently installed copy:

* `--check`: real multilingual OCR, template availability, local HTTP routes,
  and XLSX export with a supplier code whose leading zeroes survive.
* `--smoke-test`: renders the real interface and CSS in QtWebEngine, generates
  a Blob download, accepts it through the native download handler, and checks
  its bytes. The window then closes automatically.
* The installed path contains spaces and Cyrillic characters. Checks run with
  Python/Tesseract/build tools removed from PATH and unusable outbound proxy
  settings. Only Windows system components remain on PATH.
* Silent uninstall must leave persistent user data intact.

An installer is uploaded only after these checks succeed. Logs from failed
Windows builds are uploaded separately. Linux tests validate runtime preparation,
checksum failures, the local server, and Excel; they do not establish that a
Windows installer was built successfully.

For a GUI smoke test in a headless developer environment,
`QT_QPA_PLATFORM=offscreen` can be used. Do not disable the Chromium sandbox.
`--check` needs no graphical display.

The Qt shared libraries remain separate files to allow replacement with
compatible builds. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the
licenses bundled with the installer for component licenses and source locations.
