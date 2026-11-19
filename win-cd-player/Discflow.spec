# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

_sf_datas = [
    item for item in collect_data_files('_soundfile_data')
    if item[0].lower().endswith(('libsndfile_x64.dll', 'copying'))
]
_sd_datas = [
    item for item in collect_data_files('_sounddevice_data')
    if item[0].lower().endswith('libportaudio64bit.dll')
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui'), ('discflow.ico', '.')] + _sf_datas + _sd_datas,
    hiddenimports=['_soundfile_data', '_sounddevice_data', 'soundfile', 'sounddevice', 'mutagen.flac'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6'],
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
    name='Discflow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['discflow.ico'],
)
