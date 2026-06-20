"""
Code knowledge graph — parse source files into a queryable graph.

Parsers:
  Python   — stdlib ast module (accurate, zero deps)
  JS/TS    — regex-based (function decls, class decls, import/require)
  Go/Rust/Java — regex-based fallback

What gets extracted:
  Nodes  — files, classes, functions, methods
  Edges  — imports, defines, calls, inherits

After indexing, agents can:
  explore_code("how does auth work?")   → semantic search over symbol docstrings
  who_calls("verify_token")             → graph traversal: callers list
  graph_symbol("AuthMiddleware")        → full dependency subgraph
  list_files(repo_id)                   → all indexed files

Agents stop re-reading the same code files every session.
"""

from __future__ import annotations

import ast as pyast
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from samvit import db, embeddings

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 512 * 1024   # 512 KB per file
MAX_FILES      = 500          # per repo index run

# Magic byte prefixes for common binary formats
_BINARY_MAGIC_PREFIXES = [
    b"\x7fELF",             # ELF
    b"\xfe\xed\xfa\xce",    # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",    # Mach-O 64-bit
    b"\xca\xfe\xba\xbe",    # Java class
    b"\xcf\xfa\xed\xfe",    # Mach-O (another variant)
    b"\x1f\x8b",            # gzip
    b"\x42\x5a\x68",        # bzip2
    b"\x89PNG",             # PNG
    b"\xff\xd8\xff",        # JPEG
    b"\x47\x49\x46",        # GIF
    b"\x25\x50\x44\x46",    # PDF
    b"\x00\x00\x00\x00",    # common in object files
]
_SCAN_BYTES = 8192  # bytes to scan for null / magic


def _is_binary(path: Path) -> bool:
    """
    Heuristic check: read first _SCAN_BYTES bytes and look for null characters
    or known binary magic prefixes. Returns True if likely binary.
    """
    try:
        with path.open("rb") as f:
            header = f.read(_SCAN_BYTES)
    except OSError:
        return True  # can't read, treat as binary

    if not header:
        return False  # empty file is text-safe

    # Check magic byte prefixes
    for magic in _BINARY_MAGIC_PREFIXES:
        if header.startswith(magic):
            return True

    # Check for null bytes — strong indicator of binary content
    if b"\x00" in header:
        return True

    return False

# ── Language detection ────────────────────────────────────────────────────────

LANG_EXTENSIONS = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".jsx":  "javascript",
    ".tsx":  "typescript",
    ".go":   "go",
    ".rs":   "rust",
    ".java": "java",
    ".rb":   "ruby",
}

def detect_language(path: str) -> str | None:
    return LANG_EXTENSIONS.get(Path(path).suffix.lower())


# ── Node / Edge dataclasses ───────────────────────────────────────────────────

from dataclasses import dataclass, field

@dataclass
class CodeNode:
    node_type:  str
    name:       str
    qualified:  str
    file_path:  str
    line_start: int | None = None
    line_end:   int | None = None
    signature:  str | None = None
    docstring:  str | None = None

@dataclass
class CodeEdge:
    from_qualified: str
    to_name:        str
    edge_type:      str
    weight:         float = 1.0


# ── Python parser ─────────────────────────────────────────────────────────────

