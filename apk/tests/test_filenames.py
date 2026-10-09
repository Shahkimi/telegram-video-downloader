import datetime as dt

from tests.conftest import FakeMessage
from tgdl.engine.telegram_dl import build_filename, resolve_output_path
from tgdl.util import clean_filename, format_eta, format_size, format_speed


def test_clean_filename():
    assert clean_filename('a/b\\c*d?e:f"g<h>i|j') == "abcdefghij"
    assert clean_filename("  many    spaces\tand\nlines ") == "many spaces and lines"


def test_name_from_caption_first_line_truncated():
    msg = FakeMessage(text="My Holiday: Day 1\nsecond line", name="IMG_001.mp4")
    assert build_filename(msg) == "My Holiday Day 1.mp4"
    long = FakeMessage(text="x" * 80)
    assert build_filename(long) == "x" * 50 + ".mp4"


def test_name_falls_back_to_file_name_then_date():
    assert build_filename(FakeMessage(text="", name="clip.mov", ext=".mov")) == "clip.mov"
    assert build_filename(FakeMessage(text="", name=None)) == "file_20260102_030405.mp4"


def test_extension_not_duplicated_and_bin_fallback():
    assert build_filename(FakeMessage(text="song.mp4")) == "song.mp4"
    assert build_filename(FakeMessage(text="something", ext="")) == "something.bin"
    assert build_filename(FakeMessage(text="doc", ext="pdf")) == "doc.pdf"


def test_no_trailing_space_before_extension():
    assert build_filename(FakeMessage(text="Tata " + "y" * 60)).endswith(".mp4")
    assert " .mp4" not in build_filename(FakeMessage(text=("a" * 49) + " b"))


def test_output_path_free_name(tmp_path):
    path, skip = resolve_output_path(str(tmp_path), "a.mp4", 10, 5, set())
    assert path == str(tmp_path / "a.mp4") and not skip


def test_output_path_skips_same_size_existing(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x" * 10)
    path, skip = resolve_output_path(str(tmp_path), "a.mp4", 10, 5, set())
    assert path == str(tmp_path / "a.mp4") and skip
    path, skip = resolve_output_path(str(tmp_path), "a.mp4", 10, 5, set(), skip_existing=False)
    assert path == str(tmp_path / "a.mp4") and not skip      # overwrite, like the old script


def test_output_path_different_size_gets_suffix(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x" * 3)
    path, skip = resolve_output_path(str(tmp_path), "a.mp4", 10, 5, set())
    assert path == str(tmp_path / "a (msg5).mp4") and not skip
    (tmp_path / "a (msg5).mp4").write_bytes(b"y" * 4)
    path, skip = resolve_output_path(str(tmp_path), "a.mp4", 10, 5, set())
    assert path == str(tmp_path / "a (msg5-2).mp4") and not skip


def test_output_path_avoids_files_being_written_right_now(tmp_path):
    reserved = {str(tmp_path / "same.mp4")}
    path, skip = resolve_output_path(str(tmp_path), "same.mp4", 10, 7, reserved)
    assert path == str(tmp_path / "same (msg7).mp4") and not skip


def test_formatters():
    assert format_size(0) == "0 B" and format_size(1536) == "1.5 KB" and format_size(5 * 1024 ** 3) == "5.0 GB"
    assert format_speed(0) == "-" and format_speed(2048) == "2.0 KB/s"
    assert format_eta(None) == "-" and format_eta(75) == "1:15" and format_eta(3725) == "1:02:05"
