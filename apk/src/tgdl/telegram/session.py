"""
One owner for the Telethon client plus a login state machine that works without input().

The CLI drives it with input()/getpass, the Android app with dialogs. Both call the same methods:

    step = await session.start()
    NEED_API      -> set_api_credentials(api_id, api_hash)
    NEED_PHONE    -> send_code(phone)
    NEED_CODE     -> submit_code(code)           (resend_code() if it never arrived)
    NEED_PASSWORD -> submit_password(password)   (password_hint() shows the hint)
    READY         -> ensure_connected() returns a usable client
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import ConfigStore
from ..paths import AppPaths

log = logging.getLogger("tgdl.session")


class LoginStep(Enum):
    NEED_API = "need_api"
    NEED_PHONE = "need_phone"
    NEED_CODE = "need_code"
    NEED_PASSWORD = "need_password"
    READY = "ready"


class LoginError(Exception):
    """Something the user can act on. kind is stable, message is for display."""

    def __init__(self, kind: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


@dataclass
class AccountInfo:
    id: int
    name: str
    username: str | None
    phone: str | None

    @property
    def label(self) -> str:
        handle = f"@{self.username}" if self.username else "no username"
        return f"{self.name or 'Telegram user'} ({handle})"


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s().-]", "", raw or "")
    if phone and not phone.startswith("+") and phone.isdigit():
        phone = "+" + phone
    return phone


class TelegramSession:
    def __init__(self, paths: AppPaths, store: ConfigStore, *, device_model: str | None = None, app_version: str | None = None):
        self.paths = paths
        self.store = store
        self.device_model = device_model
        self.app_version = app_version
        self.client: Any = None
        self.step = LoginStep.NEED_API
        self._built_with: tuple[int, str] | None = None
        self._phone: str | None = None
        self._code_hash: str | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _start_lock(self) -> asyncio.Lock:
        """One lock per event loop; the app can restart its loop and an old lock would refuse the new one."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    # ---- client lifecycle ------------------------------------------
    def _credentials(self) -> tuple[int, str]:
        cfg = self.store.load()
        return cfg.api_id, cfg.api_hash

    def _build_client(self, api_id: int, api_hash: str) -> Any:
        from telethon import TelegramClient

        from .. import crypto_backend

        crypto_backend.install_fast_crypto()
        kwargs: dict[str, Any] = {}
        if self.device_model:
            kwargs["device_model"] = self.device_model
        if self.app_version:
            kwargs["app_version"] = self.app_version
        self.paths.session_base.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(self.paths.session_base), api_id, api_hash, connection_retries=10, **kwargs)

    async def _drop_client(self) -> None:
        client, self.client, self._built_with = self.client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        await self._drop_client()

    @property
    def is_ready(self) -> bool:
        return self.step is LoginStep.READY and self.client is not None

    # ---- state machine ---------------------------------------------
    async def start(self) -> LoginStep:
        """Connect and report what is needed next. Never prompts. Safe to call from several screens at once."""
        async with self._start_lock():
            return await self._start_unlocked()

    async def _start_unlocked(self) -> LoginStep:
        from telethon import errors

        api_id, api_hash = self._credentials()
        if not api_id or not api_hash:
            await self._drop_client()
            self.step = LoginStep.NEED_API
            return self.step

        if self.client is None or self._built_with != (api_id, api_hash):
            await self._drop_client()
            self.client = self._build_client(api_id, api_hash)
            self._built_with = (api_id, api_hash)

        try:
            if not self.client.is_connected():
                await self.client.connect()
            authorized = await self.client.is_user_authorized()
        except errors.ApiIdInvalidError as exc:
            self.step = LoginStep.NEED_API
            raise LoginError("api_invalid", "Telegram rejected this API ID / API hash. Check them at my.telegram.org.") from exc
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            raise LoginError("network", f"Cannot reach Telegram: {exc}") from exc

        if authorized:
            self.step = LoginStep.READY
        elif self.step in (LoginStep.NEED_CODE, LoginStep.NEED_PASSWORD) and self._phone:
            pass  # a login is in progress, keep its step
        else:
            self.step = LoginStep.NEED_PHONE
        return self.step

    async def check(self) -> tuple[bool, LoginError | None]:
        """(logged in, error). The error is set when the answer is unknown, for example while offline."""
        try:
            return (await self.start()) is LoginStep.READY, None
        except LoginError as exc:
            return False, exc

    async def is_authorized(self) -> bool:
        try:
            return (await self.start()) is LoginStep.READY
        except LoginError:
            return False

    async def set_api_credentials(self, api_id: int | str, api_hash: str) -> LoginStep:
        try:
            api_id_int = int(str(api_id).strip())
        except ValueError as exc:
            raise LoginError("api_invalid", "API ID must be a number.") from exc
        api_hash = (api_hash or "").strip()
        if api_id_int <= 0 or not api_hash:
            raise LoginError("api_invalid", "API ID and API hash are both required.")
        self.store.save_credentials(api_id_int, api_hash)
        await self._drop_client()
        self._phone = self._code_hash = None
        self.step = LoginStep.NEED_API
        return await self.start()

    async def _need_client(self) -> Any:
        if self.client is None:
            await self.start()
        if self.client is None:
            raise LoginError("api_invalid", "Enter your API ID and API hash first.")
        if not self.client.is_connected():
            try:
                await self.client.connect()
            except OSError as exc:
                raise LoginError("network", f"Cannot reach Telegram: {exc}") from exc
        return self.client

    async def send_code(self, phone: str) -> LoginStep:
        from telethon import errors

        number = normalize_phone(phone)
        if len(number) < 7:
            raise LoginError("phone_invalid", "Enter the phone number in international format, for example +60123456789.")
        client = await self._need_client()
        try:
            sent = await client.send_code_request(number)
        except errors.PhoneNumberInvalidError as exc:
            raise LoginError("phone_invalid", "Telegram says this phone number is not valid. Use the international format, for example +60123456789.") from exc
        except errors.PhoneNumberBannedError as exc:
            raise LoginError("phone_banned", "This phone number is banned from Telegram.") from exc
        except errors.ApiIdInvalidError as exc:
            self.step = LoginStep.NEED_API
            raise LoginError("api_invalid", "Telegram rejected this API ID / API hash. Check them at my.telegram.org.") from exc
        except errors.FloodWaitError as exc:
            raise LoginError("flood", f"Too many attempts. Try again in {exc.seconds} seconds.", exc.seconds) from exc
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            raise LoginError("network", f"Cannot reach Telegram: {exc}") from exc
        self._phone = number
        self._code_hash = sent.phone_code_hash
        self.step = LoginStep.NEED_CODE
        return self.step

    async def resend_code(self) -> LoginStep:
        if not self._phone:
            raise LoginError("phone_invalid", "Enter the phone number first.")
        return await self.send_code(self._phone)

    async def submit_code(self, code: str) -> LoginStep:
        from telethon import errors

        if not self._phone or not self._code_hash:
            raise LoginError("code_expired", "Request a new code first.")
        digits = re.sub(r"[\s-]", "", code or "")
        if not digits:
            raise LoginError("code_invalid", "Enter the code Telegram sent you.")
        client = await self._need_client()
        try:
            await client.sign_in(phone=self._phone, code=digits, phone_code_hash=self._code_hash)
        except errors.SessionPasswordNeededError:
            self.step = LoginStep.NEED_PASSWORD
            return self.step
        except errors.PhoneCodeInvalidError as exc:
            raise LoginError("code_invalid", "That code is not correct. Check it and try again.") from exc
        except (errors.PhoneCodeExpiredError, errors.PhoneCodeEmptyError) as exc:
            self.step = LoginStep.NEED_PHONE
            raise LoginError("code_expired", "The code expired. Request a new one.") from exc
        except errors.FloodWaitError as exc:
            raise LoginError("flood", f"Too many attempts. Try again in {exc.seconds} seconds.", exc.seconds) from exc
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            raise LoginError("network", f"Cannot reach Telegram: {exc}") from exc
        self.step = LoginStep.READY
        return self.step

    async def password_hint(self) -> str | None:
        from telethon import functions

        client = await self._need_client()
        try:
            pwd = await client(functions.account.GetPasswordRequest())
        except Exception:  # noqa: BLE001
            return None
        return getattr(pwd, "hint", None) or None

    async def submit_password(self, password: str) -> LoginStep:
        from telethon import errors

        if not password:
            raise LoginError("password_invalid", "Enter your two-step verification password.")
        client = await self._need_client()
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            raise LoginError("password_invalid", "Wrong password. Try again.") from exc
        except errors.FloodWaitError as exc:
            raise LoginError("flood", f"Too many attempts. Try again in {exc.seconds} seconds.", exc.seconds) from exc
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            raise LoginError("network", f"Cannot reach Telegram: {exc}") from exc
        self.step = LoginStep.READY
        return self.step

    def cancel_login(self) -> None:
        self._phone = self._code_hash = None
        if self.step in (LoginStep.NEED_CODE, LoginStep.NEED_PASSWORD):
            self.step = LoginStep.NEED_PHONE

    # ---- account ----------------------------------------------------
    async def me(self) -> AccountInfo | None:
        if self.client is None:
            return None
        try:
            me = await self.client.get_me()
        except Exception:  # noqa: BLE001
            return None
        if me is None:
            return None
        name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip()
        return AccountInfo(me.id, name, getattr(me, "username", None), getattr(me, "phone", None))

    async def logout(self) -> None:
        client = self.client
        if client is not None:
            try:
                if not client.is_connected():
                    await client.connect()
                await client.log_out()
            except Exception as exc:  # noqa: BLE001
                log.warning("log_out failed: %s", exc)
        await self._drop_client()
        for suffix in (".session", ".session-journal"):
            try:
                os.remove(str(self.paths.session_base) + suffix)
            except OSError:
                pass
        self._phone = self._code_hash = None
        self.step = LoginStep.NEED_PHONE if self._credentials()[0] else LoginStep.NEED_API

    async def ensure_connected(self) -> Any:
        """The client every download uses. Raises LoginError when not logged in."""
        step = await self.start()
        if step is not LoginStep.READY or self.client is None:
            raise LoginError("not_logged_in", "You are not logged in to Telegram. Open Settings > Account and log in.")
        return self.client

    def session_file(self) -> Path:
        return Path(str(self.paths.session_base) + ".session")
