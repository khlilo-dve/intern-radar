"""web.py 纯函数测试。"""
import pytest
from web import extract_url


class TestExtractUrl:
    def test_simple_url(self):
        assert extract_url('请看 https://lagou.com/wn/jobs/12345678') == 'https://lagou.com/wn/jobs/12345678'

    def test_url_with_chinese_trailing(self):
        assert extract_url('https://example.com/job。') == 'https://example.com/job'

    def test_url_at_start(self):
        assert extract_url('https://example.com/path') == 'https://example.com/path'

    def test_no_url(self):
        assert extract_url('没有链接的消息') is None

    def test_empty(self):
        assert extract_url('') is None

    def test_multiple_urls(self):
        result = extract_url('看 https://a.com 和 https://b.com')
        assert result == 'https://a.com'

    def test_url_with_parens(self):
        assert extract_url('https://example.com/path)') == 'https://example.com/path'
