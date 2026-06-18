# Vibecoder Security Review: secure-llm-wiki

**Date:** 2026-06-11
**Stack:** Python CLI, Ollama/Anthropic LLMs, git-backed flat-file store, no web server

## Summary

This is an unusually security-conscious codebase for its size — it was built specifically to harden against prompt injection, and most of the relevant controls are in place and correct. No critical or high-severity issues found. Four low/medium findings below, mostly around the system's interaction with the outside world rather than its core injection defenses.

## Findings

---

### [MEDIUM] SSRF via unvalidated URL in `ingest` command

**Location:** `src/secure_wiki/__main__.py:147`

**Issue:** The ingestion command fetches any `http://` or `https://` URL with no allowlist or network-range validation:

```python
with urllib.request.urlopen(source, timeout=30) as resp:
```

**Impact:** In an automated pipeline (CI job, a script looping over URLs from an issue tracker, etc.), an attacker who controls the URL argument could point it at internal services — cloud metadata endpoints (`http://169.254.254.169/latest/meta-data/`), localhost services, or internal APIs. The fetched content then flows into the LLM pipeline as untrusted input.

For purely interactive CLI use the risk is low (the user is in control of the URL). The `timeout=30` is set, which is good.

**Attack scenario (automated use):** Attacker submits `http://169.254.169.254/latest/meta-data/iam/security-credentials/` as a wiki source URL → credentials returned → processed by LLM → potentially exfiltrated via prompt injection into extracted claims.

**Fix:**
```python
from urllib.parse import urlparse
import ipaddress

def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("https",):  # block http and others
        return False
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_global
    except ValueError:
        return True  # hostname, not IP — allow
```

---

### [LOW] Full environment (including API keys) passed to git subprocess

**Location:** `src/secure_wiki/store/wiki_store.py:54-62`

**Issue:**

```python
_GIT_ENV = {
    **os.environ,          # includes ANTHROPIC_API_KEY
    "GIT_AUTHOR_NAME": "secure-llm-wiki",
    ...
}

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=_GIT_ENV, ...)
```

`ANTHROPIC_API_KEY` (and any other env vars) are passed to every git subprocess. If the `wiki_data` repo contains attacker-controlled git hooks (e.g., a `pre-commit` hook in a cloned repo that was booby-trapped), those hooks can read environment variables.

**Impact:** API key exfiltration via malicious git hook in the managed `wiki_data` directory.

**Fix:** Strip sensitive vars from the subprocess env:

```python
_STRIP_KEYS = {"ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
_GIT_ENV = {k: v for k, v in os.environ.items() if k not in _STRIP_KEYS}
_GIT_ENV.update({"GIT_AUTHOR_NAME": "secure-llm-wiki", ...})
```

---

### [LOW] Trust-marker strip regex misses Unicode homoglyphs

**Location:** `src/secure_wiki/prompts.py:189`

**Issue:**

```python
_TRUST_MARKER_RE = re.compile(r"\[[TSU]\]")  # strips [T] [S] [U]
```

This only strips ASCII `T`, `S`, `U`. A malicious source could embed visually-identical Unicode characters (e.g., Cyrillic `Т` U+0422, Greek `ꓤ` U+A4E4) to produce a marker like `[Т]` that renders identically in most editors but passes through unstripped.

The nonce boundary is the primary injection defense here, so this is a defense-in-depth gap rather than a bypass of the core control. The injection corpus tests for zero-width and hidden characters (good), but don't appear to cover Unicode homoglyph trust markers specifically.

**Fix:** Add Unicode-aware normalization before stripping, or use a tighter regex anchored to ASCII:

```python
import unicodedata
_TRUST_MARKER_RE = re.compile(r"\[[TSU]\]", re.ASCII)  # already ASCII-only

def strip_trust_markers(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)  # collapse homoglyphs
    return _TRUST_MARKER_RE.sub("", normalized).strip()
```

---

### [LOW] No dependency lockfile — supply-chain drift

**Location:** `pyproject.toml`

**Issue:** All dependencies use loose lower-bound pins (`anthropic>=0.40.0`, `pyyaml>=6.0`, etc.) with no lockfile (`requirements.lock`, `poetry.lock`, or `uv.lock`). A fresh `pip install` picks up whatever is latest, including a hypothetically compromised minor release.

`pypdf` in particular has had CVEs for malformed-PDF crashes; without pinning, a bad release could reach users.

**Fix:** Generate and commit a lockfile:
```bash
# with pip-tools
pip-compile pyproject.toml -o requirements.lock

# or with uv (faster)
uv lock
```

---

## What's Notably Good

These are controls worth explicitly calling out as correctly implemented:

- **Nonce boundary**: `secrets.token_hex(8)` (64-bit entropy) per call; model must echo the nonce or the response is rejected (`verify_extraction_envelope`, `verify_review_envelope`). Attacker cannot pre-craft a closing tag.
- **Trust marker stripping**: `[T]`/`[S]`/`[U]` tokens stripped from all source-derived text before it enters context, preventing trust forgery.
- **Hard untrusted floor**: `c.trust_level != TrustLevel.UNTRUSTED` in context loader — untrusted claims are never fed into the query LLM.
- **Two-model adversarial review**: Extraction and review use different models (`EXTRACTION_MODEL` ≠ `REVIEW_MODEL`), preserving 4-eyes independence.
- **No `shell=True`**: Git is invoked as `["git", *args]` list form throughout — no shell injection surface.
- **`yaml.safe_load`**: Used consistently; no `yaml.load` with arbitrary constructors.
- **`.env` in `.gitignore`**: API keys cannot accidentally be committed.

## Quick Wins

1. Pin dependencies: `uv lock` or `pip-compile`
2. Strip sensitive env vars from `_GIT_ENV` in `wiki_store.py`
3. Add `NFKC` normalization in `strip_trust_markers()`
4. If ingest is ever used in automation, add an SSRF guard on the URL

## Context

**Auth surface:** None (local CLI only)
**Web surface:** None (no HTTP server)
**Primary threat model:** Prompt injection via ingested wiki content — well-defended
**Secondary concerns:** Supply-chain and local privilege escalation if wiki_data repo is attacker-controlled
