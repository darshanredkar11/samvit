"""Tests for binary file detection in codegraph (Phase 2.5)."""
from __future__ import annotations
import pytest
from pathlib import Path
from samvit.codegraph import _is_binary


def test_empty_file_is_not_binary(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    assert _is_binary(f) is False


def test_text_file_is_not_binary(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hello world')\n")
    assert _is_binary(f) is False


def test_png_detected_as_binary(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert _is_binary(f) is True


def test_jpeg_detected_as_binary(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    assert _is_binary(f) is True


def test_elf_detected_as_binary(tmp_path):
    f = tmp_path / "binary"
    f.write_bytes(b"\x7fELF" + b"\x00" * 100)
    assert _is_binary(f) is True


def test_pdf_detected_as_binary(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4" + b"\x00" * 100)
    assert _is_binary(f) is True


def test_null_bytes_in_content_detected(tmp_path):
    f = tmp_path / "corrupted.txt"
    f.write_bytes(b"hello\x00world\n")
    assert _is_binary(f) is True
