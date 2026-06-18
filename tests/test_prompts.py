"""Tests for new graph enrichment and lint prompt builders."""
import re

from secure_wiki.prompts import build_enrich_prompt, build_lint_prompt


def test_enrich_prompt_contains_nonce():
    system, user = build_enrich_prompt(claims=[], source_id="test-src")
    nonce_pat = re.compile(r"<claims-[0-9a-f]{16}>")
    assert nonce_pat.search(user)
    assert "{nonce}" not in system


def test_enrich_prompt_nonces_differ_each_call():
    _, u1 = build_enrich_prompt([], "s")
    _, u2 = build_enrich_prompt([], "s")
    assert u1 != u2


def test_lint_prompt_contains_claim_pairs():
    system, user = build_lint_prompt([("id-a", "text a"), ("id-b", "text b")])
    assert "id-a" in user and "id-b" in user


def test_enrich_prompt_source_id_in_user():
    _, user = build_enrich_prompt([], "my-source-doc")
    assert "my-source-doc" in user


def test_lint_prompt_system_has_contradiction():
    system, _ = build_lint_prompt([])
    assert "contradict" in system.lower()
