"""handlers.py 纯函数测试。"""
import json
import pytest
from handlers import _as_text, _extract_image_key


class TestAsText:
    def test_plain_string(self):
        assert _as_text('hello') == 'hello'

    def test_json_with_text_key(self):
        assert _as_text('{"text": "hello"}') == 'hello'

    def test_dict_with_text_key(self):
        assert _as_text({"text": "hello"}) == 'hello'

    def test_none(self):
        assert _as_text(None) == ''

    def test_empty_string(self):
        assert _as_text('') == ''

    def test_non_json_brace_string(self):
        assert _as_text('{not json}') == '{not json}'

    def test_integer(self):
        assert _as_text(42) == '42'


class TestExtractImageKey:
    def test_from_dict(self):
        assert _extract_image_key({"image_key": "img_v3_xxx"}) == "img_v3_xxx"

    def test_from_nested_dict(self):
        data = {"message": {"content": {"image_key": "img_v3_abc"}}}
        assert _extract_image_key(data) == "img_v3_abc"

    def test_from_json_string(self):
        s = json.dumps({"image_key": "img_v3_123"})
        assert _extract_image_key(s) == "img_v3_123"

    def test_from_regex(self):
        assert _extract_image_key("some text img_v3_abc123 more text") == "img_v3_abc123"

    def test_no_key(self):
        assert _extract_image_key({"text": "hello"}) is None

    def test_none(self):
        assert _extract_image_key(None) is None
