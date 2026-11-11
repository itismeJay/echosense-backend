def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input matches literally (use with escape="\\")."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
