"""Build on 64-bit Windows with requirements-windows-build.lock installed."""
from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
stage = root / "build/windows"
runtime = stage / "tesseract"
if not (runtime / "tesseract.exe").is_file():
    raise RuntimeError("Run python scripts/build_windows.py to prepare the bundled OCR runtime.")

# Qt needs the Microsoft C++ runtime even on machines without developer tools.
# Include the redistributable DLLs rather than relying on the build agent's PATH.
system32 = Path(os.environ["SystemRoot"]) / "System32"
crt_names = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll",
             "vcruntime140.dll", "vcruntime140_1.dll")
crt = [(str(system32 / name), ".") for name in crt_names]
for source, _ in crt:
    if not Path(source).is_file():
        raise RuntimeError(f"Missing Microsoft C++ redistributable: {source}")

a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(root)],
    binaries=crt,
    datas=[
        (str(root / "assets"), "assets"),
        (str(root / "app/static"), "app/static"),
        (str(runtime), "tesseract"),
        (str(stage / "licenses"), "licenses"),
        (str(root / "packaging/THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "pytest", "IPython", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Protokol",
    debug=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Protokol")
