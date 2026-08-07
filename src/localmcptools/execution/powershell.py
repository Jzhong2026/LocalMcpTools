"""PowerShell argument construction with deterministic UTF-8 output."""

_UTF8_PREFIX = (
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; chcp 65001 > $null; "
)


def build_powershell_args(command: str) -> list[str]:
    return [
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", _UTF8_PREFIX + command,
    ]


__all__ = ["build_powershell_args"]
