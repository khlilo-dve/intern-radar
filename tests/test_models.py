"""models.py 数据模型测试。"""
import pytest
from models import IntelRecord, BITABLE_FIELD_SPEC


class TestIntelRecord:
    def _make(self, **overrides):
        defaults = dict(
            Company='测试公司',
            Tech_Vision=80,
            Product_Dominance=70,
            Leverage_Ratio=30,
            AI_Leverage=75,
            Asset_Sedimentation=80,
            Match_Score=78,
            Attack_Strategy='test strategy',
            Critical_Gap='test gap',
        )
        defaults.update(overrides)
        return IntelRecord(**defaults)

    def test_basic_construction(self):
        r = self._make()
        assert r.Company == '测试公司'
        assert r.Match_Score == 78

    def test_hard_tags_cap(self):
        r = self._make(Hard_Tags=['a', 'b', 'c', 'd', 'e', 'f', 'g'])
        assert len(r.Hard_Tags) == 5

    def test_red_flags_cap(self):
        r = self._make(Red_Flags=['a', 'b', 'c', 'd', 'e'])
        assert len(r.Red_Flags) == 5

    def test_attack_strategy_no_trim_under_limit(self):
        r = self._make(Attack_Strategy='x' * 499)
        assert len(r.Attack_Strategy) == 499

    def test_attack_strategy_at_limit(self):
        r = self._make(Attack_Strategy='x' * 500)
        assert len(r.Attack_Strategy) == 500

    def test_to_bitable_fields(self):
        r = self._make(Status='简历通过')
        fields = r.to_bitable_fields()
        assert fields['公司'] == '测试公司'
        assert fields['投递状态'] == '简历通过'
        assert '创建时间' in fields

    def test_status_default(self):
        r = self._make()
        fields = r.to_bitable_fields()
        assert fields['投递状态'] == '未投递'

    def test_no_status_no_notes(self):
        r = self._make()
        fields = r.to_bitable_fields()
        assert '备注' not in fields


class TestBitableFieldSpec:
    def test_field_count(self):
        assert len(BITABLE_FIELD_SPEC) == 19

    def test_status_is_single_select(self):
        status = next(f for f in BITABLE_FIELD_SPEC if f['field_name'] == '投递状态')
        assert status['type'] == 3
        assert len(status['property']['options']) == 6

    def test_notes_is_text(self):
        notes = next(f for f in BITABLE_FIELD_SPEC if f['field_name'] == '备注')
        assert notes['type'] == 'text'
