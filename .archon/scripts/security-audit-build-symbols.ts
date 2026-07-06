#!/usr/bin/env bun
/**
 * Deterministic symbol INDEX for the security-audit knowledge base.
 *
 * Extracts symbol DECLARATIONS only (name + kind + file + line) by walking the
 * repo from `process.cwd()` with `node:fs` + per-language regex maps. This is
 * the unconditional structural *floor*: the `threat-model` node's one-shot map,
 * the Codex / no-Serena auditor fallback, and a browsable artifact.
 *
 * Deliberately NOT a call graph — no call edges, no sink sites are extracted.
 * A regex call graph would be shallow and misleading; ship none rather than a
 * bad one. Serena (the live MCP semantic layer) owns call-graph / reference /
 * reachability depth for the Claude auditors; `kb-attack-surface` owns sinks.
 *
 * Pure `node:fs` + regex, ZERO external deps — runs anywhere bun runs. `rg` /
 * `ctags` / `tree-sitter` are an optional enhancement (detected + skipped if
 * absent), NOT a hard dependency.
 *
 * Reads `ARTIFACTS_DIR` from `process.env` (NOT a shell `$VAR` — inline bun
 * script nodes receive env, not shell substitution). Writes the bounded index
 * to `$ARTIFACTS_DIR/kb/symbols.json` ATOMICALLY (temp + rename) so a mid-node
 * crash never leaves a corrupt half-file a downstream node or resume reads.
 *
 * Exits 0 regardless of findings (deterministic node — the caller decides what
 * matters), mirroring the marketplace-security-scan precedent. Prints a small
 * JSON summary to stdout (becomes `$kb-symbols.output`).
 */
import { readdirSync, readFileSync, lstatSync, mkdirSync, writeFileSync, renameSync } from 'node:fs';
import { resolve, relative, extname } from 'node:path';

interface SymbolEntry {
  file: string;
  name: string;
  kind: string;
  line: number;
}

interface SymbolsIndex {
  files: number;
  languages: Record<string, number>;
  symbols: SymbolEntry[];
  truncated: boolean;
}

// Hard cap so symbols.json stays bounded on huge repos (KB passes by path, but
// the index itself is still read whole by the threat-model node — keep it sane).
const MAX_SYMBOLS = 5000;

// Directories that are never source we want to index (generated/vendored/build/dev-env).
const SKIP_DIRS = new Set([
  '.git',
  'node_modules',
  'dist',
  'build',
  '.archon',
  '.next',
  '.cache',
  'coverage',
  'out',
  'target',
  'venv',
  '.venv',
  '__pycache__',
  '.pytest_cache',
  'vendor',
  // Dev-env / package-manager shmlinks that point OUT of the repo (nix store, global
  // caches) — never source, and following them can descend into huge external trees.
  '.devenv',
  '.direnv',
  '.nix',
  '.turbo',
  '.pnpm-store',
]);

const MAX_FILE_BYTES = 256 * 1024; // skip minified/huge files

/** Map a file extension to a declaration extractor (returns {name,kind} or null). */
type LineExtractor = (line: string) => { name: string; kind: string } | null;

// Per-language DECLARATION extractors (declarations only — no calls, no sinks).
// Each pattern anchors on the structural keyword at/after optional leading
// modifiers so it matches definitions, not invocations.
const EXTRACTORS: Record<string, LineExtractor> = {
  // ── TypeScript / JavaScript ──
  '.ts': tsJs,
  '.tsx': tsJs,
  '.js': tsJs,
  '.jsx': tsJs,
  // ── Python ──
  '.py': (line) => {
    let m = /^(?:async\s+)?def\s+([A-Za-z_][\w]*)/.exec(line);
    if (m) return { name: m[1], kind: 'function' };
    m = /^class\s+([A-Za-z_][\w]*)/.exec(line);
    if (m) return { name: m[1], kind: 'class' };
    return null;
  },
  // ── Go ──
  '.go': (line) => {
    let m = /^func\s+(?:\([^)]*\)\s+)?([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'function' };
    m = /^type\s+([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'type' };
    return null;
  },
  // ── Rust ──
  '.rs': (line) => {
    let m = /^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'function' };
    m = /^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'struct' };
    m = /^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'enum' };
    m = /^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_]\w*)/.exec(line);
    if (m) return { name: m[1], kind: 'trait' };
    m = /^\s*(?:pub(?:\([^)]*\))?\s+)?impl(?:<[^>]*>)?\s+(\w+)/.exec(line);
    if (m) return { name: m[1], kind: 'impl' };
    return null;
  },
  // ── C / C++ ── (declarations only: typedef / struct / reliable subset)
  '.c': cCpp,
  '.h': cCpp,
  '.cpp': cCpp,
  '.cc': cCpp,
  '.cxx': cCpp,
  '.hpp': cCpp,
  '.hxx': cCpp,
  // ── Java ──
  '.java': (line) => {
    const t = line.trim();
    let m =
      /(?:^|\s)(?:public|protected|private|abstract|final|static|\s)*(?:class|interface|enum)\s+([A-Za-z_]\w*)/.exec(
        t,
      );
    if (m) return { name: m[1], kind: 'class' };
    // method: modifiers? type name(...) — best-effort, declarations only
    m =
      /^\s*(?:public|protected|private|static|final|synchronized|abstract|native|\s)+[\w<>\[\],?\s]+?\s+([A-Za-z_]\w*)\s*\(/.exec(
        line,
      );
    if (m && !/^(if|for|while|switch|catch|return|new)$/.test(m[1])) {
      return { name: m[1], kind: 'method' };
    }
    return null;
  },
};

