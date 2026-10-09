"""Settings model plus two stores: .env (CLI, backward compatible) and config.json (app)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .paths import AppPaths, is_android

GB = 1024 ** 3
DEFAULT_MAX_TOTAL_SIZE = 20 * GB
CONFIG_VERSION = 1
_QUOTES = ('"', "'")


@dataclass
class YtdlpConfig:
    enabled: bool = True
    max_height: int | None = 1080
    cookies_file: str | None = None


@dataclass
class AndroidConfig:
    clipboard_autodetect: bool = True
    keep_alive: bool = True
    keep_screen_on: bool = False
    autostart_shared: bool = True


@dataclass
class AppConfig:
    api_id: int = 0
    api_hash: str = ""
    default_channel: int | str | None = None
    downloads_dir: str | None = None
    max_total_size_bytes: int = DEFAULT_MAX_TOTAL_SIZE
    max_concurrent_files: int = 3
    parallel_connections: int = 16
    chunk_size_kb: int = 512
    skip_existing: bool = True
    range_cap: int = 1000
    per_channel_folders: bool = True    # <downloads>/<channel title>/file
    nomedia: bool = False               # .nomedia in the download folder hides everything from galleries
    ytdlp: YtdlpConfig = field(default_factory=YtdlpConfig)
    android: AndroidConfig = field(default_factory=AndroidConfig)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def downloads_path(self, paths: AppPaths) -> str:
        return self.downloads_dir or str(paths.default_downloads_dir)

    @classmethod
    def defaults(cls) -> "AppConfig":
        cfg = cls()
        if is_android():
            cfg.parallel_connections = 8
        return cfg


def _int(value: Any, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return default


def parse_channel(raw: Any) -> int | str | None:
    """Channel setting as the CLI always read it: numeric ids become int, anything else stays text."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("-") or text.isdigit():
        try:
            return int(text)
        except ValueError:
            return text
    return text


def config_from_dict(data: dict[str, Any]) -> AppConfig:
    """Tolerant loader: unknown keys are ignored, bad values fall back to defaults."""
    base = AppConfig.defaults()
    yt = data.get("ytdlp") if isinstance(data.get("ytdlp"), dict) else {}
    an = data.get("android") if isinstance(data.get("android"), dict) else {}

    max_height = yt.get("max_height", base.ytdlp.max_height)
    if max_height in (None, "", 0, "0"):
        max_height = None
    else:
        max_height = _int(max_height, 1080, 144, 4320)

    cookies = yt.get("cookies_file")
    cookies = str(cookies) if cookies else None

    downloads = data.get("downloads_dir")
    downloads = str(downloads).strip() if downloads else None
    if downloads == "downloads":
        downloads = None

    return AppConfig(
        api_id=_int(data.get("api_id"), 0, 0),
        api_hash=str(data.get("api_hash") or "").strip(),
        default_channel=parse_channel(data.get("default_channel")),
        downloads_dir=downloads or None,
        max_total_size_bytes=_int(data.get("max_total_size_bytes"), DEFAULT_MAX_TOTAL_SIZE, 1),
        max_concurrent_files=_int(data.get("max_concurrent_files"), base.max_concurrent_files, 1, 8),
        parallel_connections=_int(data.get("parallel_connections"), base.parallel_connections, 1, 16),
        chunk_size_kb=_int(data.get("chunk_size_kb"), base.chunk_size_kb, 4, 1024),
        skip_existing=_bool(data.get("skip_existing"), base.skip_existing),
        range_cap=_int(data.get("range_cap"), base.range_cap, 1, 100000),
        per_channel_folders=_bool(data.get("per_channel_folders"), base.per_channel_folders),
        nomedia=_bool(data.get("nomedia"), base.nomedia),
        ytdlp=YtdlpConfig(
            enabled=_bool(yt.get("enabled"), base.ytdlp.enabled),
            max_height=max_height,
            cookies_file=cookies,
        ),
        android=AndroidConfig(
            clipboard_autodetect=_bool(an.get("clipboard_autodetect"), base.android.clipboard_autodetect),
            keep_alive=_bool(an.get("keep_alive"), base.android.keep_alive),
            keep_screen_on=_bool(an.get("keep_screen_on"), base.android.keep_screen_on),
            autostart_shared=_bool(an.get("autostart_shared"), base.android.autostart_shared),
        ),
    )


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    out = asdict(cfg)
    out["version"] = CONFIG_VERSION
    return out


class ConfigStore(Protocol):
    def load(self) -> AppConfig: ...
    def save(self, cfg: AppConfig) -> None: ...
    def save_credentials(self, api_id: int, api_hash: str) -> None: ...


