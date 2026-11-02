# Components included in the Windows package

The installer contains a private Python runtime and libraries. It does not
install Python, Tesseract, Qt or other dependencies system-wide. Runtime files
are kept beside Protokol.exe; user libraries and numbering are stored separately
under `%LOCALAPPDATA%\Protokol\data`.

* Python: https://www.python.org/ (PSF License).
* Qt / PySide6: https://www.qt.io/qt-for-python (LGPLv3/GPLv3 and component
  licenses). The unmodified Qt shared libraries are distributed as separate
  files in `_internal`, so they can be replaced with compatible versions. The
  corresponding Qt source is available at https://download.qt.io/official_releases/
  and https://code.qt.io/. See the bundled license texts for their conditions.
* Tesseract 5.4.0.20240606: Windows binary distribution from
  https://github.com/UB-Mannheim/tesseract/releases/tag/v5.4.0.20240606 .
  Tesseract source: https://github.com/tesseract-ocr/tesseract/tree/5.4.0 .
  The Windows distribution also includes Leptonica and supporting libraries;
  notices supplied with that distribution are preserved in `_internal/tesseract`.
  Windows build sources: https://github.com/UB-Mannheim/tesseract .
* OCR models: https://github.com/tesseract-ocr/tessdata_fast at commit
  `87416418657359cb625c412a48b6e1d6d41c29bd` (Apache-2.0). The model license is
  included in `assets/tessdata/LICENSE`.
* PyMuPDF: https://pymupdf.readthedocs.io/ (AGPL-3.0 or commercial license).
  Source: https://github.com/pymupdf/PyMuPDF .
* Other Python packages and PyInstaller: license texts and package metadata are
  included in `_internal/licenses`. PyInstaller's bootloader exception permits
  distributing applications built with it.
* Word document previews run locally using docx-preview 0.3.7 (Apache-2.0)
  and JSZip 3.10.1 (MIT option). Sources: https://github.com/VolodymyrBaydalka/docxjs
  and https://github.com/Stuk/jszip . Their license texts and the bundled file
  checksums are in `app/static/vendor` in the source archive and application.

`_internal/licenses/protokol-source.zip` contains the application sources for
this build. Package metadata identifies versions and upstream sources for
included dependencies. These notices do not replace the individual licenses
or grant rights beyond them.