class PythonParser:
    """Full AST-based parser for Python source files."""

    def parse(self, source: str, file_path: str, repo_id: str) -> tuple[list[CodeNode], list[CodeEdge]]:
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        module_qualified = repo_id + "." + file_path.replace("/", ".").rstrip(".py")

        try:
            tree = pyast.parse(source)
        except SyntaxError as e:
            log.warning("Python parse error in %s: %s", file_path, e)
            return nodes, edges

        # File node
        nodes.append(CodeNode(
            node_type="file",
            name=Path(file_path).name,
            qualified=module_qualified,
            file_path=file_path,
            line_start=1,
            line_end=len(source.splitlines()),
        ))

        # Walk top-level and nested definitions
        for node in pyast.walk(tree):

            # Import statements → edges
            if isinstance(node, pyast.Import):
                for alias in node.names:
                    edges.append(CodeEdge(
                        from_qualified=module_qualified,
                        to_name=alias.name,
                        edge_type="imports",
                    ))

            elif isinstance(node, pyast.ImportFrom):
                module = node.module or ""
                edges.append(CodeEdge(
                    from_qualified=module_qualified,
                    to_name=module,
                    edge_type="imports",
                ))

            # Function definitions
            elif isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef)):
                # Build parent qualified name
                parent_q = _find_parent_qualified(tree, node, module_qualified)
                qualified = parent_q + "." + node.name
                sig = _build_signature(node)
                docstring = pyast.get_docstring(node)

                nodes.append(CodeNode(
                    node_type="function",
                    name=node.name,
                    qualified=qualified,
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno,
                    signature=sig,
                    docstring=docstring,
                ))
                edges.append(CodeEdge(
                    from_qualified=parent_q,
                    to_name=qualified,
                    edge_type="defines",
                ))

                # Extract calls within this function
                for child in pyast.walk(node):
                    if isinstance(child, pyast.Call):
                        call_name = _call_name(child)
                        if call_name:
                            edges.append(CodeEdge(
                                from_qualified=qualified,
                                to_name=call_name,
                                edge_type="calls",
                            ))

            # Class definitions
            elif isinstance(node, pyast.ClassDef):
                qualified = module_qualified + "." + node.name
                docstring = pyast.get_docstring(node)

                # Base classes → inherits edges
                for base in node.bases:
                    base_name = _expr_name(base)
                    if base_name:
                        edges.append(CodeEdge(
                            from_qualified=qualified,
                            to_name=base_name,
                            edge_type="inherits",
                        ))

                nodes.append(CodeNode(
                    node_type="class",
                    name=node.name,
                    qualified=qualified,
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno,
                    docstring=docstring,
                ))
                edges.append(CodeEdge(
                    from_qualified=module_qualified,
                    to_name=qualified,
                    edge_type="defines",
                ))

        return nodes, edges


def _find_parent_qualified(tree: pyast.AST, target: pyast.AST, module_q: str) -> str:
    """Walk the AST to find the qualified name of the parent scope."""
    for node in pyast.walk(tree):
        if isinstance(node, pyast.ClassDef):
            for child in pyast.iter_child_nodes(node):
                if child is target:
                    return module_q + "." + node.name
    return module_q


def _build_signature(node: pyast.FunctionDef | pyast.AsyncFunctionDef) -> str:
    args = []
    for arg in node.args.args:
        args.append(arg.arg)
    return f"{node.name}({', '.join(args)})"


def _call_name(node: pyast.Call) -> str | None:
    if isinstance(node.func, pyast.Name):
        return node.func.id
    if isinstance(node.func, pyast.Attribute):
        return node.func.attr
    return None


def _expr_name(node: pyast.expr) -> str | None:
    if isinstance(node, pyast.Name):
        return node.id
    if isinstance(node, pyast.Attribute):
        return node.attr
    return None


# ── Regex fallback parser (JS/TS/Go/Rust/Java) ────────────────────────────────

class RegexParser:
    """Regex-based parser for non-Python languages."""

    PATTERNS = {
        "function": [
            # JS/TS: function foo(, const foo = (, async foo(
            re.compile(r"(?:^|\s)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.M),
            re.compile(r"(?:^|\s)(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M),
            # Go: func FunctionName(
            re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", re.M),
            # Rust: fn function_name(
            re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]", re.M),
            # Java: public void method(
            re.compile(r"(?:public|private|protected|static)(?:\s+\w+)*\s+(\w+)\s*\(", re.M),
        ],
        "class": [
            re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.M),
            re.compile(r"^(?:pub\s+)?struct\s+(\w+)", re.M),
            re.compile(r"^type\s+(\w+)\s+struct\b", re.M),
        ],
        "import": [
            re.compile(r'^import\s+(?:[\w{},\s*]+\s+from\s+)?["\']([^"\']+)["\']', re.M),
            re.compile(r'^(?:const|var|let)\s+\w+\s*=\s*require\(["\']([^"\']+)["\']\)', re.M),
            re.compile(r'^import\s+"([^"]+)"', re.M),  # Go
            re.compile(r'^use\s+([\w:]+)', re.M),       # Rust
        ],
    }

    def parse(self, source: str, file_path: str, repo_id: str) -> tuple[list[CodeNode], list[CodeEdge]]:
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        lang = detect_language(file_path) or "unknown"
        file_q = repo_id + "." + Path(file_path).stem

        nodes.append(CodeNode(
            node_type="file",
            name=Path(file_path).name,
            qualified=file_q,
            file_path=file_path,
        ))

        lines = source.splitlines()

        for pat in self.PATTERNS["function"]:
            for m in pat.finditer(source):
                name = m.group(1)
                if len(name) > 1:   # skip single-char matches
                    line = source[:m.start()].count("\n") + 1
                    qualified = file_q + "." + name
                    nodes.append(CodeNode(
                        node_type="function",
                        name=name,
                        qualified=qualified,
                        file_path=file_path,
                        line_start=line,
                    ))
                    edges.append(CodeEdge(
                        from_qualified=file_q,
                        to_name=qualified,
                        edge_type="defines",
                    ))

        for pat in self.PATTERNS["class"]:
            for m in pat.finditer(source):
                name = m.group(1)
                line = source[:m.start()].count("\n") + 1
                qualified = file_q + "." + name
                nodes.append(CodeNode(
                    node_type="class",
                    name=name,
                    qualified=qualified,
                    file_path=file_path,
                    line_start=line,
                ))
                edges.append(CodeEdge(
                    from_qualified=file_q,
                    to_name=qualified,
                    edge_type="defines",
                ))

        for pat in self.PATTERNS["import"]:
            for m in pat.finditer(source):
                edges.append(CodeEdge(
                    from_qualified=file_q,
                    to_name=m.group(1),
                    edge_type="imports",
                ))

        return nodes, edges