def atomic_write_text(path: Path, text: str, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class JsonConfigStore:
    """config.json in the app data dir."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> AppConfig:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("config root must be an object")
        except FileNotFoundError:
            return AppConfig.defaults()
        except (OSError, ValueError):
            # Corrupt file: keep a copy so the user can recover credentials, start from defaults.
            try:
                os.replace(self.path, self.path.with_suffix(".json.bad"))
            except OSError:
                pass
            return AppConfig.defaults()
        return config_from_dict(data)

    def save(self, cfg: AppConfig) -> None:
        atomic_write_text(self.path, json.dumps(config_to_dict(cfg), indent=2), private=True)

    def save_credentials(self, api_id: int, api_hash: str) -> None:
        cfg = self.load()
        cfg.api_id = int(api_id)
        cfg.api_hash = str(api_hash).strip()
        self.save(cfg)


# ---------------------------------------------------------------------
# .env support (CLI). Same keys the original script used.
# ---------------------------------------------------------------------

def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in _QUOTES:
            val = val[1:-1]
        else:
            hash_at = val.find(" #")
            if hash_at != -1:
                val = val[:hash_at].rstrip()
        if key:
            values[key] = val
    return values


def _env_line_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    if key.startswith("export "):
        key = key[7:].strip()
    return key


class EnvConfigStore:
    """The .env file next to downloader.py. Values in the file win over process env vars."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _file_values(self) -> dict[str, str]:
        try:
            return parse_env_text(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return {}

    def load(self) -> AppConfig:
        file_vals = self._file_values()

        def get(key: str) -> str | None:
            return file_vals.get(key, os.environ.get(key))

        data: dict[str, Any] = {
            "api_id": get("API_ID"),
            "api_hash": get("API_HASH"),
            "default_channel": get("CHANNEL_ID"),
            "downloads_dir": get("DOWNLOADS_DIR"),
            "max_total_size_bytes": get("MAX_TOTAL_SIZE"),
            "max_concurrent_files": get("MAX_CONCURRENT_FILES"),
            "parallel_connections": get("PARALLEL_CONNECTIONS"),
            "range_cap": get("RANGE_CAP"),
            # The CLI always saved everything into one folder; keep that unless .env asks otherwise.
            "per_channel_folders": get("PER_CHANNEL_FOLDERS") or "0",
            "nomedia": get("NOMEDIA"),
        }
        yt = {
            "enabled": get("YTDLP_ENABLED"),
            "max_height": get("YTDLP_MAX_HEIGHT"),
            "cookies_file": get("YTDLP_COOKIES"),
        }
        data = {k: v for k, v in data.items() if v not in (None, "")}
        data["ytdlp"] = {k: v for k, v in yt.items() if v not in (None, "")}
        return config_from_dict(data)

    def update_values(self, updates: dict[str, str | None]) -> None:
        """Set keys in .env, keeping every other line (None values are skipped)."""
        updates = {k: str(v) for k, v in updates.items() if v is not None}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            lines = []
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            key = _env_line_key(line)
            if key in updates:
                out.append(f"{key}={updates[key]}\n")
                seen.add(key)
            else:
                out.append(line if line.endswith("\n") else line + "\n")
        for key, val in updates.items():
            if key not in seen:
                out.append(f"{key}={val}\n")
        atomic_write_text(self.path, "".join(out), private=True)
        for key, val in updates.items():
            os.environ[key] = val

    def save_credentials(self, api_id: int, api_hash: str) -> None:
        self.update_values({"API_ID": str(int(api_id)), "API_HASH": str(api_hash).strip()})

    def save(self, cfg: AppConfig) -> None:
        self.update_values({
            "API_ID": str(cfg.api_id) if cfg.api_id else None,
            "API_HASH": cfg.api_hash or None,
            "CHANNEL_ID": "" if cfg.default_channel is None else str(cfg.default_channel),
            "DOWNLOADS_DIR": cfg.downloads_dir or "",
            "MAX_TOTAL_SIZE": str(cfg.max_total_size_bytes),
            "MAX_CONCURRENT_FILES": str(cfg.max_concurrent_files),
            "PARALLEL_CONNECTIONS": str(cfg.parallel_connections),
            "RANGE_CAP": str(cfg.range_cap),
            "PER_CHANNEL_FOLDERS": "1" if cfg.per_channel_folders else "0",
            "NOMEDIA": "1" if cfg.nomedia else "0",
            "YTDLP_ENABLED": "1" if cfg.ytdlp.enabled else "0",
            "YTDLP_MAX_HEIGHT": str(cfg.ytdlp.max_height or 0),
            "YTDLP_COOKIES": cfg.ytdlp.cookies_file or "",
        })


def make_store(paths: AppPaths) -> ConfigStore:
    return EnvConfigStore(paths.config_file) if paths.mode == "cli" else JsonConfigStore(paths.config_file)
