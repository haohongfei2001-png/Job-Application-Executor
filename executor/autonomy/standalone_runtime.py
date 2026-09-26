"""Owned standalone Python staging; no host base/stdlib is accepted as proof."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .release import RUNTIME_MANIFEST_NAME, runtime_manifest, verify_runtime_candidate

STANDALONE_MARKER = "release-standalone-runtime.json"

# Run inside the candidate interpreter with Python startup configuration ignored.
# Only owned runtime/release paths and operating-system libraries are admissible.
PROBE = r"""import sys
stage=10
try:

    import ctypes, importlib, pathlib, sys, sysconfig
    stage=10
    root=pathlib.Path(sys.argv[1]).resolve()
    release=pathlib.Path(sys.argv[2]).resolve()
    def owned(path):
        return pathlib.Path(path).resolve().is_relative_to(root)
    assert owned(sys.executable) and owned(sys.prefix) and owned(sys.base_prefix)
    assert not (root/'pyvenv.cfg').exists()
    stage=11
    assert all(owned(path) for path in sys.path)
    assert owned(sysconfig.get_path('stdlib'))
    sys.path.insert(0,str(release))
    from executor.autonomy.release import installed_dependencies_match
    stage=12
    assert installed_dependencies_match(release)
    stage=13
    for name in ('ssl','sqlite3','ctypes','hashlib','lzma','bz2','http.server',
                 'cryptography.fernet','pydantic','pydantic_core','greenlet',
                 'lxml.etree','pymupdf','docx','playwright.sync_api'):
        importlib.import_module(name)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as driver:
        assert driver.chromium.name == 'chromium'
    stage=14
    for module in tuple(sys.modules.values()):
        origin=getattr(module,'__file__',None)
        if origin:
            path=pathlib.Path(origin).resolve()
            assert path.is_relative_to(root) or path.is_relative_to(release)
    stage=15
    images=[]
    if sys.platform=='darwin':
        library=ctypes.CDLL(None)
        count=library._dyld_image_count
        count.restype=ctypes.c_uint32
        image=library._dyld_get_image_name
        image.argtypes=[ctypes.c_uint32]
        image.restype=ctypes.c_char_p
        images=[image(i).decode() for i in range(count())]
        system=tuple(map(pathlib.Path,('/usr/lib','/System/Library',
            '/System/Volumes/Preboot/Cryptexes/OS/usr/lib',
            '/System/Volumes/Preboot/Cryptexes/OS/System/Library')))
    elif sys.platform=='linux':
        images=[line.split(None,5)[5].strip()
                for line in pathlib.Path('/proc/self/maps').read_text().splitlines()
                if len(line.split(None,5))==6 and 'x' in line.split(None,5)[1]
                and line.split(None,5)[5].startswith('/')]
        system=(pathlib.Path('/usr/lib'),pathlib.Path('/lib'))
    else:
        raise AssertionError('unsupported standalone platform')
    stage=16
    for name in images:
        path=pathlib.Path(name).resolve()
        if path.is_relative_to(root):
            continue
        assert not path.name.startswith('libpython')
        assert any(path.is_relative_to(base.resolve()) for base in system)

except BaseException:
    sys.exit(stage)
"""

def _probe_code(root: Path, release: Path) -> int:
    try:
        result = subprocess.run(
            [str(root / "bin" / "python"), "-I", "-B", "-c", PROBE,
             str(root), str(release)],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=30,
        )
        return result.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return 90


def verify_standalone_runtime(root: str | Path, release: str | Path) -> bool:
    root, release = Path(root), Path(release)
    marker = root / STANDALONE_MARKER
    try:
        if (marker.is_symlink() or json.loads(marker.read_text()) !=
                {"format": "jae-standalone-runtime-v1"}):
            return False
    except (OSError, UnicodeError, ValueError):
        return False
    return verify_runtime_candidate(root, release) and _probe_code(root, release) == 0


def copy_standalone_runtime_candidate(source: str | Path, target: str | Path,
                                      release: str | Path) -> dict:
    """Materialize only internal file aliases, then prove relocated ownership."""
    source, target, release = Path(source).expanduser(), Path(target).expanduser(), Path(release)
    if (source.is_symlink() or not source.is_dir() or target.exists()
            or target.is_symlink() or (source / "pyvenv.cfg").exists()):
        raise ValueError("standalone_runtime_source_invalid")
    root = source.resolve()
    # Directory aliases are refused: no recursion through an unreviewed tree.
    try:
        for path in source.rglob("*"):
            if path.is_symlink() and (
                    not path.resolve().is_relative_to(root)
                    or not path.resolve().is_file()):
                raise ValueError("standalone_runtime_alias_invalid")
            if not path.is_file() and not path.is_dir():
                raise ValueError("standalone_runtime_file_invalid")
    except (OSError, RuntimeError):
        raise ValueError("standalone_runtime_alias_invalid") from None
    python = source / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("standalone_runtime_source_invalid")
    try:
        shutil.copytree(source, target, symlinks=False)
        (target / STANDALONE_MARKER).write_text(
            '{"format":"jae-standalone-runtime-v1"}\n', encoding="utf-8")
        manifest = runtime_manifest(target, release)
        (target / RUNTIME_MANIFEST_NAME).write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if not verify_runtime_candidate(target, release):
            raise ValueError("standalone_runtime_manifest_failed")
        code = _probe_code(target, release)
        if code != 0:
            phase = str(code) if code in range(10, 17) else "unavailable"
            raise ValueError("standalone_runtime_provenance_failed:" + phase)
        return manifest
    except BaseException:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        raise