function tsJs(line: string): { name: string; kind: string } | null {
  let m = /^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)/.exec(line);
  if (m) return { name: m[1], kind: 'class' };
  m = /^(?:export\s+)?(?:abstract\s+)?interface\s+([A-Za-z_$][\w$]*)/.exec(line);
  if (m) return { name: m[1], kind: 'interface' };
  m = /^(?:export\s+)?type\s+([A-Za-z_$][\w$]*)/.exec(line);
  if (m) return { name: m[1], kind: 'type' };
  m = /^(?:export\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)/.exec(line);
  if (m) return { name: m[1], kind: 'function' };
  m = /^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)/.exec(line);
  if (m) return { name: m[1], kind: 'const' };
  return null;
}

function cCpp(line: string): { name: string; kind: string } | null {
  let m = /^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)/.exec(line);
  if (m) return { name: m[1], kind: 'struct' };
  m = /^\s*(?:typedef\s+)?enum\s+([A-Za-z_]\w*)/.exec(line);
  if (m) return { name: m[1], kind: 'enum' };
  m = /^\s*typedef\s+.+?\s([A-Za-z_]\w*)\s*;/.exec(line);
  if (m) return { name: m[1], kind: 'typedef' };
  return null;
}

function walkSourceFiles(dir: string, base: string, out: string[]): void {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return; // unreadable dir — skip gracefully
  }
  for (const entry of entries) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = resolve(dir, entry);
    let st;
    try {
      // lstat, NOT stat: never follow symlinks. Symlinked dirs (bun workspace
      // node_modules, devenv/nix profile links) can point out of the repo into
      // huge external trees or form cycles — following them hangs the walk.
      st = lstatSync(full);
    } catch {
      continue;
    }
    if (st.isSymbolicLink()) continue;
    if (st.isDirectory()) {
      walkSourceFiles(full, base, out);
    } else if (st.isFile() && st.size <= MAX_FILE_BYTES) {
      out.push(relative(base, full));
    }
  }
}

const artifactsDir = process.env['ARTIFACTS_DIR'] ?? '';
if (!artifactsDir) {
  process.stderr.write('ARTIFACTS_DIR env var is required\n');
  process.exit(1);
}

const repoRoot = process.cwd();
const symbols: SymbolEntry[] = [];
const languages: Record<string, number> = {};
let files = 0;
let truncated = false;

const sourceFiles: string[] = [];
walkSourceFiles(repoRoot, repoRoot, sourceFiles);

for (const relPath of sourceFiles) {
  const ext = extname(relPath).toLowerCase();
  const extractor = EXTRACTORS[ext];
  if (!extractor) continue; // unsupported language → filename inventory only (in inventory.json)
  files += 1;
  languages[ext] = (languages[ext] ?? 0) + 1;

  let content: string;
  try {
    content = readFileSync(resolve(repoRoot, relPath), 'utf8');
  } catch {
    continue; // unreadable file — skip gracefully
  }

  const lines = content.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const hit = extractor(lines[i]);
    if (!hit) continue;
    if (symbols.length >= MAX_SYMBOLS) {
      truncated = true;
      break;
    }
    symbols.push({ file: relPath, name: hit.name, kind: hit.kind, line: i + 1 });
  }
  if (truncated) break;
}

const index: SymbolsIndex = { files, languages, symbols, truncated };

// Atomic write: temp file + rename so a crash never leaves a corrupt half-file.
const kbDir = resolve(artifactsDir, 'kb');
mkdirSync(kbDir, { recursive: true });
const finalPath = resolve(kbDir, 'symbols.json');
const tmpPath = finalPath + '.tmp';
writeFileSync(tmpPath, JSON.stringify(index, null, 2));
renameSync(tmpPath, finalPath);

console.log(
  JSON.stringify({ files, symbol_count: symbols.length, truncated, languages }, null, 2),
);
