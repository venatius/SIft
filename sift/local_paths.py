"""Reject Windows network shares before traversing selected folders."""

import os
import stat
from pathlib import Path


def is_network_path(path):
    if os.name != "nt":
        return False
    import ctypes

    anchor = Path(os.path.abspath(path)).anchor
    if anchor.startswith("\\\\"):
        return True
    return bool(anchor) and ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor)) == 4


def is_link_or_reparse_point(path):
    info = os.lstat(path)
    attrs = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
