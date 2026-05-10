"""config.py 配置模型测试。"""
import pytest
from config import AppConfig, BitableConfig, EventConfig


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.record_write_as == 'user'
        assert cfg.reply_as == 'bot'
        assert cfg.bitable.base_name == '实习情报雷达'
        assert cfg.is_bootstrapped() is False

    def test_bootstrapped(self):
        cfg = AppConfig(bitable=BitableConfig(app_token='tok', table_id='tbl'))
        assert cfg.is_bootstrapped() is True

    def test_event_alias(self):
        cfg = AppConfig(event={'types': 'test', 'as': 'user'})
        assert cfg.event.as_identity == 'user'

    def test_from_dict(self):
        raw = {
            'bitable': {'app_token': 'x', 'table_id': 'y'},
            'record_write_as': 'bot',
        }
        cfg = AppConfig(**raw)
        assert cfg.bitable.app_token == 'x'
        assert cfg.record_write_as == 'bot'
