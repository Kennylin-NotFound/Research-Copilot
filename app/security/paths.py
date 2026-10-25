"""Portable relative path policy for API uploads and Agent workspaces."""
from pathlib import Path, PureWindowsPath
import re

IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
UPLOAD_SUFFIXES = {'.txt', '.md', '.pdf', '.docx', '.csv', '.xlsx', '.xls'}
MAX_FILE_BYTES = 8 * 1024**2


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid session identifier')
    return value


def relative_parts(value, *, allow_empty=False):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError('Invalid relative path')
    windows = PureWindowsPath(value)
    if value.startswith(('/', '\\')) or windows.drive or windows.root:
        raise ValueError('Absolute paths are forbidden')
    if value in ('', '.') and allow_empty:
        return ()
    parts = value.replace('\\', '/').split('/')
    for part in parts:
        if not part or part in ('.', '..') or len(part) > 180 or part.endswith((' ', '.')):
            raise ValueError('Invalid path component')
        if any(char in part for char in ':<>"|?*') or part.split('.')[0].upper() in RESERVED:
            raise ValueError('Invalid portable filename')
    return tuple(parts)


def confined(root: Path, value: str = '', *, allow_empty=False):
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('Trusted workspace root must be absolute')
    parts = relative_parts(value, allow_empty=allow_empty)
    # Reject links/junctions in the root itself and every existing ancestor too.
    for current in (*reversed(root.parents), root):
        if current.is_symlink() or current.is_junction():
            raise ValueError('Linked storage roots are forbidden')
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError('Links and junctions are forbidden')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escaped workspace')
    return target


def session_directory(root, session_id):
    return confined(root, 'session_' + identifier(session_id))


def upload_name(value):
    parts = relative_parts(value)
    if len(parts) != 1 or Path(value).suffix.lower() not in UPLOAD_SUFFIXES:
        raise ValueError('Use a supported plain upload filename')
    return value
