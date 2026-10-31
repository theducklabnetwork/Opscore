# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the Windows Opscore launcher/backend."""

from importlib import metadata
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parents[1]
block_cipher = None

datas = [
    (str(path), "ducklab")
    for path in ROOT.joinpath("ducklab").glob("*.py")
]
datas += [(str(path), "assets") for path in ROOT.joinpath("assets").iterdir() if path.is_file()]
datas += [
    (str(ROOT / "rxconfig.py"), "."),
    (str(ROOT / "RELEASE"), "."),
    (str(ROOT / "packaging" / "opscore_backend.py"), "packaging"),
]
datas += [
    (str(path), str(Path(".web") / "build" / path.relative_to(ROOT / ".web" / "build").parent))
    for path in ROOT.joinpath(".web", "build").rglob("*")
    if path.is_file()
]
datas += collect_data_files("tzdata") + collect_data_files("webview")

binaries = []
bundled_hiddenimports = []
reflex_modules = sorted(
    module
    for module in metadata.packages_distributions()
    if module == "reflex" or module.startswith("reflex_")
)
reflex_distributions = {
    distribution
    for module, distributions in metadata.packages_distributions().items()
    if module in reflex_modules
    for distribution in distributions
}
for package in [*reflex_modules, "granian"]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    bundled_hiddenimports += package_hiddenimports
for distribution in reflex_distributions:
    datas += copy_metadata(distribution)

hiddenimports = bundled_hiddenimports + collect_submodules("ducklab") + [
    "tzdata",
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "webview.platforms.win32",
    "webview.platforms.winforms",
]

analysis = Analysis(
    [str(ROOT / "packaging" / "opscore_desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="opscore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=False,
    name="opscore",
)
