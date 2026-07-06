#!/usr/bin/env bun
/**
 * Deterministic semantic de-duplication for adjudicated security findings.
 *
 * Reads the judge node's typed sidecar (`$ARTIFACTS_DIR/nodes/judge.md` — a JSON
 * array of adjudicated findings) and collapses findings that the SAME PATCH would
 * fix, mirroring MDASH's "patch-based grouping": same CWE + same file + same code
 * location (line-proximity) or same described issue (title) are one finding.
 *
 * Within each group the highest-severity / highest-confidence instance wins as the
 * representative; unique evidence strings and source tags are merged, and the
 * collapsed ids are recorded (`merged_ids`, `duplicate_count`).
 *
 * Reads `ARTIFACTS_DIR` from `process.env` (NOT a shell `$VAR`). Writes the deduped
 * array to `$ARTIFACTS_DIR/findings-deduped.json` ATOMICALLY (temp + rename). Exits
 * 0 always (missing/empty/unparseable input → empty result, exit 0) — a downstream
 * report node still needs a well-formed (possibly empty) file to read. Prints a
 * small JSON summary to stdout (becomes `$dedupe-findings.output`).
 *
 * NOTE on `nearestSymbol`: the shared findings schema carries no `symbol` field
 * (adding one would break strict `$id.output.field` access for consumers), so the
 * grouping proxy is CWE + file + (line-proximity | title). Two findings same CWE,
 * same file, within a ~20-line window collapse — that is the patch-based cluster.
 */
import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

interface Finding {
  [k: string]: unknown;
}

const SEVERITY_ORDER: Record<string, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  info: 1,
};
const CONFIDENCE_ORDER: Record<string, number> = { high: 3, medium: 2, low: 1 };
const LINE_WINDOW = 20; // findings within N lines in the same file+CWE → same patch

const artifactsDir = process.env['ARTIFACTS_DIR'] ?? '';
if (!artifactsDir) {
  process.stderr.write('ARTIFACTS_DIR env var is required\n');
  process.exit(1);
}

function parseLineNum(raw: unknown): number | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) return Math.floor(raw);
  if (typeof raw === 'string') {
    const m = /\d+/.exec(raw);
    if (m) return parseInt(m[0], 10);
  }
  return null;
}

function normalizeCwe(raw: unknown): string {
  const s = String(raw ?? '').trim().toUpperCase().replace(/\s+/g, '');
  if (!s) return 'CWE-UNKNOWN';
  return s.startsWith('CWE') ? s : `CWE-${s}`;
}

function normalizePath(raw: unknown): string {
  // Strip leading ./ and ../, keep case (case-sensitive FS safety). Collapse slashes.
  return String(raw ?? '')
    .trim()
    .replace(/^\.\//, '')
    .replace(/^(\.\.\/)+/, '')
    .replace(/\/+/g, '/');
}

function normalizeTitle(raw: unknown): string {
  return String(raw ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function severityOf(f: Finding): number {
  return SEVERITY_ORDER[String(f['severity'] ?? '').toLowerCase()] ?? 0;
}
function confidenceOf(f: Finding): number {
  return CONFIDENCE_ORDER[String(f['confidence'] ?? '').toLowerCase()] ?? 0;
}

/** Extract the JSON array from the typed sidecar text (tolerant of ```json fences). */
function readJudgeFindings(path: string): Finding[] {
  if (!existsSync(path)) return [];
  let text = readFileSync(path, 'utf8').trim();
  if (!text) return [];
  // Strip a surrounding ```json ... ``` fence if present.
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) text = fence[1].trim();
  // Slice from first '[' to last ']' — tolerant of leading/trailing prose.
  const start = text.indexOf('[');
  const end = text.lastIndexOf(']');
  if (start === -1 || end === -1 || end < start) return [];
  try {
    const parsed = JSON.parse(text.slice(start, end + 1));
    return Array.isArray(parsed) ? (parsed.filter((f) => f && typeof f === 'object') as Finding[]) : [];
  } catch {
    return []; // unparseable adjudication → empty result, do not crash the pipeline
  }
}

// ── Load adjudicated findings ──
const findings = readJudgeFindings(resolve(artifactsDir, 'nodes', 'judge.md'));

// ── Group by CWE + file, then cluster within each group by line-proximity | title ──
// Composite dedupe key: cwe::file::cluster, where cluster is a line band or the
// normalized title (when no reliable line is available).
const groups = new Map<string, Finding[]>();
for (const f of findings) {
  const cwe = normalizeCwe(f['cwe']);
  const file = normalizePath(f['file']);
  const line = parseLineNum(f['line']);
  const cluster = line !== null ? `L${Math.floor(line / LINE_WINDOW)}` : `T${normalizeTitle(f['title'])}`;
  const key = `${cwe}::${file}::${cluster}`;
  const bucket = groups.get(key);
  if (bucket) bucket.push(f);
  else groups.set(key, [f]);
}

// ── Merge each group into one representative finding ──
function pickRepresentative(bucket: Finding[]): Finding {
  // Highest severity wins; tiebreak highest confidence; tiebreak lowest line.
  return [...bucket].sort((a, b) => {
    const sev = severityOf(b) - severityOf(a);
    if (sev !== 0) return sev;
    const con = confidenceOf(b) - confidenceOf(a);
    if (con !== 0) return con;
    const la = parseLineNum(a['line']) ?? Number.MAX_SAFE_INTEGER;
    const lb = parseLineNum(b['line']) ?? Number.MAX_SAFE_INTEGER;
    return la - lb;
  })[0];
}

const deduped: Finding[] = [];
let collapsedGroups = 0;
for (const bucket of groups.values()) {
  const rep = pickRepresentative(bucket);
  const mergedIds = bucket
    .map((f) => f['id'])
    .filter((id): id is string => typeof id === 'string' && id.length > 0);
  const sources = Array.from(
    new Set(
      bucket
        .map((f) => f['source'])
        .filter((s): s is string => typeof s === 'string' && s.length > 0),
    ),
  ).sort();
  // Merge unique evidence strings (cap at 6 to bound report size).
  const evidence = Array.from(
    new Set(
      bucket
        .map((f) => f['evidence'])
        .filter((e): e is string => typeof e === 'string' && e.trim().length > 0),
    ),
  ).slice(0, 6);

  const merged: Finding = {
    ...rep,
    duplicate_count: bucket.length,
    merged_ids: mergedIds,
    ...(sources.length > 0 ? { sources } : {}),
    ...(evidence.length > 1 || (evidence.length === 1 && evidence[0] !== rep['evidence'])
      ? { evidence: evidence.join('\n---\n') }
      : {}),
  };
  deduped.push(merged);
  if (bucket.length > 1) collapsedGroups += 1;
}

// Most-severe first in the output.
deduped.sort((a, b) => severityOf(b) - severityOf(a));

// ── Atomic write ──
const finalPath = resolve(artifactsDir, 'findings-deduped.json');
const tmpPath = finalPath + '.tmp';
writeFileSync(tmpPath, JSON.stringify(deduped, null, 2));
renameSync(tmpPath, finalPath);

console.log(
  JSON.stringify({ before: findings.length, after: deduped.length, groups: collapsedGroups }),
);
