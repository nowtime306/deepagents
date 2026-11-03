"""Lexical overlap is evidence filtering, not semantic answer verification."""

import pytest

from app.retrieval_evidence import coverage, guidance


@pytest.mark.parametrize(
    "query", ["平台创始人的身高是多少？", "股票明天收盘价格是多少？", "的 是 多少", "a the what is"]
)
def test_shared_question_characters_and_isolated_generic_overlap_are_insufficient(query):
    assert coverage(query, "平台服务价格是每月29元。管理员不得索取用户密码。") < 0.25


@pytest.mark.parametrize(
    "query,text",
    [
        ("A1 上下文是多少？", "A1 上下文 32000"),
        ("API Key", "API Key 不回显"),
        ("产品", "产品保修三年"),
        ("退款几天到账？", "退款审核后三天到账"),
    ],
)
def test_identifiers_and_contiguous_keywords_remain_usable(query, text):
    assert coverage(query, text) >= 0.2


def test_single_characters_do_not_create_evidence_or_join_across_boundaries():
    assert coverage("量子通信延迟", "工作日内发货，迟到可退款") == 0
    assert coverage("上 下", "上下文") == 0
    assert "不得" in guidance(False)


@pytest.mark.parametrize("query", ["功能", "性能", "目的地", "被子"])
def test_characters_inside_domain_words_are_not_stripped(query):
    assert coverage(query, query) == 1