# ── Index a repo ──────────────────────────────────────────────────────────────

_python_parser  = PythonParser()
_regex_parser   = RegexParser()

def _get_parser(file_path: str):
    lang = detect_language(file_path)
    if lang == "python":
        return _python_parser
    if lang in ("javascript", "typescript", "go", "rust", "java", "ruby"):
        return _regex_parser
    return None


def _allowed_code_roots() -> list[Path]:
    configured = os.environ.get("SAMVIT_CODE_ROOTS", "/workspace")
    return [
        Path(value).expanduser().resolve()
        for value in configured.split(os.pathsep)
        if value.strip()
    ]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


async def index_repo(
    agent: dict,
    root_path: str,
    repo_id: str,
    extensions: list[str] | None = None,
) -> dict:
    """
    Walk root_path, parse all source files, build and store the code graph.
    Returns summary stats.
    """
    root = Path(root_path).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Path does not exist: {root_path}")
    allowed_roots = _allowed_code_roots()
    if not any(_is_within(root, allowed) for allowed in allowed_roots):
        roots = ", ".join(str(path) for path in allowed_roots)
        raise ValueError(
            f"Path is outside SAMVIT_CODE_ROOTS. Allowed roots: {roots}"
        )

    allowed_exts = set(extensions or list(LANG_EXTENSIONS.keys()))
    files = [
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in allowed_exts
        and not any(part.startswith(".") for part in p.parts)
        and "node_modules" not in p.parts
        and "__pycache__" not in p.parts
    ][:MAX_FILES]

    all_nodes: list[CodeNode]   = []
    all_edges: list[CodeEdge]   = []
    skipped = 0

    for path in files:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                log.debug("Skipping large file: %s", path)
                skipped += 1
                continue
            if _is_binary(path):
                log.debug("Skipping binary file: %s", path)
                skipped += 1
                continue
            source = path.read_text(errors="replace")
            rel    = str(path.relative_to(root))
            parser = _get_parser(str(path))
            if parser is None:
                continue
            nodes, edges = parser.parse(source, rel, repo_id)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        except Exception as exc:
            log.warning("Error parsing %s: %s", path, exc)
            skipped += 1

    if not all_nodes:
        raise ValueError(f"No parseable source files found under {root_path}")

    # Deduplicate nodes by qualified name
    seen_q: set[str] = set()
    unique_nodes = []
    for n in all_nodes:
        if n.qualified not in seen_q:
            seen_q.add(n.qualified)
            unique_nodes.append(n)

    await _persist_graph(agent, repo_id, root_path, unique_nodes, all_edges)

    log.info("Indexed repo=%s  files=%d  nodes=%d  edges=%d  skipped=%d",
             repo_id, len(files), len(unique_nodes), len(all_edges), skipped)

    return {
        "repo_id":    repo_id,
        "files":      len(files),
        "nodes":      len(unique_nodes),
        "edges":      len(all_edges),
        "skipped":    skipped,
    }


