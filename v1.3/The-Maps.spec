# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('maps', 'maps'), ('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pywebview probes for whichever GUI toolkit is installed; we only ever
    # use its Windows/WebView2 backend (see islepilot.py: gui="edgechromium"),
    # so drop the other backends its PyInstaller hook otherwise bundles.
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gtk', 'cef'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='The-Maps',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\the_maps.ico'],
)
