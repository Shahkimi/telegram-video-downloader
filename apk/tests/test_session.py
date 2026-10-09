import pytest
from telethon import errors

from tgdl.config import JsonConfigStore
from tgdl.telegram.session import LoginError, LoginStep, TelegramSession, normalize_phone


class FakeSigninClient:
    def __init__(self, authorized=False):
        self.authorized = authorized
        self.connected = False
        self.code_calls = []
        self.signins = []
        self.raise_on_send = None
        self.raise_on_code = None
        self.raise_on_password = None
        self.logged_out = False

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        if self.raise_on_send:
            raise self.raise_on_send
        self.code_calls.append(phone)

        class Sent:
            phone_code_hash = "HASH"

        return Sent()

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        self.signins.append(dict(phone=phone, code=code, password=password, phone_code_hash=phone_code_hash))
        if password is not None:
            if self.raise_on_password:
                raise self.raise_on_password
        elif self.raise_on_code:
            raise self.raise_on_code
        self.authorized = True

    async def log_out(self):
        self.logged_out = True
        self.authorized = False


@pytest.fixture
def session(paths):
    store = JsonConfigStore(paths.config_file)
    s = TelegramSession(paths, store)
    fake = FakeSigninClient()
    s._build_client = lambda api_id, api_hash: fake
    s.fake = fake
    return s


def test_normalize_phone():
    assert normalize_phone(" 60 (12) 345-6789 ") == "+60123456789"
    assert normalize_phone("+60123456789") == "+60123456789"


async def test_needs_api_until_credentials_are_set(session):
    assert await session.start() is LoginStep.NEED_API
    with pytest.raises(LoginError) as exc:
        await session.set_api_credentials("abc", "hash")
    assert exc.value.kind == "api_invalid"
    assert await session.set_api_credentials("123", " hash ") is LoginStep.NEED_PHONE
    assert session.store.load().api_hash == "hash"


async def test_full_login_with_two_factor(session):
    await session.set_api_credentials(1, "h")
    assert await session.send_code("+60 12 345 6789") is LoginStep.NEED_CODE
    assert session.fake.code_calls == ["+60123456789"]

    session.fake.raise_on_code = errors.SessionPasswordNeededError(request=None)
    assert await session.submit_code("12-345") is LoginStep.NEED_PASSWORD
    assert session.fake.signins[-1]["code"] == "12345"
    assert session.fake.signins[-1]["phone_code_hash"] == "HASH"

    assert await session.start() is LoginStep.NEED_PASSWORD      # reconnecting mid-login keeps the step
    session.fake.raise_on_password = errors.PasswordHashInvalidError(request=None)
    with pytest.raises(LoginError) as exc:
        await session.submit_password("wrong")
    assert exc.value.kind == "password_invalid"
    session.fake.raise_on_password = None
    assert await session.submit_password("right") is LoginStep.READY
    assert session.is_ready
    assert await session.ensure_connected() is session.fake


async def test_code_errors_are_mapped(session):
    await session.set_api_credentials(1, "h")
    await session.send_code("+60123456789")
    session.fake.raise_on_code = errors.PhoneCodeInvalidError(request=None)
    with pytest.raises(LoginError) as exc:
        await session.submit_code("1")
    assert exc.value.kind == "code_invalid" and session.step is LoginStep.NEED_CODE
    session.fake.raise_on_code = errors.PhoneCodeExpiredError(request=None)
    with pytest.raises(LoginError) as exc:
        await session.submit_code("1")
    assert exc.value.kind == "code_expired" and session.step is LoginStep.NEED_PHONE
    with pytest.raises(LoginError):
        await session.submit_code("")


async def test_send_code_errors(session):
    await session.set_api_credentials(1, "h")
    with pytest.raises(LoginError) as exc:
        await session.send_code("12")
    assert exc.value.kind == "phone_invalid"
    session.fake.raise_on_send = errors.PhoneNumberInvalidError(request=None)
    with pytest.raises(LoginError) as exc:
        await session.send_code("+60123456789")
    assert exc.value.kind == "phone_invalid"
    session.fake.raise_on_send = errors.FloodWaitError(request=None, capture=33)
    with pytest.raises(LoginError) as exc:
        await session.send_code("+60123456789")
    assert exc.value.kind == "flood" and exc.value.retry_after == 33


async def test_ensure_connected_requires_login(session):
    await session.set_api_credentials(1, "h")
    with pytest.raises(LoginError) as exc:
        await session.ensure_connected()
    assert exc.value.kind == "not_logged_in"


async def test_already_authorized_goes_straight_to_ready(session):
    session.fake.authorized = True
    session.store.save_credentials(1, "h")
    assert await session.start() is LoginStep.READY
    assert await session.is_authorized()


async def test_logout_clears_state_and_files(session, paths):
    session.fake.authorized = True
    session.store.save_credentials(1, "h")
    await session.start()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    sess_file = session.session_file()
    sess_file.write_text("x")
    await session.logout()
    assert session.fake.logged_out and not sess_file.exists()
    assert session.client is None and session.step is LoginStep.NEED_PHONE


async def test_changing_credentials_rebuilds_client(session, paths):
    built = []

    def build(api_id, api_hash):
        built.append((api_id, api_hash))
        return FakeSigninClient()

    session._build_client = build
    await session.set_api_credentials(1, "a")
    await session.set_api_credentials(2, "b")
    assert built == [(1, "a"), (2, "b")]


async def test_concurrent_start_connects_once(session):
    """Several screens ask for the login state at the same moment; only one connection may be opened."""
    import asyncio

    session.store.save_credentials(1234, "hash")
    connects = []
    real_connect = session.fake.connect

    async def slow_connect():
        connects.append(1)
        await asyncio.sleep(0.05)
        await real_connect()

    session.fake.connect = slow_connect
    steps = await asyncio.gather(*(session.start() for _ in range(5)))
    assert len(connects) == 1
    assert set(steps) == {LoginStep.NEED_PHONE}


async def test_check_reports_network_errors_instead_of_raising(session):
    session.store.save_credentials(1234, "hash")

    async def broken_connect():
        raise OSError("no route to host")

    session.fake.connect = broken_connect
    ok, error = await session.check()
    assert ok is False
    assert error is not None and error.kind == "network"


async def test_check_when_logged_in(session):
    session.store.save_credentials(1234, "hash")
    session.fake.authorized = True
    ok, error = await session.check()
    assert (ok, error) == (True, None)