async def _persist_graph(
    agent: dict,
    repo_id: str,
    root_path: str,
    nodes: list[CodeNode],
    edges: list[CodeEdge],
) -> None:
    """Write nodes and edges to the DB. Upsert on qualified name."""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            # Upsert repo registry
            lang = "mixed"
            if nodes:
                langs = {detect_language(n.file_path) for n in nodes if detect_language(n.file_path)}
                lang = next(iter(langs)) if len(langs) == 1 else "mixed"

            await conn.execute(
                """
                INSERT INTO code_repos (repo_id, agent_id, root_path, language,
                                        file_count, node_count, edge_count,
                                        workspace_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (workspace_id, repo_id) DO UPDATE
                   SET agent_id   = EXCLUDED.agent_id,
                       root_path  = EXCLUDED.root_path,
                       file_count = EXCLUDED.file_count,
                       node_count = EXCLUDED.node_count,
                       edge_count = EXCLUDED.edge_count,
                       indexed_at = now()
                """,
                repo_id, agent["id"], root_path, lang,
                len({n.file_path for n in nodes}),
                len(nodes), len(edges), agent["workspace_id"],
            )

            # Clear old nodes for this repo (re-index)
            await conn.execute(
                "DELETE FROM code_nodes WHERE repo_id = $1 AND workspace_id = $2",
                repo_id, agent["workspace_id"],
            )

            # Embed and insert nodes
            node_id_map: dict[str, str] = {}
            for node in nodes:
                embed_text = " ".join(filter(None, [
                    node.name, node.node_type, node.signature, node.docstring
                ]))[:512]
                try:
                    vec = await embeddings.embed(embed_text)
                    vector = embeddings.fmt_vector(vec)
                except Exception as exc:
                    log.warning("Embedding failed for node %s (%s): %s",
                                node.qualified, node.node_type, exc)
                    vector = None

                node_uuid = str(uuid.uuid4())
                node_id_map[node.qualified] = node_uuid

                await conn.execute(
                    """
                    INSERT INTO code_nodes
                        (id, repo_id, node_type, name, qualified, file_path,
                         line_start, line_end, signature, docstring, language,
                         embedding, workspace_id)
                    VALUES ($1, $2, $3::node_type, $4, $5, $6, $7, $8, $9, $10, $11, $12::vector, $13)
                    ON CONFLICT (workspace_id, repo_id, qualified) DO UPDATE
                       SET line_start = EXCLUDED.line_start,
                           line_end   = EXCLUDED.line_end,
                           signature  = EXCLUDED.signature,
                           docstring  = EXCLUDED.docstring,
                           embedding  = EXCLUDED.embedding,
                           indexed_at = now()
                    """,
                    node_uuid, repo_id, node.node_type, node.name, node.qualified,
                    node.file_path, node.line_start, node.line_end,
                    node.signature, node.docstring,
                    detect_language(node.file_path) or "unknown",
                    vector, agent["workspace_id"],
                )

            # Insert edges, resolving qualified names to UUIDs
            for edge in edges:
                from_id = node_id_map.get(edge.from_qualified)
                to_id   = node_id_map.get(edge.to_name)
                if from_id is None:
                    continue
                await conn.execute(
                    """
                    INSERT INTO code_edges
                        (repo_id, from_node, to_node, to_name, edge_type, weight, workspace_id)
                    VALUES ($1, $2, $3, $4, $5::edge_type, $6, $7)
                    """,
                    repo_id, from_id, to_id, edge.to_name, edge.edge_type, edge.weight,
                    agent["workspace_id"],
                )


# ── Query functions ───────────────────────────────────────────────────────────

async def explore_code(
    agent: dict,
    repo_id: str,
    query: str,
    limit: int = 10,
    node_types: list[str] | None = None,
) -> dict:
    """Semantic search over symbol names, signatures, and docstrings."""
    if not query.strip():
        raise ValueError("query must not be empty")
    limit = min(limit, 50)
    vector = embeddings.fmt_vector(await embeddings.embed(query))

    async with db.pool().acquire() as conn:
        type_clause = ""
        params: list[Any] = [vector, repo_id, limit, agent["workspace_id"]]
        if node_types:
            params.append([t for t in node_types])
            type_clause = f"AND node_type = ANY(${len(params)}::node_type[])"

        rows = await conn.fetch(
            f"""
            SELECT
                id, node_type, name, qualified, file_path,
                line_start, line_end, signature, docstring,
                1 - (embedding <=> $1::vector) AS score
              FROM code_nodes
             WHERE repo_id = $2
               AND workspace_id = $4
               AND embedding IS NOT NULL
               {type_clause}
             ORDER BY embedding <=> $1::vector
             LIMIT $3
            """,
            *params,
        )

    return {
        "results": [
            {
                "name":       r["name"],
                "type":       r["node_type"],
                "qualified":  r["qualified"],
                "file":       r["file_path"],
                "line":       r["line_start"],
                "signature":  r["signature"],
                "docstring":  r["docstring"],
                "score":      round(float(r["score"]), 4),
            }
            for r in rows
        ]
    }


