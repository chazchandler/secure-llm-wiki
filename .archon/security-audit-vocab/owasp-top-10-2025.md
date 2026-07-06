# OWASP Top 10:2025 — Auditor Reference

Source: <https://owasp.org/Top10/2025/> (the **2025** edition; the 2021 list is superseded).
Staged into every audit run's KB at `$ARTIFACTS_DIR/kb/owasp-top-10.md` by the `kb-vuln-catalog` node.

Auditors MUST consult this (and `cwe-top-25.md`) for canonical categories + CWE ids rather than relying on model memory. Each entry lists a one-line **audit lens** (what to look for) and representative **CWE mappings**.

---

## A01:2025 — Broken Access Control
**Lens:** Can a user act as another user/admin, or access data/functions their role forbids? Check missing function-level/object-level authz checks, IDOR, forced browsing, JWT/session tampering, principle-of-least-privilege violations.
**CWEs:** CWE-862 (Missing Authorization), CWE-863 (Incorrect Authorization), CWE-284 (Improper Access Control), CWE-639 (Authz Bypass via User-Controlled Key / IDOR), CWE-306 (Missing Authentication for Critical Function).

## A02:2025 — Security Misconfiguration
**Lens:** Defaults on, verbose errors exposed, unpatched/optional features enabled, missing security headers, permissive CORS, no HTTPS, cloud buckets open, debug endpoints live.
**CWEs:** CWE-16 (Configuration), CWE-611 (XXE), CWE-1188 (Insecure Default), CWE-15 (External Control of System/Configuration Setting).

## A03:2025 — Software Supply Chain Failures
**Lens:** Pinned/compromised dependencies, unsigned packages, untrusted CI/CD, missing SBOM, typosquatted libs, over-broad permissions in build pipelines. *(New for 2025 — split out from 2021 A06/A08.)*
**CWEs:** CWE-1104 (Use of Unmaintained Third-Party Components), CWE-1357 (Reliance on Uncontrolled Feature), CWE-494 (Download of Code Without Integrity Check).

## A04:2025 — Cryptographic Failures
**Lens:** Plaintext data in transit/at rest, weak/deprecated ciphers (MD5, SHA1, DES), hardcoded secrets, poor RNG, no certificate validation, password hashing too weak/ reversible.
**CWEs:** CWE-327 (Broken/Risky Crypto), CWE-328 (Reversible One-Way Hash), CWE-916 (Password Hash With Insufficient Cost), CWE-295 (Improper Cert Validation), CWE-798 (Hardcoded Credentials).

## A05:2025 — Injection
**Lens:** Untrusted input flowing into interpreters — SQL, OS command, LDAP, XPath, NoSQL, template, log, expression/language. Find the source→sink taint path; prefer parameterized APIs.
**CWEs:** CWE-89 (SQLi), CWE-78 (OS Command Injection), CWE-79 (XSS — generation/injection of web content), CWE-94 (Code Injection), CWE-77 (Command Injection).

## A06:2025 — Insecure Design
**Lens:** Missing abuse-case / threat modeling, no rate limiting, business-logic flaws, trust-boundary gaps that no amount of perfect code can fix. Look at the design, not just the implementation.
**CWEs:** CWE-209 (Information Exposure via Error), CWE-256 (Plaintext Storage of Password), CWE-501 (Trust Boundary Violation), CWE-522 (Insufficiently Protected Credentials).

## A07:2025 — Identification and Authentication Failures
**Lens:** Weak passwords, no MFA, credential stuffing exposure, session fixation, predictable tokens, session IDs in URLs, no invalidation on logout, automated-attack protections absent.
**CWEs:** CWE-287 (Improper Authentication), CWE-384 (Session Fixation), CWE-798 (Hardcoded Credentials), CWE-521 (Weak Password Requirements).

## A08:2025 — Software and Data Integrity Failures
**Lens:** Unsigned updates/CI artifacts, untrusted deserialization, plugins loaded without integrity checks, anti-tampering absent. Make assumptions about integrity explicit and verify them.
**CWEs:** CWE-502 (Deserialization of Untrusted Data), CWE-345 (Insufficient Verification of Data Authenticity), CWE-829 (Inclusion of Functionality from Untrusted Control Sphere).

## A09:2025 — Security Logging and Alerting Failures
**Lens:** Are security-relevant events (login, access denied, input validation failures, privileged actions) logged with enough context and alerted on? Can you detect/incident-respond at all?
**CWEs:** CWE-778 (Insufficient Logging), CWE-223 (Omission of Security-relevant Information), CWE-532 (Insertion of Sensitive Info into Log File).

## A10:2025 — Mishandling of Exceptional Conditions
**Lens:** Uncaught exceptions leak state or leave the app in an exploitable posture; error paths skip authz/cleanup; `fail-open` defaults; unbounded retries/state on malformed input. *(New for 2025 — errors and unhandled conditions as an attack surface.)*
**CWEs:** CWE-755 (Improper Handling of Exceptional Conditions), CWE-248 (Uncaught Exception), CWE-440 (Expected Behavior Violation), CWE-754 (Improper Check for Unusual/Exceptional Conditions).

---

**How auditors should use this:** when triaging a candidate finding, classify it under the A0X category above and cite the specific CWE id(s). Prefer the CWE Top 25:2025 (`cwe-top-25.md`) id when one fits — it carries a severity-sorted rank that informs the `severity` field in the findings schema.
