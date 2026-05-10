"""llm.py 纯函数测试。"""
import pytest
from llm import _strip_fences, _sniff_image_mime


class TestStripFences:
    def test_json_fence(self):
        assert _strip_fences('```json\n{"a":1}\n```') == '{"a":1}'

    def test_plain_fence(self):
        assert _strip_fences('```\n{"a":1}\n```') == '{"a":1}'

    def test_no_fence(self):
        assert _strip_fences('  {"a":1}  ') == '{"a":1}'

    def test_extra_text_around(self):
        result = _strip_fences('Here is the result:\n```json\n{"a":1}\n```\nDone.')
        assert result == '{"a":1}'

    def test_nested_braces(self):
        result = _strip_fences('```json\n{"a": {"b": 2}}\n```')
        assert result == '{"a": {"b": 2}}'

    def test_empty_input(self):
        assert _strip_fences('') == ''

    def test_no_json(self):
        assert _strip_fences('hello world') == 'hello world'


class TestSniffImageMime:
    def test_jpeg(self):
        data = b'\xff\xd8\xff\xe0' + b'\x00' * 12
        assert _sniff_image_mime(data) == 'image/jpeg'

    def test_png(self):
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
        assert _sniff_image_mime(data) == 'image/png'

    def test_gif87a(self):
        data = b'GIF87a' + b'\x00' * 8
        assert _sniff_image_mime(data) == 'image/gif'

    def test_gif89a(self):
        data = b'GIF89a' + b'\x00' * 8
        assert _sniff_image_mime(data) == 'image/gif'

    def test_webp(self):
        assert _sniff_image_mime(b'RIFF\x00\x00\x00\x00WEBP') == 'image/webp'

    def test_too_short(self):
        assert _sniff_image_mime(b'\xff') is None

    def test_unknown(self):
        assert _sniff_image_mime(b'\x00' * 12) is None