async def who_calls(agent: dict, repo_id: str, function_name: str) -> dict:
    """Return all functions/methods that call the named function."""
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                cn.name, cn.qualified, cn.file_path, cn.line_start,
                cn.node_type, cn.signature,
                ce.to_name
              FROM code_edges ce
              JOIN code_nodes cn ON cn.id = ce.from_node
             WHERE ce.repo_id = $1
               AND cn.workspace_id = $3
               AND ce.edge_type = 'calls'
               AND ce.to_name = $2
             ORDER BY cn.file_path, cn.line_start
            """,
            repo_id, function_name, agent["workspace_id"],
        )

    return {
        "function":  function_name,
        "callers": [
            {
                "name":      r["name"],
                "qualified": r["qualified"],
                "file":      r["file_path"],
                "line":      r["line_start"],
                "signature": r["signature"],
            }
            for r in rows
        ],
    }


async def graph_symbol(agent: dict, repo_id: str, symbol_name: str, depth: int = 2) -> dict:
    """
    Return the dependency subgraph around a symbol (BFS up to `depth` hops).
    Useful for understanding what a function/class depends on and what uses it.
    """
    depth = min(depth, 4)  # cap at 4 to avoid enormous graphs

    async with db.pool().acquire() as conn:
        # Find the root node
        root = await conn.fetchrow(
            """
            SELECT id, name, qualified, file_path, node_type, signature
              FROM code_nodes
             WHERE repo_id = $1 AND workspace_id = $3
               AND (name = $2 OR qualified LIKE '%' || $2)
             LIMIT 1
            """,
            repo_id, symbol_name, agent["workspace_id"],
        )
        if not root:
            return {"error": f"Symbol '{symbol_name}' not found in repo '{repo_id}'"}

        # BFS: collect nodes within `depth` hops
        visited: set[str] = {str(root["id"])}
        frontier: list[str] = [str(root["id"])]
        all_nodes: list[dict] = [{
            "id": str(root["id"]), "name": root["name"],
            "type": root["node_type"], "file": root["file_path"],
            "signature": root["signature"],
        }]
        all_edges: list[dict] = []

        for _ in range(depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            for node_id in frontier:
                # Outbound edges (what this symbol calls / imports)
                edges = await conn.fetch(
                    """
                    SELECT ce.edge_type, ce.to_name,
                           cn.id AS to_id, cn.name AS to_sym,
                           cn.node_type AS to_type, cn.file_path AS to_file
                      FROM code_edges ce
                      LEFT JOIN code_nodes cn ON cn.id = ce.to_node
                     WHERE ce.from_node = $1
                    """,
                    node_id,
                )
                for e in edges:
                    all_edges.append({
                        "from": node_id,
                        "to":   str(e["to_id"]) if e["to_id"] else None,
                        "to_name": e["to_name"],
                        "type": e["edge_type"],
                    })
                    if e["to_id"] and str(e["to_id"]) not in visited:
                        visited.add(str(e["to_id"]))
                        next_frontier.append(str(e["to_id"]))
                        all_nodes.append({
                            "id":   str(e["to_id"]),
                            "name": e["to_sym"],
                            "type": e["to_type"],
                            "file": e["to_file"],
                        })

                # Inbound edges (what calls / uses this symbol)
                inbound_edges = await conn.fetch(
                    """
                    SELECT ce.edge_type,
                           cn.id AS from_id, cn.name AS from_sym,
                           cn.node_type AS from_type, cn.file_path AS from_file
                      FROM code_edges ce
                      LEFT JOIN code_nodes cn ON cn.id = ce.from_node
                     WHERE ce.to_node = $1
                    """,
                    node_id,
                )
                for e in inbound_edges:
                    all_edges.append({
                        "from": str(e["from_id"]) if e["from_id"] else None,
                        "to":   node_id,
                        "type": e["edge_type"],
                    })
                    if e["from_id"] and str(e["from_id"]) not in visited:
                        visited.add(str(e["from_id"]))
                        next_frontier.append(str(e["from_id"]))
                        all_nodes.append({
                            "id":   str(e["from_id"]),
                            "name": e["from_sym"],
                            "type": e["from_type"],
                            "file": e["from_file"],
                        })
            frontier = next_frontier

    return {
        "root":  symbol_name,
        "nodes": all_nodes,
        "edges": all_edges,
        "depth": depth,
    }


async def list_repos(agent: dict) -> dict:
    """List all indexed repos."""
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT repo_id, root_path, language, file_count, node_count, edge_count, indexed_at FROM code_repos WHERE workspace_id = $1 ORDER BY indexed_at DESC",
            agent["workspace_id"],
        )
    return {"repos": [dict(r) for r in rows]}
