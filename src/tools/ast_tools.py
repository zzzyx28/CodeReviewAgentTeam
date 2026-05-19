"""
tree-sitter based AST analysis tools.

Provides multi-language code parsing, structure extraction, and pattern
detection for the four review agents. The AST layer does structural
analysis; the LLM layer does semantic reasoning on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FindingLocation:
    line_start: int
    line_end: int | None = None
    snippet: str = ""


@dataclass
class FindingResult:
    """Structural finding from AST analysis (before LLM reasoning)."""
    category: str  # security / performance / maintainability / api_design
    title: str
    description: str
    lines: list[FindingLocation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeStructure:
    """Extracted code structure for agent consumption."""
    language: str
    lines: list[str]
    total_lines: int
    functions: list[dict[str, Any]]
    classes: list[dict[str, Any]]
    imports: list[str]
    api_routes: list[dict[str, Any]]
    comments: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_MARKERS: list[tuple[str, list[str]]] = [
    ("python", [r"^\s*def\s+\w+\s*\(", r"^\s*import\s+\w+", r"^\s*from\s+\w+\s+import", r"^\s*class\s+\w+.*:"]),
    ("javascript", [r"const\s+\w+\s*=", r"let\s+\w+\s*=", r"function\s+\w+\s*\(", r"import\s+.*\s+from"]),
    ("typescript", [r":\s*(string|number|boolean|void)\b", r"interface\s+\w+", r"type\s+\w+\s*="]),
    ("java", [r"public\s+(static\s+)?(void|class|int|String)", r"import\s+java\.", r"@(Override|Entity|Service)"]),
    ("go", [r"func\s+\w+\s*\(.*\)\s*(\w+)?\s*\{", r"package\s+\w+", r"import\s*\([\s\S]*\)"]),
    ("sql", [r"SELECT\s+.+\s+FROM", r"CREATE\s+TABLE", r"INSERT\s+INTO"]),
]


def detect_language(code: str, hint: str | None = None) -> str:
    """Detect programming language from code heuristics."""
    if hint:
        return hint.lower()

    scores: dict[str, int] = {}
    for lang, patterns in _LANG_MARKERS:
        scores[lang] = sum(1 for p in patterns if re.search(p, code, re.MULTILINE | re.IGNORECASE))

    if max(scores.values(), default=0) == 0:
        return "unknown"

    # Break ties: prefer more specific languages
    best = max(scores, key=scores.get)
    return best


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

class CodeParser:
    """Multi-language code parser using tree-sitter backed by regex fallbacks.

    Attempts to use native tree-sitter parsers for supported languages,
    falling back to regex-based analysis for others.
    """

    SUPPORTED_TREE_SITTER: set[str] = {"python", "javascript", "typescript", "java", "go"}

    def __init__(self, language: str):
        self.language = language
        self.tree: Any = None
        self._ts_lang: Any = None

    @property
    def use_tree_sitter(self) -> bool:
        return self.language in self.SUPPORTED_TREE_SITTER

    def parse(self, code: str):
        lines = code.splitlines()
        if self.use_tree_sitter:
            try:
                self._parse_with_tree_sitter(code)
            except Exception:
                pass  # fall through to regex-based

    # -- tree-sitter path -------------------------------------------------------

    def _parse_with_tree_sitter(self, code: str):
        try:
            from tree_sitter_languages import get_language, get_parser  # type: ignore
        except ImportError:
            return

        try:
            lang = get_language(self.language)
            parser = get_parser(self.language)
            self.tree = parser.parse(code.encode("utf-8"))
            self._ts_lang = lang
        except Exception:
            self.tree = None

    # -- AST node traversal -----------------------------------------------------

    def _walk(self, node, callback, depth=0):
        if node is None:
            return
        callback(node, depth)
        if hasattr(node, "children"):
            for child in node.children:
                self._walk(child, callback, depth + 1)

    # -- Structure extraction ---------------------------------------------------

    def extract_structure(self, code: str) -> CodeStructure:
        lines = code.splitlines()
        self.parse(code)

        return CodeStructure(
            language=self.language,
            lines=lines,
            total_lines=len(lines),
            functions=self._find_functions(code),
            classes=self._find_classes(code),
            imports=self._find_imports(code),
            api_routes=self._find_api_routes(code),
            comments=self._find_comments(code),
        )

    # -- Function detection -----------------------------------------------------

    _FUNC_PATTERNS: dict[str, str] = {
        "python": r"^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->.*)?:",
        "javascript": r"(?:function\s+(\w+)\s*\(([^)]*)\)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>)",
        "typescript": r"(?:function\s+(\w+)\s*\(([^)]*)\)(?:\s*:\s*\w+)?|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)(?:\s*:\s*\w+)?\s*=>)",
        "go": r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(([^)]*)\)\s*(\w+)?\s*\{",
    }

    def _find_functions(self, code: str) -> list[dict[str, Any]]:
        funcs = []
        pattern = self._FUNC_PATTERNS.get(self.language, self._FUNC_PATTERNS["python"])
        for i, line in enumerate(code.splitlines(), 1):
            m = re.search(pattern, line)
            if m:
                groups = [g for g in m.groups() if g is not None]
                name = groups[0] if groups else "unknown"
                params = groups[1] if len(groups) > 1 else ""
                funcs.append({
                    "name": name,
                    "line": i,
                    "params": [p.strip() for p in params.split(",") if p.strip()],
                })
        return funcs

    # -- Class detection --------------------------------------------------------

    _CLASS_PATTERNS: dict[str, str] = {
        "python": r"^\s*class\s+(\w+)(?:\(([^)]*)\))?\s*:",
        "javascript": r"class\s+(\w+)(?:\s+extends\s+(\w+))?\s*\{",
        "typescript": r"class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+\w+)?\s*\{",
        "java": r"(?:public\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+\w+)?\s*\{",
    }

    def _find_classes(self, code: str) -> list[dict[str, Any]]:
        classes = []
        pattern = self._CLASS_PATTERNS.get(self.language, self._CLASS_PATTERNS["python"])
        for i, line in enumerate(code.splitlines(), 1):
            m = re.search(pattern, line)
            if m:
                classes.append({
                    "name": m.group(1),
                    "line": i,
                    "inherits": m.group(2) if m.lastindex and m.lastindex >= 2 else None,
                })
        return classes

    # -- Import detection -------------------------------------------------------

    _IMPORT_PATTERNS: dict[str, str] = {
        "python": r"^\s*(?:import\s+(\w+)|from\s+(\S+)\s+import\s+.+)",
        "javascript": r"^\s*import\s+.+\s+from\s+['\"]([^'\"]+)['\"]",
        "typescript": r"^\s*import\s+.+\s+from\s+['\"]([^'\"]+)['\"]",
        "java": r"^\s*import\s+([\w.]+)",
        "go": r'^\s*"([^"]+)"',
    }

    def _find_imports(self, code: str) -> list[str]:
        imports = set()
        pattern = self._IMPORT_PATTERNS.get(self.language, self._IMPORT_PATTERNS["python"])
        for line in code.splitlines():
            m = re.search(pattern, line)
            if m:
                imports.add(m.group(1) or m.group(2) or m.group(0))
        return sorted(imports)

    # -- API route detection ----------------------------------------------------

    def _find_api_routes(self, code: str) -> list[dict[str, Any]]:
        routes = []
        if self.language in ("python",):
            # Flask / FastAPI / Django
            for i, line in enumerate(code.splitlines(), 1):
                m = re.search(r"@(?:app|router|bp)\.(?:get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]", line)
                if m:
                    routes.append({"path": m.group(1), "line": i, "method": m.group(0).split(".")[-1].split("(")[0]})
                # FastAPI route decorators
                m2 = re.search(r"@(?:app|router)\.\w+\((?:['\"]([^'\"]+)['\"])", line)
                if m2 and not m:
                    routes.append({"path": m2.group(1), "line": i, "method": "unknown"})
        elif self.language in ("javascript", "typescript"):
            # Express
            for i, line in enumerate(code.splitlines(), 1):
                m = re.search(r"\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]", line)
                if m:
                    routes.append({"path": m.group(2), "line": i, "method": m.group(1)})
        elif self.language == "java":
            # Spring annotations
            for i, line in enumerate(code.splitlines(), 1):
                m = re.search(r"@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\s*\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]", line)
                if m:
                    routes.append({"path": m.group(1), "line": i, "method": "spring"})
        elif self.language == "go":
            # Gin / standard library
            for i, line in enumerate(code.splitlines(), 1):
                m = re.search(r"\.(?:GET|POST|PUT|DELETE|PATCH|HandleFunc)\s*\(\s*['\"]([^'\"]+)['\"]", line)
                if m:
                    routes.append({"path": m.group(1), "line": i, "method": "go"})

        return routes

    # -- Comment detection ------------------------------------------------------

    _COMMENT_PATTERNS: dict[str, tuple[str, str | None]] = {
        "python": (r"#.*", r'"{3}[\s\S]*?"{3}'),
        "javascript": (r"//.*", r"/\*[\s\S]*?\*/"),
        "typescript": (r"//.*", r"/\*[\s\S]*?\*/"),
        "java": (r"//.*", r"/\*[\s\S]*?\*/"),
        "go": (r"//.*", r"/\*[\s\S]*?\*/"),
    }

    def _find_comments(self, code: str) -> list[dict[str, Any]]:
        comments = []
        patterns = self._COMMENT_PATTERNS.get(self.language)
        if not patterns:
            return comments
        single, multi = patterns
        for i, line in enumerate(code.splitlines(), 1):
            m = re.search(single, line)
            if m:
                comments.append({"line": i, "text": m.group(0).strip()})
        if multi:
            for m in re.finditer(multi, code):
                line_no = code[:m.start()].count("\n") + 1
                comments.append({"line": line_no, "text": m.group(0)[:80]})
        return comments


# -- Convenience functions ----------------------------------------------------

def parse_code(code: str, language: str | None = None) -> CodeParser:
    lang = detect_language(code, language)
    parser = CodeParser(lang)
    parser.parse(code)
    return parser


def extract_structure(code: str, language: str | None = None) -> CodeStructure:
    lang = detect_language(code, language)
    parser = CodeParser(lang)
    return parser.extract_structure(code)


# ---------------------------------------------------------------------------
# Structural analysis: pattern-based pre-detection for each agent dimension
# ---------------------------------------------------------------------------

def analyze_security_patterns(structure: CodeStructure) -> list[FindingResult]:
    """Detect security-relevant patterns in code structure."""
    findings = []
    code = "\n".join(structure.lines)

    # Dangerous function calls
    dangerous_calls = {
        "exec": "Dynamic code execution with exec() — possible RCE",
        "eval": "Dynamic code evaluation with eval() — possible RCE",
        "os.system": "Shell command injection risk via os.system()",
        "subprocess.call": "Shell injection risk — prefer subprocess.run with shell=False",
        "subprocess.Popen": "Shell injection risk with shell=True",
        "popen": "Shell command execution — check for shell=True",
        ".execute(": "Raw SQL execution — verify parameterization",
        "raw": "Raw SQL execution — use parameterized queries",
        "innerHTML": "XSS risk — use textContent or sanitize",
        "dangerouslySetInnerHTML": "React XSS risk — use with extreme caution",
        "document.write": "XSS risk — avoid document.write()",
        "pickle.loads": "Insecure deserialization — pickle can execute arbitrary code",
        "yaml.load": "Unsafe YAML loading — use yaml.safe_load()",
        "md5(": "Weak hashing algorithm — use SHA-256 or bcrypt",
        "sha1(": "Weak hashing algorithm — use SHA-256 or stronger",
        "password": "Hardcoded credential pattern — check context",
        "secret_key": "Hardcoded secret key — move to environment variable",
        "api_key": "Hardcoded API key — use secret management",
        "token": "Potential hardcoded token — check context",
        "BEGIN RSA PRIVATE KEY": "Private key in source code — critical",
    }

    for i, line in enumerate(structure.lines, 1):
        for pattern, desc in dangerous_calls.items():
            if pattern.lower() in line.lower():
                findings.append(FindingResult(
                    category="security",
                    title=desc.split(" — ")[0] if " — " in desc else desc,
                    description=desc,
                    lines=[FindingLocation(line_start=i, snippet=line.strip())],
                ))

    # Missing input validation hint: check if routes/functions take input without validation
    for func in structure.functions:
        if len(func["params"]) > 0:
            # Check for validation patterns nearby
            start = max(0, func["line"] - 1)
            end = min(len(structure.lines), func["line"] + 10)
            context = "\n".join(structure.lines[start:end])
            if not re.search(r"(?:validate|sanitize|escape|check|assert\s+isinstance)", context, re.IGNORECASE):
                findings.append(FindingResult(
                    category="security",
                    title="Potential missing input validation",
                    description=f"Function '{func['name']}' accepts parameters but no validation pattern detected in nearby context",
                    lines=[FindingLocation(line_start=func["line"], snippet=structure.lines[func["line"] - 1].strip())],
                ))

    return findings


def analyze_performance_patterns(structure: CodeStructure) -> list[FindingResult]:
    """Detect performance-relevant patterns."""
    findings = []
    lines = structure.lines

    # N+1 query pattern: query inside a loop
    in_loop = False
    loop_start = 0
    for i, line in enumerate(lines, 1):
        if re.search(r"\b(for|while)\s+", line):
            in_loop = True
            loop_start = i
        elif in_loop and re.search(r"^\S", line) and i > loop_start + 1:
            # Simple heuristic: non-indented line after loop body starts
            pass

        if in_loop:
            if re.search(r"(?:\.query|\.execute|\.find|SELECT\s|\.filter|\.all\s*\()", line, re.IGNORECASE):
                findings.append(FindingResult(
                    category="performance",
                    title="Potential N+1 query: database call inside loop",
                    description=f"Line {i}: Database operation detected inside a loop starting at line {loop_start}",
                    lines=[
                        FindingLocation(line_start=loop_start, snippet=lines[loop_start - 1].strip()),
                        FindingLocation(line_start=i, snippet=line.strip()),
                    ],
                ))

    # Nested loops (O(n²))
    loop_depth = 0
    loop_lines: list[int] = []
    for i, line in enumerate(lines, 1):
        indent = len(line) - len(line.lstrip())
        if re.search(r"\b(for|while)\s+", line):
            loop_depth += 1
            loop_lines.append(i)
            if loop_depth >= 2:
                findings.append(FindingResult(
                    category="performance",
                    title=f"O(n²) complexity: nested loop at line {i}",
                    description=f"Nested loop depth={loop_depth}. Consider flattening or using hash-based lookup.",
                    lines=[FindingLocation(line_start=ln, snippet=lines[ln - 1].strip()) for ln in loop_lines[-2:]],
                ))

    # Large list comprehensions / allocations
    for i, line in enumerate(lines, 1):
        if re.search(r"range\s*\(\s*(?:len\s*\()?\s*\d{5,}", line):
            findings.append(FindingResult(
                category="performance",
                title="Potential large allocation",
                description=f"Line {i}: Large range/allocation detected",
                lines=[FindingLocation(line_start=i, snippet=line.strip())],
            ))

    return findings


def analyze_maintainability_patterns(structure: CodeStructure) -> list[FindingResult]:
    """Detect maintainability issues."""
    findings = []

    # Long functions
    func_ranges: list[dict] = []
    for func in structure.functions:
        func_start = func["line"]
        # Estimate function end by finding next function/class at same indent or EOF
        func_end = func_start
        for i in range(func_start, len(structure.lines)):
            line = structure.lines[i]
            if line and not line[0].isspace() and i >= func_start:
                if re.match(r"\s*(?:def|class|async)\s+", line):
                    func_end = i
                    break
            func_end = i + 1
        func_ranges.append({"name": func["name"], "start": func_start, "end": func_end, "length": func_end - func_start + 1})

    for fr in func_ranges:
        if fr["length"] > 30:
            findings.append(FindingResult(
                category="maintainability",
                title=f"Long function: '{fr['name']}' is {fr['length']} lines",
                description=f"Function '{fr['name']}' spans {fr['length']} lines. Consider refactoring into smaller functions (< 20-30 lines).",
                lines=[FindingLocation(line_start=fr["start"], snippet=structure.lines[fr["start"] - 1].strip())],
            ))

    # Too many parameters
    for func in structure.functions:
        if len(func["params"]) > 4:
            findings.append(FindingResult(
                category="maintainability",
                title=f"Too many parameters: '{func['name']}' has {len(func['params'])} params",
                description=f"Function '{func['name']}' has {len(func['params'])} parameters. Consider using a parameter object or builder pattern.",
                lines=[FindingLocation(line_start=func["line"], snippet=structure.lines[func["line"] - 1].strip())],
            ))

    # Deep nesting
    for i, line in enumerate(structure.lines, 1):
        indent = len(line) - len(line.lstrip())
        if indent > 16:  # >4 levels of 4-space indentation
            findings.append(FindingResult(
                category="maintainability",
                title=f"Deep nesting detected at line {i}",
                description=f"Line {i} has {indent} spaces of indentation (>4 levels). Consider early returns or extracting nested logic.",
                lines=[FindingLocation(line_start=i, snippet=line.strip())],
            ))

    return findings


def analyze_api_patterns(structure: CodeStructure) -> list[FindingResult]:
    """Detect API design issues."""
    findings = []

    if not structure.api_routes:
        return findings  # No API routes found — this agent won't be dispatched

    # Check for inconsistent naming patterns
    paths = [r["path"] for r in structure.api_routes]
    if paths:
        has_plural = any("/" + p.split("/")[-1] + "s" == p.split("/")[-1] + "s" for p in paths if "/" in p)

        # Check for mixed naming conventions
        snake = sum(1 for p in paths if "_" in p)
        kebab = sum(1 for p in paths if "-" in p)
        camel = sum(1 for p in paths if re.search(r"[a-z][A-Z]", p))
        if (snake > 0 and kebab > 0) or (snake > 0 and camel > 0) or (kebab > 0 and camel > 0):
            findings.append(FindingResult(
                category="api_design",
                title="Inconsistent API path naming convention",
                description=f"Found mixed naming conventions: {snake} snake_case, {kebab} kebab-case, {camel} camelCase paths",
                lines=[],
            ))

    # Missing pagination on list endpoints
    for route in structure.api_routes:
        if re.search(r"(?:list|all|search)", route["path"], re.IGNORECASE):
            findings.append(FindingResult(
                category="api_design",
                title=f"List endpoint may lack pagination: {route['path']}",
                description=f"Route '{route['path']}' appears to be a list endpoint. Consider adding pagination (limit/offset or page/size) to avoid large responses.",
                lines=[FindingLocation(line_start=route["line"])],
            ))

    return findings


def run_all_analyzers(structure: CodeStructure) -> dict[str, list[FindingResult]]:
    """Run all structural analyzers and return results keyed by agent category."""
    return {
        "security": analyze_security_patterns(structure),
        "performance": analyze_performance_patterns(structure),
        "maintainability": analyze_maintainability_patterns(structure),
        "api_design": analyze_api_patterns(structure),
    }
