import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp missing")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def under(path, parent):
    try:
        Path(workspace_path(path)).relative_to(Path(workspace_path(parent)))
        return True
    except (ValueError, TypeError):
        return False


def workspace_path(value):
    path = os.fspath(value)
    if path.startswith("file:"):
        parsed = urlsplit(path)
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("nonlocal file URI workspace is unsupported")
        path = unquote(parsed.path)
    return str(Path(path).expanduser().resolve())


def redact(text, limit=1800, external=False):
    """Best-effort minimization, not an assertion that arbitrary prose is safe."""
    text = str(text or "")
    text = re.sub(r"(?is)<oai-mem-citation>.*?(?:</oai-mem-citation>|$)", "", text)
    text = re.sub(r":codex-file-citation\{[^}]*\}", "", text)
    text = re.sub(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----", "[REDACTED_KEY]", text)
    text = re.sub(r"```[\s\S]*?```", "[code omitted]", text)
    text = re.sub(r"(?is)<(?:environment_context|INSTRUCTIONS|system_reminder|permissions instructions)>.*?</(?:environment_context|INSTRUCTIONS|system_reminder|permissions instructions)>", "", text)
    text = re.sub(r"(?i)(?:Bearer\s+)[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED_SECRET]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|notion[_-]?token|token|password|passwd|secret|密码|密钥)\s*[\"']?\s*[:=：]\s*)[\"']?[^\s,;\"'}]+", r"\1[REDACTED_SECRET]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|ntn_[A-Za-z0-9_-]+|secret_[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[REDACTED_SECRET]", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)([?&](?:token|key|secret|password|signature|auth)=)[^&\s]+", r"\1[REDACTED_SECRET]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    if external:
        text = text.replace(str(Path.home()), "~")
        text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]", text)
        text = re.sub(r"(?:/Users|/home)/[^/\s\"<>]+(?:/[^\s\"<>]*)?", "[local path]", text)
    text = text.replace("\x00", "").strip()
    return text[:limit] + (" …[truncated]" if len(text) > limit else "")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def lock(path):
    import fcntl
    Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "a") as f:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another Daily Review process holds the local state lock")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
