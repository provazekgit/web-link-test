import os

def win_longpath(path: str) -> str:
    """Vrátí cestu s \\?\\ prefixem, pokud je na Windows moc dlouhá."""
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    abs_path = os.path.abspath(path)
    if len(abs_path) >= 240:
        if abs_path.startswith("\\\\"):
            # UNC -> \\?\UNC\server\share\...
            return "\\\\?\\UNC\\" + abs_path.lstrip("\\")
        return "\\\\?\\" + abs_path
    return path

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
