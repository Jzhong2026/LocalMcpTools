"""Safety helpers — redaction of secrets before any persistence.

This module is intentionally pure (no I/O, no side effects) so it can
be unit-tested exhaustively and reused by every caller that might write
secrets to disk, the audit log, or an artifact.
"""