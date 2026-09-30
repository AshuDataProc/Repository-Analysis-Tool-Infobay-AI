import os
import sys
import json
import csv
import argparse
import hashlib
import re
import subprocess
import time
import logging
import multiprocessing
import threading
import datetime
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional, Any

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from github import Github, RateLimitExceededException, GithubException
    HAS_PYGITHUB = True
except ImportError:
    HAS_PYGITHUB = False

    class GithubException(Exception):
        pass

    class RateLimitExceededException(GithubException):
        pass

    class Github:
        pass

CSV_LOCK = threading.Lock()

try:
    import tiktoken
    HAS_TIKTOKEN = True
    ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    HAS_TIKTOKEN = False
    ENCODING = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 5 * 1024 * 1024
SIMILARITY_THRESHOLD = 0.85
MIN_LOC = 3
MIN_TOKENS = 20
MAX_SIMILARITY_PAIRS = 50_000
MAX_BUCKET_COMPARE = 300

GIT_LOG_TIMEOUT = 120
CODE_AGE_THRESHOLDS_YEARS = [1, 2]
REQUIRED_SCC_VERSION = "3.7.0"

# Stage 4: Composite rating criteria
RATING_CRITERIA = {
    "commits":           {"min": 75,   "preferred": 200,    "weight": 0.20},
    "dev_span_months":   {"min": 6,    "preferred": 24,     "weight": 0.15},
    "contributors":      {"min": 2,    "preferred": 5,      "weight": 0.15},
    "loc":               {"min": 5_000, "preferred": 50_000, "weight": 0.20},
    "has_tests":         {"weight": 0.10},
    "has_ci":            {"weight": 0.10},
    "test_file_count":   {"min": 20,   "preferred": 200,    "weight": 0.05},
    "source_file_ratio": {"min": 0.40, "preferred": 0.60,   "weight": 0.05},
}

# Stage 4: Framework detection — manifest → framework list
FRAMEWORK_MANIFEST_PARSERS = {
    "package.json": [
        "react", "vue", "angular", "svelte",
        "next", "nuxt", "gatsby", "remix", "astro",
        "express", "fastify", "nestjs", "koa", "hapi",
    ],
    "requirements.txt": [
        "django", "flask", "fastapi", "tornado",
        "starlette", "aiohttp", "pyramid", "falcon", "bottle"
    ],
    "Pipfile": [
        "django", "flask", "fastapi"
    ],
    "pom.xml": [
        "spring", "springboot", "quarkus", "micronaut"
    ],
    "build.gradle": [
        "spring", "springboot", "quarkus", "micronaut"
    ],
    "Cargo.toml": [
        "actix", "axum", "warp", "rocket"
    ],
    "go.mod": [
        "gin", "echo", "fiber", "chi", "beego"
    ],
    "Gemfile": [
        "rails", "sinatra", "hanami"
    ],
    "composer.json": [
        "laravel", "symfony", "codeigniter", "yii"
    ],
    "mix.exs": [
        "phoenix"
    ],
    "stack.yaml": [
        "yesod", "scotty"
    ],
}

IMPORT_FRAMEWORK_MAP = {
    "py": {
        "django": "Django",
        "flask": "Flask",
        "fastapi": "FastAPI",
        "tornado": "Tornado",
        "starlette": "Starlette",
        "aiohttp": "aiohttp",
        "pyramid": "Pyramid",
        "falcon": "Falcon",
        "bottle": "Bottle"
    },
    "js": {
        "express": "Express",
        "next": "Next.js",
        "react": "React",
        "vue": "Vue",
        "angular": "Angular",
        "fastify": "Fastify",
        "nestjs": "NestJS",
        "koa": "Koa",
        "hapi": "Hapi"
    },
    "jsx": {"react": "React"},
    "tsx": {
        "react": "React",
        "next": "Next.js"
    }
}

FRONTEND_FRAMEWORKS = {
    "react", "vue", "angular", "svelte",
    "next", "nuxt", "gatsby", "remix", "astro"
}

BACKEND_FRAMEWORKS = {
    "express", "fastify", "nestjs", "koa", "hapi",
    "django", "flask", "fastapi", "tornado", "starlette", "aiohttp",
    "pyramid", "falcon", "bottle",
    "spring", "springboot", "quarkus", "micronaut",
    "actix", "axum", "warp", "rocket",
    "gin", "echo", "fiber", "chi", "beego",
    "rails", "sinatra", "hanami",
    "laravel", "symfony", "codeigniter", "yii",
    "phoenix", "yesod", "scotty"
}

EXT_TO_LANG_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.jsx': 'JSX',
    '.ts': 'TypeScript', '.tsx': 'TSX', '.go': 'Go',
    '.java': 'Java', '.c': 'C', '.cpp': 'C++', '.cc': 'C++',
    '.h': 'C/C++ Header', '.hpp': 'C/C++ Header',
    '.cs': 'C#', '.rb': 'Ruby', '.php': 'PHP', '.rs': 'Rust',
    '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.sass': 'Sass',
    '.less': 'LESS', '.sql': 'SQL', '.sh': 'Bourne Shell',
    '.ps1': 'PowerShell', '.bat': 'DOS Batch', '.json': 'JSON',
    '.xml': 'XML', '.md': 'Markdown', '.yaml': 'YAML', '.yml': 'YAML',
    '.swift': 'Swift', '.kt': 'Kotlin', '.scala': 'Scala', '.lua': 'Lua'
}

FORMAL_FRAMEWORK_NAMES = {
    "react": "React", "next": "Next.js", "vue": "Vue", "angular": "Angular",
    "svelte": "Svelte", "express": "Express", "fastapi": "FastAPI", "django": "Django",
    "flask": "Flask", "spring": "Spring Boot", "springboot": "Spring Boot",
    "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mongodb": "MongoDB",
    "redis": "Redis", "mysql": "MySQL", "sqlite": "SQLite", "firebase": "Firebase",
    "jest": "Jest", "vitest": "Vitest", "cypress": "Cypress", "playwright": "Playwright"
}

LANG_MERGE_MAP = {
    "C/C++ Header": "C++",
    "Jupyter Notebook": "Python",
    "JSX": "JavaScript",
    "TSX": "TypeScript"
}

FRONTEND_LANGUAGES = {
    "HTML", "CSS", "SCSS", "Sass", "Less",
    "JavaScript", "TypeScript",
    "JSX", "TSX",
    "Vue", "Svelte"
}

BACKEND_LANGUAGES = {
    "Python", "Java", "Go", "Rust", "Ruby", "PHP",
    "C++", "C", "C#", "Scala", "Kotlin",
    "Elixir", "Haskell", "Perl", "Clojure",
    "SQL", "Swift", "Dart"
}

NON_CORE_FORMATS = {
    "JSON", "XML", "YAML", "CSV", "TOML", "INI", "Protocol Buffers", "Graphviz (DOT)",
    "SVG", "SQL", "Properties", "Excel", "Parquet", "HCL", "Starlark",
    "Markdown", "Text", "Plain Text", "reStructuredText", "AsciiDoc", "Doxygen", "Org", "TeX", "Org Mode",
    "Gradle", "ProGuard", "Windows Resource File", "Maven POM", "Protocol Buffers",
    "PowerShell", "Bourne Shell", "Bourne Again Shell", "Fish Shell", "DOS Batch", "make",
    "CMake", "Dockerfile", "Vagrantfile", "Procfile", "Rakefile", "Gemfile", "Pipfile",
    "BitBake", "Meson", "Kconfig", "QMake", "Bazel",
    "LESS", "SCSS", "Sass", "Stylus", "PostCSS"
}

BOILERPLATE_FILES = {
    "reportwebvitals.js", "setuptests.js", "app.test.js", "logo.svg",
    "favicon.ico", "service-worker.js", "manifest.json", "robots.txt",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "manage.py", "asgi.py", "wsgi.py", "__init__.py",
    "mvnw", "mvnw.cmd", "gradlew", "gradlew.bat",
    "settings.gradle", "gradle-wrapper.properties",
    "artisan", "server.php",
    "bundle", "rails", "rake", "setup"
}

VENDORED_NAMES = {
    "jquery", "bootstrap", "angular", "react.production", "react-dom.production",
    "vue.global", "vue.runtime", "lodash", "underscore", "backbone",
    "d3", "three", "moment", "chart", "highcharts", "leaflet",
    "popper", "tether", "select2", "datatables", "tinymce", "ckeditor",
    "ace-editor", "codemirror", "quill", "summernote", "sweetalert",
    "toastr", "animate", "font-awesome", "normalize", "reset",
    "polyfill", "modernizr", "respond", "html5shiv", "pace",
    "hammer", "howler", "socket.io", "fabric", "konva",
    "excalidraw", "wiris", "mathjax", "katex", "zoom-meeting",
    "dropbox-sdk", "aws-sdk", "firebase-app", "firebase-auth",
    "multislider",
}

VENDORED_DIRS = {
    "/vendor/", "/vendors/", "/third_party/", "/third-party/",
    "/bower_components/", "/jspm_packages/", "/web_modules/",
    "/custom_node_modules/", "/extern/", "/external/", "/lib/vendor/",
    "/.bundle/", "/cache/", "/deps/",
    "/site-packages/", "/dist-packages/", "/_vendor/",
}

BUNDLE_PATTERNS = {"bundle.js", "chunk.js", "vendor.js", "vendors.js", "runtime.js", "main.chunk.js", "vendors~main"}

# Stage 4: Infrastructure & Service Detection
INFRASTRUCTURE_INDICATORS = {
    "databases": [
        "postgres", "postgresql", "mysql", "mongodb", "redis", "sqlite", "elasticsearch",
        "dynamodb", "mariadb", "oracle", "mssql", "cassandra",
        "neo4j", "couchdb", "prisma", "sequelize", "mongoose", "psycopg2", "psycopg",
        "sqlalchemy", "typeorm", "knex", "tortoise-orm", "dj-database-url",
        "supabase", "pocketbase", "surrealdb", "cockroachdb", "fauna", "influxdb",
        "snowflake", "clickhouse", "databricks", "firebird", "trino", "presto"
    ],
    "deployment": [
        "docker", "kubernetes", "aws", "azure", "gcp", "heroku", "vercel",
        "netlify", "terraform", "ansible", "jenkins", "travis", "circleci",
        "github actions", "dockerfile", "docker-compose"
    ],
    "apis": [
        "stripe", "twilio", "sendgrid", "openai", "firebase", "auth0",
        "slack", "aws-sdk", "google-cloud", "mailgun", "algolia", "pusher"
    ]
}

INFRA_MANIFESTS_TO_CHECK = ["package.json", "requirements.txt", "Pipfile", "pom.xml","build.gradle", "Cargo.toml", "go.mod", "Gemfile", "composer.json", "docker-compose.yml"
]
INFRA_CONFIG_FILES_TO_CHECK = [".env", "settings.py", "config.js","web.config", "appsettings.json", "config.php", "configuration.yaml"]

INFRA_DB_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".rdb"}

INFRA_CODE_EXTENSIONS = {".py", ".js", ".ts", ".php", ".go", ".java", ".rb", ".cs", ".sql"}

INFRA_CONN_MAP = {
    "postgres://": "postgres",
    "postgresql://": "postgres",
    "mysql://": "mysql",
    "mongodb://": "mongodb",
    "redis://": "redis"
}

INFRA_FILE_MAP = {
    "dockerfile": ("deployment", "docker"),
    "docker-compose": ("deployment", "docker-compose"),
    "jenkinsfile": ("deployment", "jenkins"),
    ".travis.yml": ("deployment", "travis"),
    "circle.yml": ("deployment", "circleci"),
    "terraform": ("deployment", "terraform"),
    ".github/workflows": ("deployment", "github actions")
}

INFRA_CANONICAL_MAP = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "psycopg2": "PostgreSQL", "psycopg": "PostgreSQL",
    "mongodb": "MongoDB", "mongoose": "MongoDB",
    "mysql": "MySQL", "mariadb": "MariaDB",
    "sqlite": "SQLite", "redis": "Redis", "elasticsearch": "Elasticsearch",
    "dynamodb": "DynamoDB", "cassandra": "Cassandra", "oracle": "Oracle",
    "mssql": "SQL Server", "neo4j": "Neo4j", "couchdb": "CouchDB",
    "firebase": "Firebase", "firestore": "Firebase",
    "prisma": "Prisma (ORM)", "sequelize": "Sequelize (ORM)", "sqlalchemy": "SQLAlchemy (ORM)",
    "typeorm": "TypeORM", "knex": "Knex.js", "docker": "Docker", "kubernetes": "Kubernetes",
    "github actions": "GitHub Actions", "docker-compose": "Docker Compose"
}

DOC_SETUP_KEYWORDS = ['setup', 'install', 'getting started', 'running', 'requirements']

COVERAGE_FILES = ['lcov.info', 'coverage.xml', 'cobertura.xml', '.coverage', 'coverage/index.html']

# Stage 4: Testing & Quality Patterns
TEST_CASE_PATTERNS = {
    "python": re.compile(r"def\s+test_"),
    "javascript": re.compile(r"(it|test)\s*\("),
    "typescript": re.compile(r"(it|test)\s*\("),
    "java": re.compile(r"@Test"),
    "go": re.compile(r"func\s+Test"),
    "php": re.compile(r"public\s+function\s+test"),
    "ruby": re.compile(r"test\s+['\"]"),
}

# Stage 5: Secret / credential scanning patterns
SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("AWS Access Key",       re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key",       re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]")),
    ("GitHub Token",         re.compile(r"ghp_[0-9a-zA-Z]{36}")),
    ("GitHub OAuth",         re.compile(r"gho_[0-9a-zA-Z]{36}")),
    ("GitHub App Token",     re.compile(r"ghu_[0-9a-zA-Z]{36}")),
    ("Slack Token",          re.compile(r"xox[baprs]-[0-9a-zA-Z\-]{10,48}")),
    ("Google API Key",       re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Stripe Secret Key",    re.compile(r"sk_live_[0-9a-zA-Z]{24,}")),
    ("Stripe Public Key",    re.compile(r"pk_live_[0-9a-zA-Z]{24,}")),
    ("Private Key Block",    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Generic Password",     re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}['\"]")),
    ("Generic API Key",      re.compile(r"(?i)(api_key|apikey|api-key)\s*[=:]\s*['\"][^'\"]{8,}['\"]")),
    ("Generic Secret",       re.compile(r"(?i)(secret|token)\s*[=:]\s*['\"][^'\"]{8,}['\"]")),
    ("DB Connection String", re.compile(r"(?i)(mongodb|postgres|mysql|redis)://[^\s'\"]{10,}")),
    ("Bearer Token",         re.compile(r"(?i)bearer\s+[0-9a-zA-Z\-._~+/]{20,}")),
]

SECRET_SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".php",
    ".java", ".kt", ".cs", ".env", ".sh", ".bash", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf", ".json",
}

SECRET_SCAN_SKIP = {".lock", ".sum", ".mod"}

# Language name mapping (extension → display name)
EXT_TO_LANGUAGE: Dict[str, str] = {
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".c++": "C++", ".hxx": "C++", ".h++": "C++", ".C": "C++", ".H": "C++",
    ".py": "Python", ".pyw": "Python", ".pyx": "Python",
    ".pxd": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy", ".gradle": "Groovy",
    ".clj": "Clojure", ".cljs": "Clojure", ".cljc": "Clojure", ".edn": "Clojure",
    ".cs": "C#",
    ".fs": "F#", ".fsx": "F#", ".fsi": "F#",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".zig": "Zig",
    ".nim": "Nim",
    ".v": "V",
    ".d": "D",
    ".ada": "Ada", ".adb": "Ada", ".ads": "Ada",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".fish": "Shell", ".ksh": "Shell", ".csh": "Shell", ".tcsh": "Shell",
    ".bat": "Batch", ".cmd": "Batch",
    ".ps1": "PowerShell",
    ".rb": "Ruby", ".erb": "Ruby", ".rake": "Ruby",
    ".php": "PHP", ".php3": "PHP", ".php4": "PHP", ".php5": "PHP", ".phtml": "PHP",
    ".pl": "Perl", ".pm": "Perl", ".t": "Perl", ".pod": "Perl",
    ".lua": "Lua",
    ".r": "R", ".R": "R",
    ".hs": "Haskell", ".lhs": "Haskell",
    ".ml": "OCaml", ".mli": "OCaml",
    ".erl": "Erlang", ".hrl": "Erlang",
    ".ex": "Elixir", ".exs": "Elixir",
    ".elm": "Elm",
    ".lisp": "Lisp", ".lsp": "Lisp", ".cl": "Lisp", ".el": "Lisp",
    ".scm": "Scheme", ".ss": "Scheme", ".rkt": "Scheme",
    ".re": "ReasonML", ".rei": "ReasonML",
    ".purs": "PureScript",
    ".dart": "Dart",
    ".jl": "Julia",
    ".f": "Fortran", ".for": "Fortran", ".f90": "Fortran",
    ".f95": "Fortran", ".f03": "Fortran", ".f08": "Fortran",
    ".sol": "Solidity",
    ".move": "Move",
    ".vh": "Verilog", ".sv": "Verilog",
    ".vhd": "VHDL", ".vhdl": "VHDL",
    ".asm": "Assembly", ".s": "Assembly", ".S": "Assembly",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".jsp": "JSP", ".asp": "ASP", ".aspx": "ASP.NET",
    ".sql": "SQL", ".psql": "SQL", ".mysql": "SQL", ".pgsql": "SQL",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".proto": "Protobuf",
    ".tf": "Terraform", ".tfvars": "Terraform", ".hcl": "HCL",
    ".sls": "SaltStack",
    ".ipynb": "Jupyter",
    ".pas": "Pascal", ".pp": "Pascal", ".inc": "Pascal",
    ".cob": "COBOL", ".cbl": "COBOL", ".cpy": "COBOL",
    ".cr": "Crystal",
    ".vim": "Vimscript",
    ".tcl": "Tcl",
    ".awk": "AWK",
    ".sed": "Sed",
    ".mk": "Makefile",
    ".cmake": "CMake",
    ".hack": "Hack", ".hh": "Hack",
    ".groovy": "Groovy",
}

OPENSOURCE_LICENSE_FILES = {
    "license", "license.txt", "license.md", "license.rst",
    "licence", "licence.txt", "licence.md",
    "copying", "copying.txt", "copying.md",
}

OPENSOURCE_SPDX_INDICATORS = [
    "mit license", "apache license", "gnu general public",
    "bsd license", "bsd 3-clause", "bsd 2-clause", "mozilla public",
    "isc license", "creative commons", "the unlicense", "eclipse public",
    "european union public", "common development",
]

SKIP_DIRS = {
    '.git', 'node_modules', 'vendor', 'vendors', '__pycache__', 'env', 'venv', '.venv',
    '.tox', 'build', 'dist', '.idea', '.vscode',
    '.next', '.nuxt', '.svelte-kit', '.output', '.cache', '.parcel-cache',
    'out', 'coverage', '.pytest_cache', '.mypy_cache',
    '.env', 'virtualenv', 'conda-env', 'site-packages', 'dist-packages',
    '.yarn', '.pnp', 'bower_components', 'jspm_packages', 'web_modules',
    'migrations', 'alembic',
    'test', 'tests', 'spec', 'specs',
    'examples', 'samples', 'demo', 'benchmarks', 'screenshots',
    'Lib', 'lib64', 'Scripts', 'bin', 'Include', 'obj',
    'third_party', 'third-party', 'extern', 'external', 'custom_node_modules'
}

SKIP_EXTENSIONS = {
    '.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib', '.gitignore', '.gitmodules',
    '.zip', '.tar', '.gz', '.jpg', '.jpeg', '.png', '.gif',
    '.pdf', '.mp4', '.mp3', '.ttf', '.woff', '.woff2',
    '.eot', '.otf', '.ico', '.svg', '.webp', '.bmp',
    '.json', '.md', '.markdown', '.txt', '.yaml', '.yml', '.toml', '.xml', '.csv', '.tsv',
    '.diff', '.patch',
    '.org', '.rc', '.pro', '.properties', '.gradle', '.pom'
}

C_EXTENSIONS = {".c", ".h"}
CPP_EXTENSIONS = {".cpp", ".hpp", ".cc", ".cxx", ".c++", ".hxx", ".h++", ".C", ".H"}
PYTHON_EXTENSIONS = {".py", ".pyw", ".pyx", ".pxd", ".pyi"}
JAVASCRIPT_EXTENSIONS = {".js", ".mjs", ".cjs"}
TYPESCRIPT_EXTENSIONS = {".ts", ".tsx"}
JSX_EXTENSIONS = {".jsx"}
FRONTEND_FRAMEWORK_EXTENSIONS = {".vue", ".svelte"}
JAVA_EXTENSIONS = {".java"}
KOTLIN_EXTENSIONS = {".kt", ".kts"}
SCALA_EXTENSIONS = {".scala"}
GROOVY_EXTENSIONS = {".groovy", ".gradle"}
CLOJURE_EXTENSIONS = {".clj", ".cljs", ".cljc", ".edn"}
CSHARP_EXTENSIONS = {".cs"}
FSHARP_EXTENSIONS = {".fs", ".fsx", ".fsi"}
GO_EXTENSIONS = {".go"}
RUST_EXTENSIONS = {".rs"}
SWIFT_EXTENSIONS = {".swift"}
OBJC_EXTENSIONS = {".m", ".mm"}
UNIX_SHELL_EXTENSIONS = {".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh"}
WINDOWS_SHELL_EXTENSIONS = {".bat", ".cmd", ".ps1"}
RUBY_EXTENSIONS = {".rb", ".erb", ".rake"}
PHP_EXTENSIONS = {".php", ".php3", ".php4", ".php5", ".phtml"}
PERL_EXTENSIONS = {".pl", ".pm", ".t", ".pod"}
LUA_EXTENSIONS = {".lua"}
R_EXTENSIONS = {".r", ".R"}
HASKELL_EXTENSIONS = {".hs", ".lhs"}
OCAML_EXTENSIONS = {".ml", ".mli"}
ERLANG_EXTENSIONS = {".erl", ".hrl"}
ELIXIR_EXTENSIONS = {".ex", ".exs"}
ELM_EXTENSIONS = {".elm"}
DART_EXTENSIONS = {".dart"}
JULIA_EXTENSIONS = {".jl"}
NIM_EXTENSIONS = {".nim"}
CRYSTAL_EXTENSIONS = {".cr"}
ZIG_EXTENSIONS = {".zig"}
V_EXTENSIONS = {".v"}
VERILOG_EXTENSIONS = {".vh", ".sv"}
VHDL_EXTENSIONS = {".vhd", ".vhdl"}
ASSEMBLY_EXTENSIONS = {".asm", ".s", ".S"}
SOLIDITY_EXTENSIONS = {".sol"}
MOVE_EXTENSIONS = {".move"}
WEB_TEMPLATE_EXTENSIONS = {".jsp", ".asp", ".aspx"}
SQL_EXTENSIONS = {".sql", ".psql", ".mysql", ".pgsql"}
GRAPHQL_EXTENSIONS = {".graphql", ".gql"}
PROTOBUF_EXTENSIONS = {".proto"}
TERRAFORM_EXTENSIONS = {".tf", ".tfvars", ".hcl"}
SALT_EXTENSIONS = {".sls"}
JUPYTER_EXTENSIONS = {".ipynb"}
PASCAL_EXTENSIONS = {".pas", ".pp", ".inc"}
FORTRAN_EXTENSIONS = {".f", ".for", ".f90", ".f95", ".f03", ".f08"}
COBOL_EXTENSIONS = {".cob", ".cbl", ".cpy"}
ADA_EXTENSIONS = {".ada", ".adb", ".ads"}
D_EXTENSIONS = {".d"}
LISP_EXTENSIONS = {".lisp", ".lsp", ".cl", ".el"}
SCHEME_EXTENSIONS = {".scm", ".ss", ".rkt"}
VIM_EXTENSIONS = {".vim"}
TCL_EXTENSIONS = {".tcl"}
AWK_EXTENSIONS = {".awk"}
SED_EXTENSIONS = {".sed"}
MAKEFILE_EXTENSIONS = {".mk"}
CMAKE_EXTENSIONS = {".cmake"}
HACK_EXTENSIONS = {".hack", ".hh"}
REASON_EXTENSIONS = {".re", ".rei"}
PURESCRIPT_EXTENSIONS = {".purs"}

CODE_EXTENSIONS = (
    C_EXTENSIONS | CPP_EXTENSIONS | PYTHON_EXTENSIONS | JAVASCRIPT_EXTENSIONS |
    TYPESCRIPT_EXTENSIONS | JSX_EXTENSIONS | FRONTEND_FRAMEWORK_EXTENSIONS |
    JAVA_EXTENSIONS | KOTLIN_EXTENSIONS | SCALA_EXTENSIONS | GROOVY_EXTENSIONS |
    CLOJURE_EXTENSIONS | CSHARP_EXTENSIONS | FSHARP_EXTENSIONS | GO_EXTENSIONS |
    RUST_EXTENSIONS | SWIFT_EXTENSIONS | OBJC_EXTENSIONS | UNIX_SHELL_EXTENSIONS |
    WINDOWS_SHELL_EXTENSIONS | RUBY_EXTENSIONS | PHP_EXTENSIONS | PERL_EXTENSIONS |
    LUA_EXTENSIONS | R_EXTENSIONS | HASKELL_EXTENSIONS | OCAML_EXTENSIONS |
    ERLANG_EXTENSIONS | ELIXIR_EXTENSIONS | ELM_EXTENSIONS | DART_EXTENSIONS |
    JULIA_EXTENSIONS | NIM_EXTENSIONS | CRYSTAL_EXTENSIONS | ZIG_EXTENSIONS |
    V_EXTENSIONS | VERILOG_EXTENSIONS | VHDL_EXTENSIONS | ASSEMBLY_EXTENSIONS |
    SOLIDITY_EXTENSIONS | MOVE_EXTENSIONS | WEB_TEMPLATE_EXTENSIONS |
    SQL_EXTENSIONS | GRAPHQL_EXTENSIONS | PROTOBUF_EXTENSIONS |
    TERRAFORM_EXTENSIONS | SALT_EXTENSIONS |
    JUPYTER_EXTENSIONS | PASCAL_EXTENSIONS | FORTRAN_EXTENSIONS |
    COBOL_EXTENSIONS | ADA_EXTENSIONS | D_EXTENSIONS | LISP_EXTENSIONS |
    SCHEME_EXTENSIONS | VIM_EXTENSIONS | TCL_EXTENSIONS | AWK_EXTENSIONS |
    SED_EXTENSIONS | MAKEFILE_EXTENSIONS | CMAKE_EXTENSIONS | HACK_EXTENSIONS |
    REASON_EXTENSIONS | PURESCRIPT_EXTENSIONS
)

NON_CODE_EXTENSIONS = {
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".tex",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".csv", ".tsv", ".dat",
    ".log", ".tmp", ".temp", ".bak", ".swp", ".swo",
    ".lock", ".pem", ".crt", ".key", ".cer",
    ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"
}

# Text assets that are useful to an LLM even though they are not source code.
DOC_TEXT_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".adoc", ".tex"}
CONFIG_TEXT_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml"}
LLM_TEXT_EXTENSIONS = DOC_TEXT_EXTENSIONS | CONFIG_TEXT_EXTENSIONS

# Business-defined non-authored directories. The authored SCC scan excludes
# these in addition to SCC-detected generated files (--no-gen).
SCC_NON_AUTHORED_DIRS = {
    ".git", ".hg", ".svn", "vendor", "node_modules", "dist", "build",
    "generated", "migrations", "autogen", "auto-generated", "generated-src",
    "generated-code", "__pycache__", ".pytest_cache", "obj", "coverage",
    ".nyc_output", ".next", ".nuxt", ".gradle", ".cxx", "Pods",
    "DerivedData", "vendor_prefixed", "vendor8", "bower_components",
    "generated-sources", "intermediates", ".idea", ".kotlin", ".vite",
    ".cache",
}

# SCC recognizes documentation/configuration/data formats too. They remain in
# total repository LOC, but are not source-code token inputs; their tokens are
# counted independently through DOC_TEXT_EXTENSIONS/CONFIG_TEXT_EXTENSIONS.
SCC_NON_SOURCE_LANGUAGES = {
    "JSON", "XML", "YAML", "CSV", "TOML", "INI", "Plain Text", "Text",
    "Markdown", "reStructuredText", "AsciiDoc", "Doxygen", "Org", "Org Mode",
    "TeX", "SVG", "Properties", "Excel", "Parquet", "License",
}

SPECIAL_CODE_FILES = {
    "dockerfile": "code", "makefile": "code", "gnumakefile": "code",
    "rakefile": "code", "gemfile": "code", "vagrantfile": "code",
    "jenkinsfile": "code", "podfile": "code", "brewfile": "code",
    "cakefile": "code", "guardfile": "code", "fastfile": "code",
    "dangerfile": "code"
}

EXCLUDED_SPECIAL_FILES = {
    "readme", "license", "licence", "changelog", "changes",
    "authors", "contributors", "notice", "patents", "copying",
    "todo", "history", "news", "thanks", "credits", "citation"
}

CONFIG_FILENAMES = {
    'requirements.txt', 'package.json', 'pom.xml',
    'build.gradle', 'cargo.toml', 'dockerfile', 'makefile', 'setup.py',
    'gemfile', 'rakefile', 'podfile'
}

CI_FILENAMES = {
    ".gitlab-ci.yml", ".gitlab-ci.yaml", "circle.yml", ".circleci",
    "azure-pipelines.yml", "azure-pipelines.yaml", ".travis.yml",
    "appveyor.yml", "jenkinsfile"
}

CI_DIRECTORIES = {".github/workflows", ".circleci", ".azure-pipelines"}

CI_PATH_PATTERNS = {'.github/workflows', '.github\\workflows', '.circleci/', '.gitlab-ci', 'azure-pipelines'}

_overlap = CODE_EXTENSIONS & NON_CODE_EXTENSIONS
if _overlap:
    raise ValueError(
        f"CRITICAL: Extension overlap detected between CODE and NON_CODE: {_overlap}\n"
        f"This indicates a configuration error. Please review extension definitions."
    )

allCodeExts = []

extSets = [
    C_EXTENSIONS, CPP_EXTENSIONS, PYTHON_EXTENSIONS, JAVASCRIPT_EXTENSIONS,
    TYPESCRIPT_EXTENSIONS, JSX_EXTENSIONS, FRONTEND_FRAMEWORK_EXTENSIONS,
    JAVA_EXTENSIONS, KOTLIN_EXTENSIONS, SCALA_EXTENSIONS, GROOVY_EXTENSIONS,
    CLOJURE_EXTENSIONS, CSHARP_EXTENSIONS, FSHARP_EXTENSIONS, GO_EXTENSIONS,
    RUST_EXTENSIONS, SWIFT_EXTENSIONS, OBJC_EXTENSIONS, UNIX_SHELL_EXTENSIONS,
    WINDOWS_SHELL_EXTENSIONS, RUBY_EXTENSIONS, PHP_EXTENSIONS, PERL_EXTENSIONS,
    LUA_EXTENSIONS, R_EXTENSIONS, HASKELL_EXTENSIONS, OCAML_EXTENSIONS,
    ERLANG_EXTENSIONS, ELIXIR_EXTENSIONS, ELM_EXTENSIONS, DART_EXTENSIONS,
    JULIA_EXTENSIONS, NIM_EXTENSIONS, CRYSTAL_EXTENSIONS, ZIG_EXTENSIONS,
    V_EXTENSIONS, VERILOG_EXTENSIONS, VHDL_EXTENSIONS, ASSEMBLY_EXTENSIONS,
    SOLIDITY_EXTENSIONS, MOVE_EXTENSIONS, WEB_TEMPLATE_EXTENSIONS,
    SQL_EXTENSIONS, GRAPHQL_EXTENSIONS, PROTOBUF_EXTENSIONS,
    TERRAFORM_EXTENSIONS, SALT_EXTENSIONS,
    JUPYTER_EXTENSIONS, PASCAL_EXTENSIONS, FORTRAN_EXTENSIONS,
    COBOL_EXTENSIONS, ADA_EXTENSIONS, D_EXTENSIONS, LISP_EXTENSIONS,
    SCHEME_EXTENSIONS, VIM_EXTENSIONS, TCL_EXTENSIONS, AWK_EXTENSIONS,
    SED_EXTENSIONS, MAKEFILE_EXTENSIONS, CMAKE_EXTENSIONS, HACK_EXTENSIONS,
    REASON_EXTENSIONS, PURESCRIPT_EXTENSIONS
]

for extGroup in extSets:
    allCodeExts.extend(extGroup)

if len(allCodeExts) != len(set(allCodeExts)):
    from collections import Counter
    duplicates = [ext for ext, count in Counter(allCodeExts).items() if count > 1]
    raise ValueError(
        f"CRITICAL: Duplicate extensions found in CODE_EXTENSIONS groups: {duplicates}\n"
        f"Each extension should appear in only one group."
    )


def getWorkerCount() -> int:
    try:
        cpuCores = multiprocessing.cpu_count()
        return max(1, min(cpuCores, cpuCores - 1))
    except Exception:
        return 4


def getAdaptiveBatchSize(fileList: List[str]) -> int:
    try:
        if HAS_PSUTIL:
            availableMb = psutil.virtual_memory().available / (1024 * 1024)
        else:
            availableMb = 4096  # Assume 4GB if psutil missing
        safeFiles = int((availableMb * 0.80 * 1024) / 50)
        return max(50, min(safeFiles, len(fileList)))
    except Exception:
        return min(200, len(fileList))


def logResourceStats(label: str = "") -> None:
    """Log current CPU and RAM usage statistics."""
    if not HAS_PSUTIL:
        return
    try:
        cpuPct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        logger.info(
            f"[RESOURCES]{' ' + label if label else ''} "
            f"CPU={cpuPct:.1f}%  RAM={mem.percent:.1f}%  "
            f"Available={mem.available // (1024*1024)} MB"
        )
    except Exception:
        pass


def countLexicalTokens(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[A-Za-z_]+|\d+|==|!=|<=|>=|[^\s]", text)


def getLlmTokens(text: str) -> int:
    if not text:
        return 0

    if HAS_TIKTOKEN:
        try:
            return len(ENCODING.encode(text, disallowed_special=()))
        except Exception as e:
            logger.warning(f"[getLlmTokens] tiktoken encode failed: {e}")
    return max(1, len(text) // 4)


def countLoc(text: str) -> int:
    if not text:
        return 0

    return sum(1 for line in text.splitlines() if line.strip())


def computeSimilarity(tokens1: List[str], tokens2: List[str]) -> float:
    set1 = tokens1 if isinstance(tokens1, (set, frozenset)) else set(tokens1)
    set2 = tokens2 if isinstance(tokens2, (set, frozenset)) else set(tokens2)
    if not set1 and not set2:
        return 1.0

    if not set1 or not set2:
        return 0.0

    return len(set1 & set2) / len(set1 | set2)


def cloneRepo(repoUrl: str, targetDir: str, githubToken: Optional[str] = None,
              gitlabToken: Optional[str] = None, bitbucketToken: Optional[str] = None,
              branch: Optional[str] = None) -> bool:
    actualUrl = repoUrl
    maskedUrl = repoUrl
    tokens_to_mask = []

    # Check for GitLab
    glInfo = extractGitLabRepoInfo(targetDir, repoUrl)
    if glInfo and gitlabToken:
        domain, project_path, _ = glInfo
        actualUrl = f"https://oauth2:{gitlabToken}@{domain}/{project_path}.git"
        maskedUrl = f"https://oauth2:[MASKED]@{domain}/{project_path}.git"
        tokens_to_mask.append(gitlabToken)
    else:
        # Check for Bitbucket
        bbInfo = extractBitbucketRepoInfo(targetDir, repoUrl)
        if bbInfo and bitbucketToken:
            domain, workspace, repo, _is_cloud = bbInfo
            actualUrl = f"https://x-token-auth:{bitbucketToken}@{domain}/{workspace}/{repo}.git"
            maskedUrl = f"https://x-token-auth:[MASKED]@{domain}/{workspace}/{repo}.git"
            tokens_to_mask.append(bitbucketToken)
        else:
            # Check for GitHub
            ghInfo = extractGitHubRepoInfo(targetDir, repoUrl)
            if ghInfo and githubToken:
                owner, repo = ghInfo
                actualUrl = f"https://{githubToken}@github.com/{owner}/{repo}.git"
                maskedUrl = f"https://[MASKED]@github.com/{owner}/{repo}.git"
                tokens_to_mask.append(githubToken)

    def mask_text(text: str) -> str:
        if not text:
            return text
        for t in tokens_to_mask:
            if t:
                text = text.replace(t, "[MASKED]")
        return text

    cloneArgs = ["git", "clone", "--progress"]
    if branch:
        cloneArgs.extend(["--branch", branch])
    cloneArgs.extend([actualUrl, targetDir])

    if actualUrl != repoUrl:
        logger.info(f"[cloneRepo] Rewrote URL for authenticated clone: {maskedUrl}")
        try:
            process = subprocess.Popen(
                cloneArgs,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            
            def stream_reader(pipe, dest):
                try:
                    for line in pipe:
                        dest.write(mask_text(line))
                        dest.flush()
                except Exception:
                    pass

            t_out = threading.Thread(target=stream_reader, args=(process.stdout, sys.stdout))
            t_err = threading.Thread(target=stream_reader, args=(process.stderr, sys.stderr))
            t_out.start()
            t_err.start()

            process.wait()
            t_out.join()
            t_err.join()

            return process.returncode == 0
        except FileNotFoundError:
            logger.error("[cloneRepo] 'git' command not found. Please install git.")
            return False
        except Exception as e:
            logger.error(f"[cloneRepo] Unexpected error during authenticated clone: {mask_text(str(e))}")
            return False
    else:
        try:
            result = subprocess.run(
                cloneArgs,
                stdout=sys.stdout,
                stderr=sys.stderr,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            return result.returncode == 0
        except subprocess.CalledProcessError as e:
            logger.error(f"[cloneRepo] Clone failed with exit code {e.returncode}")
            return False
        except FileNotFoundError:
            logger.error("[cloneRepo] 'git' command not found. Please install git.")
            return False
        except Exception as e:
            logger.error(f"[cloneRepo] Unexpected error: {e}")
            return False


def isBinary(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
            return b"\0" in chunk
    except Exception:
        return True


def hashFile(filepath: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return f"hash_error_{filepath}"


def readFileSafe(filepath: str) -> Optional[str]:
    """Read a file safely using utf-8 or latin-1 fallback."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='latin-1') as f:
                return f.read()
        except Exception:
            return None
    except Exception:
        return None


def classifyFile(filename: str, ext: str) -> Tuple[bool, str]:
    """Determine if a file is code or non-code based on extensions."""
    nameLower = filename.lower()

    if nameLower in EXCLUDED_SPECIAL_FILES:
        return False, "excluded"

    if ext in NON_CODE_EXTENSIONS:
        return False, "non-code"

    if ext in CODE_EXTENSIONS:
        return True, "code"

    if not ext and nameLower in SPECIAL_CODE_FILES:
        return True, "code"

    return False, "unknown"


def runStage1Analysis(rootDir: str, maxFiles: Optional[int] = None) -> Tuple[Dict[str, Any], List[str]]:
    """
    Perform directory traversal to extract structure and detect repo health signals.
    Single-threaded I/O walk optimized for speed.
    """
    structure = {"dirs": 0, "files": 0, "extensions": defaultdict(int)}
    signals = {"hasSource": False, "hasTests": False, "hasConfig": False, "hasCI": False}
    fileList = []

    for dirpath, dirnames, filenames in os.walk(rootDir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and (not d.startswith('.') or d == '.github')]

        relDir = os.path.relpath(dirpath, rootDir)
        if relDir != ".":
            structure["dirs"] += 1

        relPathNormalized = relDir.replace('\\', '/').lower()
        for ciDir in CI_DIRECTORIES:
            if ciDir in relPathNormalized:
                signals["hasCI"] = True
                break

        for file in filenames:
            ext = os.path.splitext(file)[1].lower()
            basename = os.path.basename(file)
            basenameLower = basename.lower()

            # Documentation/configuration is part of the LLM context even though
            # several of these extensions are excluded from code-only analysis.
            if (ext in SKIP_EXTENSIONS and ext not in LLM_TEXT_EXTENSIONS) or (file.startswith('.') and ext):
                continue

            filepath = os.path.join(dirpath, file)

            if isVendoredOrGenerated(filepath, file):
                continue

            try:
                if os.path.getsize(filepath) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue

            isCode, classification = classifyFile(basename, ext)

            if classification == "excluded":
                continue

            structure["files"] += 1
            structure["extensions"][ext if ext else f".{classification}"] += 1
            fileList.append(filepath)

            if not signals["hasSource"] and isCode:
                signals["hasSource"] = True

            lname = file.lower()
            lpath = filepath.lower().replace('\\', '/')

            if 'test' in lname or 'spec' in lname or '/tests/' in lpath:
                signals["hasTests"] = True

            if lname in CONFIG_FILENAMES:
                signals["hasConfig"] = True

            if not signals["hasCI"]:
                if basenameLower in CI_FILENAMES:
                    signals["hasCI"] = True
                elif any(ciPath in lpath for ciPath in CI_PATH_PATTERNS):
                    signals["hasCI"] = True

            if maxFiles and structure["files"] >= maxFiles:
                break

        if maxFiles and structure["files"] >= maxFiles:
            break

    structure["extensions"] = dict(structure["extensions"])
    structure["test_file_count"] = sum(
        1 for fp in fileList
        if "test" in os.path.basename(fp).lower() or "spec" in os.path.basename(fp).lower()
    )
    fileCount = structure["files"]
    confidence = "high" if fileCount > 50 else "medium" if fileCount > 10 else "low"

    return {
        "structure": structure,
        "signals": signals,
        "confidence": confidence
    }, fileList


def getSccCommand() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ext = ".exe" if sys.platform == "win32" else ""
    local_scc = os.path.join(script_dir, f"scc{ext}")

    if os.path.isfile(local_scc):
        return local_scc
    return "scc"


def validateSccVersion() -> None:
    """Fail fast unless the installed SCC version is the required one."""
    scc_command = getSccCommand()
    try:
        result = subprocess.run(
            [scc_command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"SCC {REQUIRED_SCC_VERSION} is required, but 'scc' was not found. "
            "Install it or place scc.exe in the tool directory."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out while checking the SCC version.") from exc

    output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0:
        raise RuntimeError(
            f"SCC version check failed with exit code {result.returncode}: {output or 'no output'}"
        )

    match = re.search(r"\b(\d+\.\d+\.\d+)\b", output)
    installed_version = match.group(1) if match else None
    if installed_version != REQUIRED_SCC_VERSION:
        found = installed_version or (output or "unknown")
        raise RuntimeError(
            f"SCC {REQUIRED_SCC_VERSION} is required. Installed version: {found}."
        )


def runScc(repoDir: str, authoredOnly: bool = False) -> Dict[str, Any]:
    """
    Run SCC and return normalized totals, language breakdown, and source files.

    The total scan matches `scc .`. The authored scan applies the agreed
    business policy: SCC-generated files plus configured dependency/vendor/
    build/migration directories are non-authored. `source_files` contains only
    authored programming-language files and drives source token calculation.
    """
    try:
        cmd = [getSccCommand(), "--format", "json2"]
        if authoredOnly:
            cmd.extend([
                "--no-gen",
                "--by-file",
                "--exclude-dir",
                ",".join(sorted(SCC_NON_AUTHORED_DIRS, key=str.lower)),
            ])
        cmd.append(".")
        result = subprocess.run(
            cmd, cwd=repoDir, capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            logger.warning(f"[runScc] SCC exited with {result.returncode}: {result.stderr.strip()}")
            return {}
        if not result.stdout.strip():
            return {}

        payload = json.loads(result.stdout)
        summaries = payload.get("languageSummary", [])
        totals = {"code": 0, "comment": 0, "blank": 0, "lines": 0, "nFiles": 0}
        sourceTotals = {"code": 0, "comment": 0, "blank": 0, "lines": 0, "nFiles": 0}
        languages = {}
        sourceFiles = []
        repoAbs = os.path.abspath(repoDir)

        for stats in summaries:
            language = str(stats.get("Name") or "Unknown")
            normalized = {
                "code": int(stats.get("Code", 0) or 0),
                "comment": int(stats.get("Comment", 0) or 0),
                "blank": int(stats.get("Blank", 0) or 0),
                "lines": int(stats.get("Lines", 0) or 0),
                "nFiles": int(stats.get("Count", 0) or 0),
            }
            languages[language] = normalized
            for key in totals:
                totals[key] += normalized[key]

            if authoredOnly and language not in SCC_NON_SOURCE_LANGUAGES:
                for key in sourceTotals:
                    sourceTotals[key] += normalized[key]
                for fileStats in stats.get("Files", []) or []:
                    location = fileStats.get("Location")
                    if not location:
                        continue
                    path = os.path.normpath(
                        location if os.path.isabs(location) else os.path.join(repoAbs, location)
                    )
                    try:
                        if os.path.commonpath([repoAbs, os.path.abspath(path)]) != repoAbs:
                            continue
                    except ValueError:
                        continue
                    if os.path.isfile(path):
                        sourceFiles.append(path)

        return {
            "totals": totals,
            "source_totals": sourceTotals if authoredOnly else totals.copy(),
            "languages": languages,
            "source_files": list(dict.fromkeys(sourceFiles)),
            "source": "scc",
            "mode": "authored" if authoredOnly else "total",
        }
    except Exception as e:
        logger.warning(f"[runScc] Failed to run SCC: {e}")
    return {}


def processFileBatch(batch: List[Any]) -> List[Dict[str, Any]]:
    """
    Worker function to process file batch in parallel.
    Computes LOC, tokens, and prepares data for duplication analysis.
    Code, documentation, and configuration files contribute to token metrics.
    Duplication and AI-detection inputs remain code-only.
    """
    results = []
    for workItem in batch:
        if isinstance(workItem, (tuple, list)):
            filepath, forcedCategory = workItem
        else:
            filepath, forcedCategory = workItem, None
        ext = os.path.splitext(filepath)[1].lower()
        basename = os.path.basename(filepath)

        isCode, classification = classifyFile(basename, ext)

        isTextAsset = ext in LLM_TEXT_EXTENSIONS
        if forcedCategory == "code":
            isCode = True
        elif forcedCategory in {"docs", "config"}:
            isCode = False
            isTextAsset = True
        if not isCode and not isTextAsset:
            continue

        category = forcedCategory or (
            "code" if isCode else ("docs" if ext in DOC_TEXT_EXTENSIONS else "config")
        )

        try:
            # SCC-authored source files define the complete code-token corpus.
            # Preserve the historical size cap only for docs/config assets.
            if category != "code" and os.path.getsize(filepath) > MAX_FILE_BYTES:
                continue

            # One disk read supplies binary detection, hashing, and tokenization.
            with open(filepath, "rb") as f:
                raw = f.read()
            if b"\x00" in raw[:8192]:
                continue
            content = raw.decode("utf-8", errors="ignore")

            if not content:
                continue

            llmTokens = len(ENCODING.encode(content)) if HAS_TIKTOKEN else (len(content) // 4)
            lexicalTokenList = countLexicalTokens(content)
            lexicalTokens = len(lexicalTokenList)
            loc = countLoc(content)

            tokenList = frozenset(lexicalTokenList) if isCode and (10 < lexicalTokens < 5000) else frozenset()

            # Framework detection via imports (first 100 lines)
            foundFws = []
            if ext.lstrip('.') in IMPORT_FRAMEWORK_MAP:
                lines = content.splitlines()[:100]
                for line in lines:
                    lineLower = line.lower()
                    if "import " in lineLower or "from " in lineLower or "require(" in lineLower:
                        for kw, formalName in IMPORT_FRAMEWORK_MAP[ext.lstrip('.')].items():
                            if re.search(rf"\b{kw}\b", lineLower):
                                foundFws.append(formalName)
            results.append({
                "filepath": filepath,
                "llm_tokens": llmTokens,
                "lexical_tokens": lexicalTokens,
                "loc": loc,
                "frameworks": list(set(foundFws)),
                "hash": hashlib.sha256(raw).hexdigest(),
                "token_set": tokenList,
                "category": category,
            })
        except Exception:
            continue

    return results


def runStage2Analysis(
    fileList: List[str], authoredCodeFiles: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, List[str]]]:
    """
    Execute parallel deep analysis to compute total/per-file metrics,
    exact duplication (hash-based), and similarity-based duplication (Jaccard clustering).
    Uses ProcessPoolExecutor for CPU-bound tokenization.
    """
    authoredSet = {
        os.path.normcase(os.path.abspath(path)) for path in (authoredCodeFiles or [])
    }
    categoryByPath: Dict[str, str] = {}
    for path in authoredCodeFiles or []:
        categoryByPath[os.path.normcase(os.path.abspath(path))] = "code"
    for path in fileList:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in authoredSet:
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in DOC_TEXT_EXTENSIONS:
            categoryByPath[normalized] = "docs"
        elif ext in CONFIG_TEXT_EXTENSIONS:
            categoryByPath[normalized] = "config"

    codeSet = {path for path, category in categoryByPath.items() if category == "code"}
    docsSet = {path for path, category in categoryByPath.items() if category == "docs"}
    configSet = {path for path, category in categoryByPath.items() if category == "config"}
    if codeSet & docsSet or codeSet & configSet or docsSet & configSet:
        raise ValueError("[stage2] Token input categories overlap")

    workItems = [(path, category) for path, category in categoryByPath.items()]
    if not workItems:
        return {}, {}, {}, {}

    tokenPaths = [path for path, _ in workItems]
    batchSize = getAdaptiveBatchSize(tokenPaths)
    batches = [workItems[i:i + batchSize] for i in range(0, len(workItems), batchSize)]
    numWorkers = min(getWorkerCount(), len(batches))

    logger.info(f"[stage2] Workers={numWorkers}  Batch={batchSize}  Files={len(workItems)}")

    perFileLlm = {}
    perFileLexical = {}
    perFileLoc = {}
    totalLlm = 0
    totalLlmCode = totalLlmDocs = totalLlmConfig = 0
    totalLexical = 0
    totalLexicalCode = totalLexicalDocs = totalLexicalConfig = 0
    totalLoc = 0
    contentHashes = defaultdict(list)
    fileTokensMap = {}
    fileStatsMap = {}
    frameworkFindings = defaultdict(set)
    completedFiles = 0
    t0 = time.time()

    # Avoid process startup/pickling overhead for a single small batch.
    if len(batches) == 1:
        completedBatches = [processFileBatch(batches[0])]
    else:
        with ProcessPoolExecutor(max_workers=numWorkers) as executor:
            completedBatches = []
            futures = {executor.submit(processFileBatch, batch): batch for batch in batches}
            for future in as_completed(futures):
                try:
                    completedBatches.append(future.result())
                except Exception:
                    continue

    for batchResults in completedBatches:
            for item in batchResults:
                fp = item["filepath"]
                llm = item["llm_tokens"]
                lex = item["lexical_tokens"]
                loc = item["loc"]
                h = item["hash"]
                ts = item["token_set"]
                fws = item.get("frameworks", [])
                category = item.get("category", "code")

                totalLlm += llm
                totalLexical += lex
                totalLoc += loc
                perFileLlm[fp] = llm
                perFileLexical[fp] = lex
                perFileLoc[fp] = loc
                fileStatsMap[fp] = {"loc": loc, "llm_tokens": llm, "hash": h}
                contentHashes[h].append(fp)

                if category == "code":
                    totalLlmCode += llm
                    totalLexicalCode += lex
                elif category == "docs":
                    totalLlmDocs += llm
                    totalLexicalDocs += lex
                else:
                    totalLlmConfig += llm
                    totalLexicalConfig += lex

                for fw in fws:
                    frameworkFindings["code_imports"].add(fw)

                if category == "code" and loc >= MIN_LOC and ts and llm >= MIN_TOKENS:
                    fileTokensMap[fp] = ts

                completedFiles += 1

            elapsed = time.time() - t0
            throughput = completedFiles / elapsed if elapsed > 0 else 0
            if completedFiles % 200 == 0:
                logger.info(f"  → Processed {completedFiles}/{len(workItems)} files  ({throughput:.1f}/s)")

    exactDuplicateTokens = 0
    exactDuplicateCount = 0
    exactDuplicatedFiles = set()

    for h, paths in contentHashes.items():
        if len(paths) > 1:
            exactDuplicateCount += (len(paths) - 1)
            for dupFile in paths[1:]:
                exactDuplicateTokens += perFileLlm.get(dupFile, 0)
                exactDuplicatedFiles.add(dupFile)

    adj = defaultdict(set)
    processedPairs = 0

    sizeBuckets = defaultdict(list)
    for fp, tokens in fileTokensMap.items():
        sizeBuckets[len(tokens) // 50].append(fp)

    for key in sizeBuckets:
        if len(sizeBuckets[key]) > MAX_BUCKET_COMPARE:
            logger.warning(f"[similarity] Pruning bucket of size {len(sizeBuckets[key])} to {MAX_BUCKET_COMPARE}")
            sizeBuckets[key] = sizeBuckets[key][:MAX_BUCKET_COMPARE]

    for bucketFiles in sizeBuckets.values():
        for i in range(len(bucketFiles)):
            if processedPairs >= MAX_SIMILARITY_PAIRS:
                break

            f1 = bucketFiles[i]
            for j in range(i + 1, len(bucketFiles)):
                if processedPairs >= MAX_SIMILARITY_PAIRS:
                    break

                f2 = bucketFiles[j]
                processedPairs += 1
                if computeSimilarity(fileTokensMap[f1], fileTokensMap[f2]) >= SIMILARITY_THRESHOLD:
                    adj[f1].add(f2)
                    adj[f2].add(f1)

    visited = set()
    similarClusters = []
    similarDuplicateTokens = 0
    seen = set()

    nodes = list(adj.keys())
    for node in nodes:
        if node not in visited:
            stack = [node]
            cluster = []
            while stack:
                curr = stack.pop()
                if curr in visited:
                    continue

                visited.add(curr)
                cluster.append(curr)
                if curr in adj:
                    stack.extend(adj[curr] - visited)

            if len(cluster) > 1:
                similarClusters.append(cluster)
                for file in cluster[1:]:
                    if file not in seen:
                        similarDuplicateTokens += perFileLlm.get(file, 0)
                        seen.add(file)

    totalDuplicateTokens = exactDuplicateTokens + similarDuplicateTokens
    tokenWeighted = (totalDuplicateTokens / totalLlm) if totalLlm > 0 else 0

    if len(similarClusters) > 0 and totalDuplicateTokens == 0:
        raise ValueError("[stage2] Duplication broken: similarity exists but duplicate_tokens = 0")

    if exactDuplicateCount > 0 and exactDuplicateTokens == 0:
        raise ValueError("[stage2] Exact duplicates not contributing to token count")

    if len(similarClusters) > 0 and similarDuplicateTokens == 0 and exactDuplicateTokens == 0:
        raise ValueError("[stage2] Inconsistent similarity vs duplication metrics")

    if totalLlm != totalLlmCode + totalLlmDocs + totalLlmConfig:
        raise ValueError("[stage2] LLM token category totals do not reconcile")
    if totalLexical != totalLexicalCode + totalLexicalDocs + totalLexicalConfig:
        raise ValueError("[stage2] Lexical token category totals do not reconcile")

    return {
        "metrics": {
            "llm_tokens": {"total": totalLlm, "code": totalLlmCode, "docs": totalLlmDocs,
                           "config": totalLlmConfig, "per_file": perFileLlm},
            "lexical_tokens": {"total": totalLexical, "code": totalLexicalCode,
                               "docs": totalLexicalDocs, "config": totalLexicalConfig,
                               "per_file": perFileLexical},
            "loc": {"total": totalLoc, "per_file": perFileLoc}
        },
        "ratios": {
            "tokens_per_loc": round(totalLlm / totalLoc if totalLoc else 0, 2),
            "lexical_to_llm": round(totalLexical / totalLlm if totalLlm else 0, 4)
        },
        "duplication_metrics": {
            "token_weighted": round(tokenWeighted, 4),
            "duplicate_tokens": totalDuplicateTokens,
            "exact_duplicate_files": exactDuplicateCount,
            "similar_clusters": similarClusters
        },
        "file_stats_summary": {"total_files_analyzed": len(fileStatsMap)}
    }, fileStatsMap, fileTokensMap, {k: list(v) for k, v in frameworkFindings.items()}


def calculateMeanStd(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return mean, variance ** 0.5


def computeUniformity(fileStats: Dict[str, Any]) -> float:
    tokens = sorted([v["llm_tokens"] for v in fileStats.values() if v["llm_tokens"] > 0])
    if not tokens or len(tokens) < 2:
        return 0.0

    cutoff = int(len(tokens) * 0.90)
    tokens = tokens[:cutoff] if cutoff > 0 else tokens
    if len(tokens) < 2:
        return 0.0

    mean, std = calculateMeanStd(tokens)
    return 1.0 / (1.0 + (std / mean)) if mean > 0 else 0.0


def normalizeWeight(tokens: int) -> float:
    return min(1.0, tokens / 1000)


def calculateNamingConsistency(filePaths: List[str]) -> float:
    if not filePaths:
        return 0.0

    snake = sum(1 for f in filePaths if "_" in os.path.basename(f))
    total = len(filePaths)
    return snake / total


def calculateTokenSmoothness(fileStats: Dict[str, Any]) -> float:
    tokens = [v["llm_tokens"] for v in fileStats.values() if v["llm_tokens"] > 0]
    if not tokens or len(tokens) < 2:
        return 0.0

    mean, std = calculateMeanStd(tokens)
    cv = std / (mean + 1e-9)
    return 1.0 / (1.0 + cv)


def runAiDetectionAnalysis(
    fileStats: Dict[str, Any],
    duplicationMetrics: Dict[str, Any],
    fileTokensMap: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Execute AI-generated code detection using multi-signal heuristics.
    Weighted combination: duplication (30%), similarity (20%), cluster density (25%),
    uniformity (15%), naming (10%).
    """
    totalFiles = len(fileStats)
    if totalFiles == 0:
        return {"repo_score": 0.0, "confidence": "none", "signals": {}}

    filteredStats = {
        f: stats for f, stats in fileStats.items()
        if stats["loc"] >= MIN_LOC and stats["llm_tokens"] >= MIN_TOKENS
    }

    uniformity = computeUniformity(filteredStats)

    totalTokens = sum(s["llm_tokens"] for s in fileStats.values())
    dupTokens = duplicationMetrics.get("duplicate_tokens", 0)
    duplicationSignal = min(1.0, (dupTokens / totalTokens) * 2.5) if totalTokens > 0 else 0.0

    similarClusters = duplicationMetrics.get("similar_clusters", [])
    similarFiles = sum(len(cluster) for cluster in similarClusters)
    similaritySignal = similarFiles / totalFiles if totalFiles > 0 else 0.0

    clusterDensity = max(
        (len(cluster) / totalFiles) for cluster in similarClusters
    ) if similarClusters else 0.0

    naming = calculateNamingConsistency(list(filteredStats.keys()))
    smoothness = calculateTokenSmoothness(filteredStats)

    if len(similarClusters) > 0 and duplicationMetrics.get("duplicate_tokens", 0) == 0:
        raise ValueError("[aiDetection] CRITICAL: similarity exists but duplicate_tokens = 0")

    if duplicationMetrics.get("exact_duplicate_files", 0) > 0 and duplicationMetrics.get("duplicate_tokens", 0) == 0:
        raise ValueError("[aiDetection] CRITICAL: exact duplicates not contributing to tokens")

    if similaritySignal > 0.8 and duplicationSignal < 0.1:
        raise ValueError("[aiDetection] CRITICAL: inconsistent similarity vs duplication")

    aiScore = (
        0.30 * duplicationSignal +
        0.20 * similaritySignal +
        0.25 * clusterDensity +
        0.15 * uniformity +
        0.10 * naming
    )
    aiScore = max(0.0, min(1.0, aiScore))

    perFileScores = {}
    for file, stats in filteredStats.items():
        weight = normalizeWeight(stats["llm_tokens"])
        isDuplicated = any(file in cluster for cluster in similarClusters)
        perFileScores[file] = min(1.0, (weight * 0.6 + (0.4 if isDuplicated else 0.0)))

    confidence = "high" if len(filteredStats) > 50 else "medium" if len(filteredStats) > 10 else "low"

    return {
        "repo_score": round(aiScore, 4),
        "confidence": confidence,
        "signals": {
            "uniformity": round(uniformity, 4),
            "duplication": round(duplicationSignal, 4),
            "similarity": round(similaritySignal, 4),
            "cluster_density": round(clusterDensity, 4),
            "naming": round(naming, 4),
            "smoothness": round(smoothness, 4)
        },
        "per_file_scores": {f: round(s, 4) for f, s in perFileScores.items()}
    }


def runGit(args: List[str], cwd: str, timeout: int = GIT_LOG_TIMEOUT) -> Optional[str]:
    """Run a git command safely, returning stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def prepareAnalysisBranch(repoDir: str, requestedBranch: Optional[str]) -> Tuple[bool, Dict[str, str]]:
    """Checkout/update the requested branch, or the remote default when omitted."""
    requestedBranch = (requestedBranch or "").strip()
    remoteHead = runGit(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repoDir)
    if not remoteHead:
        runGit(["remote", "set-head", "origin", "--auto"], repoDir, timeout=300)
        remoteHead = runGit(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repoDir)
    defaultBranch = remoteHead.split("/", 1)[1] if remoteHead and "/" in remoteHead else ""
    targetBranch = requestedBranch or defaultBranch

    if not targetBranch:
        targetBranch = runGit(["symbolic-ref", "--short", "HEAD"], repoDir) or ""
        defaultBranch = targetBranch

    if requestedBranch:
        valid = runGit(["check-ref-format", "--branch", requestedBranch], repoDir)
        if valid is None:
            logger.error(f"[branch] Invalid branch name: {requestedBranch}")
            return False, {}

    if targetBranch:
        # Refresh the requested/default remote branch when possible. Failure is
        # tolerated only when the corresponding local branch already exists.
        runGit(["fetch", "origin", targetBranch], repoDir, timeout=300)
        localExists = runGit(["rev-parse", "--verify", f"refs/heads/{targetBranch}"], repoDir)
        remoteExists = runGit(["rev-parse", "--verify", f"refs/remotes/origin/{targetBranch}"], repoDir)
        if not localExists and not remoteExists:
            logger.error(f"[branch] Requested branch '{targetBranch}' was not found.")
            return False, {}

        checkoutArgs = ["checkout", targetBranch] if localExists else [
            "checkout", "-b", targetBranch, "--track", f"origin/{targetBranch}"
        ]
        if runGit(checkoutArgs, repoDir, timeout=300) is None:
            logger.error(f"[branch] Could not checkout branch '{targetBranch}'.")
            return False, {}
        if remoteExists:
            # Never rewrite history; update only when a clean fast-forward is possible.
            if runGit(["merge", "--ff-only", f"origin/{targetBranch}"], repoDir, timeout=300) is None:
                logger.error(
                    f"[branch] Could not fast-forward '{targetBranch}' to origin/{targetBranch}. "
                    "Check for local changes or divergent history."
                )
                return False, {}

    analysisBranch = runGit(["symbolic-ref", "--short", "HEAD"], repoDir) or targetBranch
    return True, {
        "default_branch": defaultBranch or analysisBranch or "",
        "analysis_branch": analysisBranch or "",
        "branch_selection_source": "requested" if requestedBranch else "repository_default",
    }


def getAllContributors(repoDir: str) -> List[Dict[str, Any]]:
    """
    Return a structured list of every contributor discovered from git history.

    Uses ``git shortlog -sne --all`` which emits lines of the form:
        <commit_count>\\t<Name> <email>

    This is additive metadata only — it does NOT affect unique_contributors,
    commit_count, or any other existing Stage 0 field.

    Returns a list of dicts:
        [{"name": str, "email": str, "commits": int}, ...]
    sorted descending by commit count.
    On any failure returns an empty list (no crash, no pipeline impact).
    """
    # Use a dedicated timeout — shortlog on repos with 10k+ commits can be slow.
    # 300 s is generous; on failure we return [] safely, never crash.
    raw = runGit(["shortlog", "-sne", "--all"], repoDir, timeout=300)
    if not raw:
        return []

    contributors: List[Dict[str, Any]] = []
    # Regex: optional leading spaces, commit count, tab, display name + optional <email>
    lineRe = re.compile(
        r"^\s*(\d+)\s+(.+?)(?:\s+<([^>]*)>)?\s*$"
    )
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = lineRe.match(line)
        if not m:
            continue
        commitCountStr, rawName, rawEmail = m.group(1), m.group(2), m.group(3)

        # Trim whitespace only — no identity merging
        name = (rawName or "").strip() or ""
        email = (rawEmail or "").strip() or ""

        try:
            commits = int(commitCountStr)
        except (ValueError, TypeError):
            commits = 0

        contributors.append({"name": name, "email": email, "commits": commits})

    contributors.sort(key=lambda x: x["commits"], reverse=True)
    return contributors

def getAllCommitsDetailed(repoDir: str) -> List[Dict[str, Any]]:
    """
    Return one row per commit with hash, author, email, date, message,
    and whether it's 'meaningful'.
    Merge commits are always marked not-meaningful, matching the
    --no-merges logic used for meaningful_commit_count in Stage 0.
    """
    FIELD_SEP = "\x1f"
    RECORD_SEP = "\x1e"
    fmt = f"%H{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}%ci{FIELD_SEP}%P{FIELD_SEP}%D{FIELD_SEP}%B{RECORD_SEP}"

    # Traverse every fetched reference.  This includes commits which are not
    # reachable from the currently checked-out branch while remaining honest
    # about history which was never fetched or is no longer reachable.
    raw = runGit(["log", "--all", f"--format={fmt}"], repoDir, timeout=300)
    if not raw:
        return []

    trivial = re.compile(
        r"^(wip|fix|fixup!|squash!|update|commit|temp|tmp|test|merge|revert|bump)[\s\.\!]*$",
        re.IGNORECASE,
    )

    commits = []
    skipped = 0
    for record in raw.split(RECORD_SEP):
        record = record.strip()
        if not record:
            continue
        parts = record.split(FIELD_SEP)
        if len(parts) != 7:
            skipped += 1
            continue
        commitHash, author, email, date, parents, refs, message = parts
        message = message.strip()
        subject = message.splitlines()[0] if message else ""
        isMergeCommit = len(parents.strip().split()) > 1
        isMeaningful = (
            not isMergeCommit
            and len(subject) > 10
            and not trivial.match(subject)
        )

        commits.append({
            "hash": commitHash,
            "author": author.strip(),
            "email": email.strip(),
            "date": date.strip(),
            "message": message,
            "parents": [value for value in parents.strip().split() if value],
            "refs": [value.strip() for value in refs.split(",") if value.strip()],
            "is_merge": isMergeCommit,
            "meaningful": "Yes" if isMeaningful else "No",
        })

    if skipped:
        logger.warning(f"[getAllCommitsDetailed] Skipped {skipped} malformed commit record(s).")

    return commits

def runStage0GitAnalysis(repoDir: str) -> Dict[str, Any]:
    """
    Stage 0 — Git Metadata Extraction.

    Collects without cloning: commit count, unique contributors, active
    development span, commit quality, and full-history integrity signals.
    Requires the directory to be a valid git repository.
    """
    result: Dict[str, Any] = {
        "available": False,
        "commit_count": 0,
        "unique_contributors": 0,
        "first_commit_date": None,
        "last_commit_date": None,
        "active_span_months": 0,
        "meaningful_commit_pct": 0.0,
        "history_integrity": {
            "appears_intact": False,
            "has_merge_commits": False,
            "force_push_risk": False,
            "single_bulk_commit": False,
            "notes": [],
        },
        "branch_count": 0,
        "default_branch": None,
        "analysis_branch": None,
        "branch_selection_source": None,
    }

    # Quick sanity-check: is this a git repo?
    if not os.path.isdir(os.path.join(repoDir, ".git")):
        result["notes"] = "Not a git repository — Stage 0 skipped."
        return result

    result["available"] = True
    print(f"[Stage 0] Analyzing Git history in {repoDir}...")

    raw = runGit(["rev-list", "--count", "HEAD"], repoDir)
    if raw and raw.isdigit():
        result["commit_count"] = int(raw)

    raw = runGit(["log", "HEAD", "--format=%ae"], repoDir)
    if raw:
        emails = {e.strip().lower() for e in raw.splitlines() if e.strip()}
        result["unique_contributors"] = len(emails)

    tagsRaw = runGit(["tag"], repoDir)
    result["tag_count"] = len(tagsRaw.splitlines()) if tagsRaw else 0

    rawTs = runGit(["log", "HEAD", "--format=%at"], repoDir)
    if rawTs:
        tsList = []
        for line in rawTs.splitlines():
            t = line.strip()
            if t.isdigit():
                tsList.append(int(t))

        if tsList:
            firstDt = datetime.datetime.fromtimestamp(min(tsList))
            lastDt = datetime.datetime.fromtimestamp(max(tsList))

            result["first_commit_date"] = firstDt.strftime("%Y-%m-%d")
            result["last_commit_date"] = lastDt.strftime("%Y-%m-%d")
            result["last_update"] = lastDt.strftime("%Y-%m-%d")

            delta = lastDt - firstDt
            result["active_span_months"] = max(1, round(delta.days / 30.44, 1))

    rawMsgs = runGit(["log", "HEAD", "--format=%s"], repoDir)
    if rawMsgs:
        msgs = [m.strip() for m in rawMsgs.splitlines() if m.strip()]
        trivial = re.compile(
            r"^(wip|fix|fixup!|squash!|update|commit|temp|tmp|test|merge|revert|bump)[\s\.\!]*$",
            re.IGNORECASE,
        )
        meaningful = sum(
            1 for m in msgs if len(m) > 10 and not trivial.match(m)
        )
        result["meaningful_commit_pct"] = round(
            meaningful / len(msgs) * 100 if msgs else 0, 1
        )
        result["total_commits_sampled"] = len(msgs)

    rawMerge = runGit(["log", "HEAD", "--merges", "--format=%H"], repoDir)
    if rawMerge:
        result["history_integrity"]["has_merge_commits"] = bool(rawMerge.strip())
        result["history_integrity"]["merge_commit_count"] = len(
            [h for h in rawMerge.splitlines() if h.strip()]
        )

    rawNonmerge = runGit(["log", "HEAD", "--no-merges", "--format=%s"], repoDir)
    result["meaningful_commit_count"] = 0
    if rawNonmerge:
        nonmergeMsgs = [m.strip() for m in rawNonmerge.splitlines() if m.strip()]
        trivial = re.compile(
            r"^(wip|fix|fixup!|squash!|update|commit|temp|tmp|test|merge|revert|bump)[\s\.\!]*$",
            re.IGNORECASE,
        )
        result["meaningful_commit_count"] = sum(
            1 for m in nonmergeMsgs if len(m) > 10 and not trivial.match(m)
        )

    if result["commit_count"] > 0:
        rawDates = runGit(["log", "HEAD", "--format=%ci"], repoDir)
        if rawDates:
            dates = [line[:10] for line in rawDates.splitlines() if line.strip()]
            uniqueDays = len(set(dates))
            if uniqueDays == 1 and result["commit_count"] > 10:
                result["history_integrity"]["single_bulk_commit"] = True
                result["history_integrity"]["notes"].append(
                    "All commits on a single day — possible bulk import."
                )

    shallowFile = os.path.join(repoDir, ".git", "shallow")
    if os.path.exists(shallowFile):
        result["history_integrity"]["force_push_risk"] = True
        result["history_integrity"]["notes"].append(
            "Shallow clone detected — full history may be truncated."
        )

    integrityOk = (
        not result["history_integrity"]["single_bulk_commit"]
        and not result["history_integrity"]["force_push_risk"]
        and result["commit_count"] >= 2
    )
    result["history_integrity"]["appears_intact"] = integrityOk

    result["all_contributors"] = getAllContributors(repoDir)
    result["all_commits_detailed"] = getAllCommitsDetailed(repoDir)

    rawBranches = runGit(["branch", "-a"], repoDir)
    if rawBranches:
        uniqueBranches = set()
        for line in rawBranches.splitlines():
            line = line.strip()
            if not line or "->" in line:
                continue
            if line.startswith("*"):
                line = line[1:].strip()
            prefixes = ["remotes/origin/", "origin/", "refs/heads/", "refs/remotes/origin/"]
            for prefix in prefixes:
                if line.startswith(prefix):
                    line = line[len(prefix):]
                    break
            if line:
                uniqueBranches.add(line)
        result["branch_count"] = len(uniqueBranches)

    rawHead = runGit(["symbolic-ref", "--short", "HEAD"], repoDir)
    result["analysis_branch"] = rawHead
    remoteHead = runGit(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repoDir)
    if remoteHead and "/" in remoteHead:
        result["default_branch"] = remoteHead.split("/", 1)[1]
    else:
        result["default_branch"] = rawHead

    return result


def computeLanguageBreakdown(
    fileList: List[str], perFileLoc: Dict[str, int]
) -> Dict[str, Any]:
    """
    Map every analysed file to a display language name using EXT_TO_LANGUAGE.
    Returns per-language LOC counts, file counts, and percentage shares.
    Also detects special extensionless code files (Dockerfile, Makefile, etc.).
    """
    langLoc: Dict[str, int] = defaultdict(int)
    langFiles: Dict[str, int] = defaultdict(int)
    totalLoc = sum(perFileLoc.values())

    for fp in fileList:
        ext = os.path.splitext(fp)[1].lower()
        name = os.path.basename(fp).lower()
        if not ext and name in SPECIAL_CODE_FILES:
            lang = name.capitalize()
        else:
            lang = EXT_TO_LANGUAGE.get(ext) or EXT_TO_LANGUAGE.get(
                os.path.splitext(fp)[1]  # try original case for .R etc.
            )

        if not lang:
            continue

        loc = perFileLoc.get(fp, 0)
        langLoc[lang] += loc
        langFiles[lang] += 1

    breakdown = {}
    for lang, loc in sorted(langLoc.items(), key=lambda x: -x[1]):
        pct = round(loc / totalLoc * 100, 2) if totalLoc else 0.0
        breakdown[lang] = {
            "loc":   loc,
            "files": langFiles[lang],
            "pct":   pct,
        }

    return {
        "languages":      list(breakdown.keys()),
        "language_count": len(breakdown),
        "breakdown":      breakdown,
    }


def detectFrameworks(repoDir: str) -> Dict[str, List[str]]:
    """
    Scan manifest files recursively for framework/library keywords.
    Aggregates results from all found manifests (useful for monorepos).
    """
    detected: Dict[str, List[str]] = {}

    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for manifest, keywords in FRAMEWORK_MANIFEST_PARSERS.items():
            if manifest in files:
                manifestPath = os.path.join(root, manifest)
                try:
                    with open(manifestPath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                    found = [kw for kw in keywords if kw in content]
                    if found:
                        if manifest not in detected:
                            detected[manifest] = []
                        detected[manifest].extend(found)
                except Exception:
                    continue
    for m in detected:
        detected[m] = list(set(detected[m]))
    return detected


def analyzeCodeAge(repoDir: str, fileList: List[str]) -> Dict[str, Any]:
    """
    Determine staleness of the codebase by querying the last-modified commit
    date for a sample of files via `git log --follow`.
    Returns the fraction of files not touched in the past 1 and 2 years,
    plus a breakdown bucket.
    """
    result: Dict[str, Any] = {
        "available": False,
        "sampled_files": 0,
        "pct_untouched_1yr": 0.0,
        "pct_untouched_2yr": 0.0,
        "age_distribution": {},
    }

    if not os.path.isdir(os.path.join(repoDir, ".git")):
        return result

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    buckets: Dict[str, int] = {
        "<6m": 0, "6m-1yr": 0, "1yr-2yr": 0, ">2yr": 0
    }
    sample = fileList[:500]
    ages: List[float] = []          # age in months

    for fp in sample:
        rel = os.path.relpath(fp, repoDir)
        raw = runGit(
            ["log", "--follow", "--format=%ci", "--max-count=1", "--", rel],
            repoDir,
            timeout=10,
        )
        if not raw:
            continue
        try:
            last_mod = datetime.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            months = (now - last_mod).days / 30.44
            ages.append(months)
            if months < 6:
                buckets["<6m"] += 1
            elif months < 12:
                buckets["6m-1yr"] += 1
            elif months < 24:
                buckets["1yr-2yr"] += 1
            else:
                buckets[">2yr"] += 1
        except Exception:
            continue

    if not ages:
        return result

    total = len(ages)
    old_1yr = sum(1 for a in ages if a > 12)
    old_2yr = sum(1 for a in ages if a > 24)

    result.update({
        "available": True,
        "sampled_files": total,
        "pct_untouched_1yr": round(old_1yr / total * 100, 1),
        "pct_untouched_2yr": round(old_2yr / total * 100, 1),
        "median_age_months": round(sorted(ages)[total // 2], 1),
        "age_distribution": buckets,
    })
    return result


def detectInfrastructure(repoDir: str) -> Dict[str, List[str]]:
    """Scan manifests and files for DBs, Deployment tools, and API usage recursively."""
    found = {"databases": set(), "deployment": set(), "apis": set()}

    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for m in INFRA_MANIFESTS_TO_CHECK:
            if m in files:
                path = os.path.join(root, m)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        for cat, keywords in INFRASTRUCTURE_INDICATORS.items():
                            for k in keywords:
                                if k in content:
                                    found[cat].add(k)

                        # Special Firebase Logic
                        if "firebase" in content:
                            found["apis"].add("firebase")
                            if any(x in content for x in ["firestore", "firebase-database", "realtime-database"]):
                                found["databases"].add("firebase")
                except Exception:
                    pass

        for cf in INFRA_CONFIG_FILES_TO_CHECK:
            if cf in files:
                path = os.path.join(root, cf)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        for prefix, db_name in INFRA_CONN_MAP.items():
                            if prefix in content:
                                found["databases"].add(db_name)
                except Exception:
                    pass

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in INFRA_DB_EXTENSIONS:
                found["databases"].add(f"Physical DB: {f}")

            if ext in INFRA_CODE_EXTENSIONS:
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as code_f:
                        head = "".join([code_f.readline() for _ in range(300)]).lower()

                        for cat, keywords in INFRASTRUCTURE_INDICATORS.items():
                            for k in keywords:
                                # Stricter regex: must be an import, require, or connection string
                                # Patterns: import k, from k, require('k'), k://, new k(
                                patterns = [
                                    rf"\bimport\s+{re.escape(k)}\b",
                                    rf"\bfrom\s+{re.escape(k)}\b",
                                    rf"require\(['\"]{re.escape(k)}['\"]",
                                    rf"\b{re.escape(k)}://",
                                    rf"\bnew\s+{re.escape(k)}\("
                                ]
                                if any(re.search(p, head) for p in patterns):
                                    found[cat].add(k)
                except Exception:
                    pass

        # 3. Detect via File Extensions (e.g. .sql files)
        if any(f.endswith(".sql") for f in files):
            found["databases"].add("SQL (Schema found)")

    # 2. Check for specific filenames

    for root, dirs, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue
        for d in dirs:
            if d == ".github":
                if os.path.exists(os.path.join(root, d, "workflows")):
                    found["deployment"].add("github actions")
        for f in files:
            f_lower = f.lower()
            for key, (cat, val) in INFRA_FILE_MAP.items():
                if key in f_lower:
                    found[cat].add(val)

    result = {}
    for cat, items in found.items():
        mapped = set()
        for item in items:
            # Map to canonical name if exists, otherwise title-case it
            name = INFRA_CANONICAL_MAP.get(item.lower(), item.title())
            mapped.add(name)

        # Deduplicate redundant ORM vs DB entries if both exist
        final_list = sorted(list(mapped))
        result[cat] = final_list

    return result


def isVendoredOrGenerated(filepath: str, filename: str) -> bool:
    """Detect vendored, minified, or generated files — mimics GitLab Linguist."""
    fnLower = filename.lower()
    fpLower = filepath.replace("\\", "/").lower()

    if fnLower in BOILERPLATE_FILES:
        return True

    # 1. Minified files
    if fnLower.endswith((".min.js", ".min.css", ".min.map")):
        return True

    # 2. Source maps
    if fnLower.endswith((".js.map", ".css.map")):
        return True

    # 3. Known vendored library names
    nameNoExt = os.path.splitext(fnLower)[0]
    # Strip version suffixes like "jquery-3.6.0" -> "jquery"
    nameBase = nameNoExt.split("-")[0].split(".")[0]
    if nameBase in VENDORED_NAMES or nameNoExt in VENDORED_NAMES:
        return True
    for vn in VENDORED_NAMES:
        if vn in fnLower:
            return True

    # 4. Vendored directories
    for vd in VENDORED_DIRS:
        if vd in fpLower:
            return True

    # 5. Bundle / chunk patterns (webpack, rollup, parcel output)
    if fnLower in BUNDLE_PATTERNS or any(p in fnLower for p in BUNDLE_PATTERNS):
        return True

    # 6. Large single-line files (machine-generated) — check first 4KB
    try:
        if os.path.getsize(filepath) > 1024 * 1024:  # > 1MB
            with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                sample = fh.read(4096)
                if sample and "\n" not in sample.strip() and len(sample) >= 4000:
                    return True
    except Exception:
        pass

    return False


def calculateByteBreakdown(repoDir: str) -> Dict[str, int]:
    """Calculate total bytes per language using GitLab Linguist-style rules.

    Excludes: minified files, vendored libraries, generated bundles,
    source maps, and machine-generated single-line files.
    """
    byte_counts = {}
    for root, dirs, files in os.walk(repoDir):
        # Prune skip directories and virtual environments
        def _is_venv_dir(d, parent):
            candidate = os.path.join(parent, d)
            return (os.path.isdir(os.path.join(candidate, "Lib", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.10", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.11", "site-packages")) or
                    os.path.isdir(os.path.join(candidate, "lib", "python3.12", "site-packages")) or
                    os.path.isfile(os.path.join(candidate, "pyvenv.cfg")))

        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith('.')
                   and not _is_venv_dir(d, root)]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXT_TO_LANG_MAP:
                continue

            filepath = os.path.join(root, f)

            # Skip vendored / generated / minified
            if isVendoredOrGenerated(filepath, f):
                continue

            base_lang = EXT_TO_LANG_MAP[ext]
            identity = base_lang

            try:
                size = os.path.getsize(filepath)
                # Quick scan for identity (first 50 lines)
                if ext in {'.py', '.js', '.jsx', '.ts', '.tsx'}:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f_obj:
                        head = [f_obj.readline().lower() for _ in range(50)]

                    content_head = "".join(head)
                    found_id = None

                    # Framework mapping logic
                    if ext == '.py':
                        if 'django' in content_head:
                            found_id = "Django (Python)"
                        elif 'flask' in content_head:
                            found_id = "Flask (Python)"
                        elif 'fastapi' in content_head:
                            found_id = "FastAPI (Python)"
                        elif 'tornado' in content_head:
                            found_id = "Tornado (Python)"
                        elif 'aiohttp' in content_head:
                            found_id = "aiohttp (Python)"
                    elif ext in {'.js', '.jsx', '.ts', '.tsx'}:
                        if 'next' in content_head:
                            found_id = f"Next.js ({base_lang})"
                        elif 'react' in content_head:
                            found_id = f"React ({base_lang})"
                        elif 'vue' in content_head:
                            found_id = f"Vue ({base_lang})"
                        elif 'express' in content_head:
                            found_id = f"Express ({base_lang})"
                        elif 'jest' in content_head:
                            found_id = f"Jest ({base_lang})"

                    if found_id:
                        identity = found_id

                byte_counts[identity] = byte_counts.get(identity, 0) + size
            except Exception:
                continue
    return byte_counts


def analyzeDocumentation(repoDir: str) -> Dict[str, Any]:
    """Extract description and setup guidelines from README (recursive)."""
    res = {"description": "N/A", "has_setup": False, "doc_quality": "Low", "env_vars": "None"}

    readmePath = None
    # Scan up to depth 2 for README
    for root, _, files in os.walk(repoDir):
        depth = root.replace(repoDir, "").count(os.sep)
        if depth > 2:
            continue
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for f in files:
            if f.lower().startswith("readme"):
                readmePath = os.path.join(root, f)
                break
        if readmePath:
            break

    if readmePath:
        try:
            with open(readmePath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                lines = content.splitlines()

                # Simple description (first non-header paragraph)
                for line in lines:
                    clean = line.strip()
                    if clean and not clean.startswith("#"):
                        res["description"] = clean[:200] + ("..." if len(clean) > 200 else "")
                        break
                # Setup detection
                headers = [line.lower() for line in lines if line.strip().startswith("#")]
                if any(any(k in h for k in DOC_SETUP_KEYWORDS) for h in headers):
                    res["has_setup"] = True

                # Quality heuristic (based on number of headers and length)
                if len(headers) > 5 and len(content) > 2000:
                    res["doc_quality"] = "High"
                elif len(headers) > 2:
                    res["doc_quality"] = "Medium"

                # Env vars detection
                if ".env" in content.lower() or "environment variables" in content.lower():
                    res["env_vars"] = "Detected in README"
        except Exception:
            pass

    # Check for .env files
    for f in os.listdir(repoDir):
        if ".env" in f.lower():
            res["env_vars"] = "Mapped (.env file present)"

    return res


def detectCoverage(repoDir: str) -> str:
    for root, _, files in os.walk(repoDir):
        if any(sd in root for sd in SKIP_DIRS):
            continue
        for f in files:
            if f.lower() in COVERAGE_FILES:
                return "Detected"
    return "N/A"


def estimateTestCases(fileList: List[str]) -> int:
    """Heuristic count of test cases by scanning patterns in test files."""
    count = 0
    testFiles = [f for f in fileList if "test" in f.lower() or "spec" in f.lower()]

    for path in testFiles[:200]:  # Limit scan to first 200 test files for speed
        ext = path.split('.')[-1].lower()
        pattern = None
        if ext == "py":
            pattern = TEST_CASE_PATTERNS["python"]
        elif ext in ("js", "jsx"):
            pattern = TEST_CASE_PATTERNS["javascript"]
        elif ext in ("ts", "tsx"):
            pattern = TEST_CASE_PATTERNS["typescript"]
        elif ext == "java":
            pattern = TEST_CASE_PATTERNS["java"]
        elif ext == "go":
            pattern = TEST_CASE_PATTERNS["go"]
        elif ext == "php":
            pattern = TEST_CASE_PATTERNS["php"]
        elif ext == "rb":
            pattern = TEST_CASE_PATTERNS["ruby"]

        if pattern:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    count += len(pattern.findall(content))
            except Exception:
                pass
    return count


def computeComplexityScore(loc: int, total_tokens: int, file_count: int) -> str:
    if loc == 0:
        return "N/A"
    density = total_tokens / loc
    mod = loc / file_count if file_count > 0 else loc

    if density > 15 or mod > 500:
        return "High"
    if density > 8 or mod > 200:
        return "Moderate"
    return "Low"


def detectOpenSource(repoDir: str) -> Dict[str, Any]:
    """
    Detect whether a repository is open-source (recursive search).
    Checks for LICENSE files and known SPDX identifiers.
    """
    result = {
        "is_open_source": False,
        "license_file": None,
        "license_type": None,
        "has_readme": False,
    }

    for root, _, files in os.walk(repoDir):
        depth = root.replace(repoDir, "").count(os.sep)
        if depth > 2:
            continue
        if any(sd in root for sd in SKIP_DIRS):
            continue

        for f in files:
            f_lower = f.lower()
            # README detection
            if f_lower.startswith("readme"):
                result["has_readme"] = True

            # LICENSE detection
            if f_lower in OPENSOURCE_LICENSE_FILES:
                result["license_file"] = f
                license_path = os.path.join(root, f)
                try:
                    with open(license_path, "r", encoding="utf-8", errors="ignore") as f_obj:
                        content = f_obj.read(2000).lower()
                    for indicator in OPENSOURCE_SPDX_INDICATORS:
                        if indicator in content:
                            result["is_open_source"] = True
                            result["license_type"] = indicator.title()
                            break
                    else:
                        result["is_open_source"] = True
                        result["license_type"] = "Unknown (license file present)"
                except Exception:
                    result["is_open_source"] = True

        if result["license_file"] and result["has_readme"]:
            break  # Found both, can stop early

    return result


def computeRepoRating(
    gitData: Dict[str, Any],
    stage1Signals: Dict[str, Any],
    metricsData: Dict[str, Any],
    langData: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stage 4 — Composite repository quality rating (0.0 – 10.0).

    Combines multiple signals with weighted criteria to produce an overall rating and label.
    Also checks if minimum thresholds are met for basic health.
    """
    signals = stage1Signals.get("signals", {})
    structure = stage1Signals.get("structure", {})
    loc = metricsData.get("loc", {}).get("value", 0) or metricsData.get("loc", {}).get("total", 0)
    scores: Dict[str, float] = {}

    def _score(value, crit_key: str) -> float:
        c = RATING_CRITERIA[crit_key]
        mn, pref = c["min"], c["preferred"]
        if value >= pref:
            return 1.0
        if value >= mn:
            return 0.5 + 0.5 * (value - mn) / max(pref - mn, 1)
        if value > 0:
            return 0.5 * value / max(mn, 1)
        return 0.0

    scores["commits"] = _score(gitData.get("commit_count", 0), "commits")
    scores["dev_span_months"] = _score(gitData.get("active_span_months", 0), "dev_span_months")
    scores["contributors"] = _score(gitData.get("unique_contributors", 0), "contributors")
    scores["loc"] = _score(loc, "loc")
    scores["has_tests"] = 1.0 if signals.get("hasTests") else 0.0
    scores["has_ci"] = 1.0 if signals.get("hasCI") else 0.0

    # Test file count approximation via stage1 structure (count test-named files)
    test_file_count = structure.get("test_file_count", 0)
    scores["test_file_count"] = _score(test_file_count, "test_file_count")
    # Source file ratio
    total_files = structure.get("files", 0)
    lang_files_total = sum(
        v["files"] for v in langData.get("breakdown", {}).values()
    )
    src_ratio = lang_files_total / total_files if total_files > 0 else 0.0
    scores["source_file_ratio"] = _score(src_ratio, "source_file_ratio")
    # Weighted total
    weighted = sum(
        scores[k] * RATING_CRITERIA[k]["weight"]
        for k in scores
    )
    rating_10 = round(weighted * 10, 2)

    label = (
        "Excellent" if rating_10 >= 8 else
        "Good" if rating_10 >= 6 else
        "Fair" if rating_10 >= 4 else
        "Poor"
    )
    return {
        "rating":       rating_10,
        "label":        label,
        "component_scores": {k: round(v, 4) for k, v in scores.items()},
        "meets_minimum": all([
            gitData.get("commit_count", 0) >= RATING_CRITERIA["commits"]["min"],
            gitData.get("unique_contributors", 0) >= RATING_CRITERIA["contributors"]["min"],
            loc >= RATING_CRITERIA["loc"]["min"],
            signals.get("hasTests", False),
        ]),
    }


def scanSecrets(fileList: List[str]) -> Dict[str, Any]:
    """
    Stage 5 — Basic credential / secret scanning.
    Uses regex patterns to detect potential secrets in text-based files.`
    """
    findings: List[Dict[str, str]] = []
    scanned = 0

    for fp in fileList:
        ext = os.path.splitext(fp)[1].lower()
        name = os.path.basename(fp).lower()

        # Only scan text-based source files likely to contain secrets
        if ext not in SECRET_SCAN_EXTENSIONS and name not in {".env", "secrets"}:
            if ext in SECRET_SCAN_SKIP or ext not in CODE_EXTENSIONS:
                continue

        try:
            if os.path.getsize(fp) > 500_000:   # skip files > 500KB
                continue
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        scanned += 1
        for secret_type, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({
                    "file": os.path.basename(fp),
                    "path": fp,
                    "type": secret_type,
                })
                break

    return {
        "files_scanned":   scanned,
        "findings_count":  len(findings),
        "findings":        findings,
        "clean":           len(findings) == 0,
        "risk_level": (
            "high" if len(findings) >= 5 else
            "medium" if len(findings) >= 1 else
            "low"
        ),
    }


def extractGitLabRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str, str]]:
    """
    Detect GitLab domain, project path, and project name from inputTarget URL or local git configuration.
    Returns: (domain, project_path, project_name)
    """
    import urllib.parse
    for candidate in [inputTarget, targetDir]:
        if not candidate:
            continue
            
        # 1. Handle HTTP/HTTPS URLs (including ones with embedded oauth2 tokens/passwords)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            try:
                parsed = urllib.parse.urlparse(candidate)
                hostname = parsed.hostname
                if hostname and "gitlab" in hostname.lower():
                    path = parsed.path.lstrip("/")
                    if path.endswith(".git"):
                        path = path[:-4]
                    name = path.split("/")[-1]
                    return hostname, path, name
            except Exception:
                pass
                
        # 2. Handle SSH and other Git URL formats (e.g. git@gitlab.com:org/repo.git)
        else:
            if "gitlab" in candidate.lower():
                if "@" in candidate:
                    parts = candidate.split("@", 1)[1]
                else:
                    parts = candidate
                
                m = re.split(r'[:/]', parts, 1)
                if len(m) == 2:
                    domain = m[0]
                    path = m[1]
                    if path.endswith(".git"):
                        path = path[:-4]
                    name = path.split("/")[-1]
                    return domain, path, name

    # Check git config remote origin
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            return extractGitLabRepoInfo(targetDir, remote_url)
    return None


def callGitLabApi(url: str, headers: dict, params: dict = None) -> Optional[Any]:
    """
    Executes a GitLab API request with rate limit handling and retries.
    """
    import requests
    retries = 0
    max_retries = 5
    backoff = 2
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (backoff ** retries)
                logger.warning(f"GitLab API rate limit hit. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"GitLab API server error ({response.status_code}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            else:
                logger.error(f"GitLab API request failed with status code {response.status_code}: {response.text}")
                return None
        except requests.RequestException as e:
            sleep_time = backoff ** retries
            logger.warning(f"Network error during GitLab API call: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    return None


def fetchGitLabMergeRequests(domain: str, project_path: str, token: Optional[str]) -> List[Dict[str, Any]]:
    """
    Fetch all Merge Requests for the repository using GitLab API, paginating.
    """
    import urllib.parse
    mr_list = []
    logger.info("Fetching GitLab Merge Requests...")

    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded_path = urllib.parse.quote_plus(project_path)
    url = f"https://{domain}/api/v4/projects/{encoded_path}/merge_requests"

    params = {
        "state": "all",
        "per_page": 100,
        "page": 1
    }

    while True:
        logger.info(f"Fetching GitLab MRs page {params['page']}...")
        response = callGitLabApi(url, headers, params)
        if not response:
            break

        data = response.json()
        if not data:
            break

        for mr in data:
            state = mr.get("state")
            lifecycle_status = "open" if state == "opened" else ("merged" if state == "merged" else "closed")
            merged_at = mr.get("merged_at")

            mr_dict = dict(mr)
            mr_dict.update({
                "provider": "gitlab",
                "provider_state": state,
                "lifecycle_status": lifecycle_status,
                "number": mr.get("iid"),
                "title": mr.get("title"),
                "body": mr.get("description"),
                "state": "open" if lifecycle_status == "open" else "closed",
                "created_at": mr.get("created_at"),
                "updated_at": mr.get("updated_at"),
                "closed_at": mr.get("closed_at"),
                "merged_at": merged_at,
                "user": {"login": mr.get("author", {}).get("username")} if mr.get("author") else None,
                "html_url": mr.get("web_url"),
                "additions": None,
                "deletions": None,
                "changed_files": None,
                "comments": None,
                "review_comments": None,
                "commits": None,
                "head_sha": mr.get("sha"),
                "merge_commit_sha": mr.get("merge_commit_sha"),
                "squash_commit_sha": mr.get("squash_commit_sha"),
                "source_branch": mr.get("source_branch"),
                "target_branch": mr.get("target_branch"),
            })
            mr_list.append(mr_dict)

        next_page = response.headers.get("X-Next-Page")
        if next_page and next_page.strip():
            params["page"] = int(next_page)
        elif len(data) < params["per_page"]:
            break
        else:
            params["page"] += 1

    return mr_list


def runGitLabMrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    GitLab MR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "total_pr_comments_count": 0,
        "open_pr_comments_count": 0,
        "closed_pr_comments_count": 0,
        "merged_pr_comments_count": 0,
        "total_pr_review_comment_count": 0,
        "open_pr_review_comment_count": 0,
        "closed_pr_review_comment_count": 0,
        "merged_pr_review_comment_count": 0,
        "github_pr_analysis_available": False,
        "pr_content_tokens": _emptyPrTokenMetrics(),
        "pr_content_tokens_lexical": _emptyPrTokenMetrics(),
    }

    env_token = os.getenv("GITLAB_TOKEN") or os.getenv("GL_TOKEN")
    final_token = env_token or token

    info = extractGitLabRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a GitLab repository or remote URL. Skipping MR analysis.")
        return defaults

    domain, project_path, repo_name = info

    try:
        prs = fetchGitLabMergeRequests(domain, project_path, final_token)

        metrics = calculatePrMetrics(prs)
        metrics["github_pr_analysis_available"] = True
        metrics["_merge_commit_identities"] = buildMergeCommitIdentities(prs)
        metrics.update(enrichGitLabPrContentTokens(
            prs, domain, project_path, final_token
        ))
        metrics.update(calculatePrCommentMetrics(prs))
        metrics.update(extractGitLabIssuesAndCi(
            domain, project_path, final_token, outputDir, repo_name
        ))

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"GitLab MR analysis failed: {e}. Falling back to default metrics.")
        return defaults


def enrichViaGitLabApi(
    domain: str,
    project_path: str,
    token: Optional[str],
) -> Dict[str, Any]:
    """
    Optional GitLab API enrichment.
    """
    import urllib.parse
    headers = {}
    if token:
        headers["PRIVATE-TOKEN"] = token

    encoded_path = urllib.parse.quote_plus(project_path)
    url = f"https://{domain}/api/v4/projects/{encoded_path}"

    response = callGitLabApi(url, headers)
    if not response:
        return {"available": False, "reason": "Request failed"}

    try:
        project = response.json()
        
        created_at_str = "N/A"
        if project.get("created_at"):
            try:
                created_at_str = project.get("created_at").split("T")[0]
            except Exception:
                pass
                
        pushed_at_str = "N/A"
        if project.get("last_activity_at"):
            try:
                pushed_at_str = project.get("last_activity_at").split("T")[0]
            except Exception:
                pass

        license_info = project.get("license")
        license_spdx = license_info.get("spdx_id") if license_info else None

        size_bytes = project.get("statistics", {}).get("repository_size", 0)
        size_kb = size_bytes // 1024 if size_bytes else 0

        return {
            "available":       True,
            "full_name":       project.get("path_with_namespace"),
            "owner":           project.get("namespace", {}).get("path"),
            "description":     project.get("description"),
            "is_private":      project.get("visibility") == "private",
            "is_fork":         project.get("forked_from_project") is not None,
            "stars":           project.get("star_count", 0),
            "forks":           project.get("forks_count", 0),
            "open_issues":     project.get("open_issues_count", 0),
            "topics":          project.get("topics", project.get("tag_list", [])),
            "default_branch":  project.get("default_branch"),
            "created_at":      created_at_str,
            "pushed_at":       pushed_at_str,
            "size_kb":         size_kb,
            "license":         license_spdx,
            "language":        None,
            "subscribers":     None,
            "visibility":      project.get("visibility"),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

def extractBitbucketRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str, str, bool]]:
    """
    Detect Bitbucket domain, workspace/project, and repo slug from inputTarget URL
    or local git configuration.
    Returns: (domain, workspace_or_project, repo_slug, is_cloud)
    """
    import urllib.parse

    for candidate in [inputTarget, targetDir]:
        if not candidate:
            continue

        # 1. Handle HTTP/HTTPS URLs (including ones with embedded tokens/passwords)
        if candidate.startswith("http://") or candidate.startswith("https://"):
            try:
                parsed = urllib.parse.urlparse(candidate)
                hostname = parsed.hostname
                if hostname and "bitbucket" in hostname.lower():
                    is_cloud = hostname.lower() == "bitbucket.org"
                    path = parsed.path.strip("/")
                    if path.endswith(".git"):
                        path = path[:-4]

                    # Bitbucket Server/Data Center paths look like:
                    #   scm/PROJECT/repo.git
                    #   projects/PROJECT/repos/repo/browse
                    path = re.sub(r"^scm/", "", path)
                    path = re.sub(r"^projects/([^/]+)/repos/([^/]+).*$", r"\1/\2", path)

                    parts = path.split("/")
                    if len(parts) >= 2:
                        workspace = parts[0]
                        repo = parts[1]
                        return hostname, workspace, repo, is_cloud
            except Exception:
                pass

        # 2. Handle SSH and other Git URL formats
        #    Cloud:  git@bitbucket.org:workspace/repo.git
        #    Server: ssh://git@bitbucket.company.com:7999/PROJECT/repo.git
        else:
            if "bitbucket" in candidate.lower():
                try:
                    stripped = candidate
                    if stripped.startswith("ssh://"):
                        stripped = stripped[len("ssh://"):]

                    if "@" in stripped:
                        host_and_path = stripped.split("@", 1)[1]
                    else:
                        host_and_path = stripped

                    # host_and_path is one of:
                    #   bitbucket.org:workspace/repo.git
                    #   bitbucket.company.com:7999/PROJECT/repo.git
                    m = re.split(r'[:/]', host_and_path, 1)
                    if len(m) == 2:
                        domain = m[0]
                        rest = m[1]

                        # Strip a leading port number if present (server SSH URLs)
                        rest = re.sub(r'^\d+/', '', rest)

                        if rest.endswith(".git"):
                            rest = rest[:-4]

                        rest = re.sub(r"^scm/", "", rest)

                        parts = rest.split("/")
                        if len(parts) >= 2:
                            is_cloud = domain.lower() == "bitbucket.org"
                            workspace = parts[0]
                            repo = parts[1]
                            return domain, workspace, repo, is_cloud
                except Exception:
                    pass

    # 3. Check git config remote origin as a fallback
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            return extractBitbucketRepoInfo(targetDir, remote_url)

    return None

def _bitbucketAuthHeaders(token: Optional[str], is_cloud: bool) -> dict:
    """
    Builds auth headers for Bitbucket API calls.
    Cloud: Bearer token (Repository/Workspace access token) works for both
           read scopes; falls back to no auth for public repos.
    Server/Data Center: Bearer token (Personal Access Token).
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def callBitbucketApi(url: str, headers: dict, params: dict = None) -> Optional[Any]:
    """
    Executes a Bitbucket API request with rate limit handling and retries.
    """
    import requests
    retries = 0
    max_retries = 5
    backoff = 2
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_time = int(retry_after) if retry_after and retry_after.isdigit() else (backoff ** retries)
                logger.warning(f"Bitbucket API rate limit hit. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"Bitbucket API server error ({response.status_code}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif response.status_code in [401, 403]:
                logger.error(f"Bitbucket API auth failed ({response.status_code}): {response.text}")
                return None
            else:
                logger.error(f"Bitbucket API request failed with status code {response.status_code}: {response.text}")
                return None
        except requests.RequestException as e:
            sleep_time = backoff ** retries
            logger.warning(f"Network error during Bitbucket API call: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    return None


def enrichViaBitbucketApi(
    domain: str,
    workspace: str,
    repo: str,
    token: Optional[str],
    is_cloud: bool,
) -> Dict[str, Any]:
    """
    Optional Bitbucket API enrichment. Branches between Bitbucket Cloud (API v2.0)
    and Bitbucket Server/Data Center (REST API 1.0), which have different
    endpoints, auth, and response shapes.
    """
    import urllib.parse
    headers = _bitbucketAuthHeaders(token, is_cloud)

    if is_cloud:
        encoded_workspace = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://api.bitbucket.org/2.0/repositories/{encoded_workspace}/{encoded_repo}"

        response = callBitbucketApi(url, headers)
        if not response:
            return {"available": False, "reason": "Request failed"}

        try:
            data = response.json()

            created_at_str = "N/A"
            if data.get("created_on"):
                try:
                    created_at_str = data.get("created_on").split("T")[0]
                except Exception:
                    pass

            pushed_at_str = "N/A"
            if data.get("updated_on"):
                try:
                    pushed_at_str = data.get("updated_on").split("T")[0]
                except Exception:
                    pass

            size_bytes = data.get("size", 0)
            size_kb = size_bytes // 1024 if size_bytes else 0

            return {
                "available":       True,
                "full_name":       data.get("full_name"),
                "owner":           workspace,
                "description":     data.get("description"),
                "is_private":      data.get("is_private", False),
                "is_fork":         data.get("parent") is not None,
                "stars":           None,   # Bitbucket Cloud has no star count
                "forks":           None,   # requires separate /forks endpoint
                "open_issues":     None,   # requires separate /issues endpoint
                "topics":          [],     # Bitbucket has no topics/tags concept
                "default_branch":  (data.get("mainbranch") or {}).get("name"),
                "created_at":      created_at_str,
                "pushed_at":       pushed_at_str,
                "size_kb":         size_kb,
                "license":         None,   # not exposed by the repo endpoint
                "language":        data.get("language"),
                "subscribers":     None,
                "visibility":      "private" if data.get("is_private") else "public",
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}

    else:
        # Bitbucket Server / Data Center — REST API 1.0
        encoded_project = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://{domain}/rest/api/1.0/projects/{encoded_project}/repos/{encoded_repo}"

        response = callBitbucketApi(url, headers)
        if not response:
            return {"available": False, "reason": "Request failed"}

        try:
            data = response.json()

            default_branch = None
            try:
                branch_url = f"{url}/default-branch"
                branch_resp = callBitbucketApi(branch_url, headers)
                if branch_resp:
                    default_branch = branch_resp.json().get("displayId")
            except Exception:
                pass

            is_public = data.get("public", False)

            return {
                "available":       True,
                "full_name":       f"{workspace}/{data.get('slug', repo)}",
                "owner":           workspace,
                "description":     data.get("description"),
                "is_private":      not is_public,
                "is_fork":         data.get("origin") is not None,
                "stars":           None,
                "forks":           None,
                "open_issues":     None,
                "topics":          [],
                "default_branch":  default_branch,
                "created_at":      "N/A",   # not exposed by Server API
                "pushed_at":       "N/A",
                "size_kb":         None,    # requires separate size endpoint
                "license":         None,
                "language":        None,
                "subscribers":     None,
                "visibility":      "public" if is_public else "private",
            }
        except Exception as exc:
            return {"available": False, "reason": str(exc)}


def fetchBitbucketPullRequests(
    domain: str,
    workspace: str,
    repo: str,
    token: Optional[str],
    is_cloud: bool,
) -> List[Dict[str, Any]]:
    """
    Fetch all Pull Requests for the repository using the Bitbucket API, paginating.
    Cloud and Server differ significantly in pagination and state filtering,
    so this branches on is_cloud.
    """
    import urllib.parse
    pr_list = []
    headers = _bitbucketAuthHeaders(token, is_cloud)
    logger.info("Fetching Bitbucket Pull Requests...")

    if is_cloud:
        encoded_workspace = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        base_url = f"https://api.bitbucket.org/2.0/repositories/{encoded_workspace}/{encoded_repo}/pullrequests"

        # Bitbucket Cloud doesn't support state=ALL in one call — must query
        # each state separately and merge the results.
        for state in ("OPEN", "MERGED", "DECLINED", "SUPERSEDED"):
            url = base_url
            params = {"state": state, "pagelen": 50}

            while url:
                response = callBitbucketApi(url, headers, params)
                if not response:
                    break

                data = response.json()
                values = data.get("values", [])
                if not values:
                    break

                for pr in values:
                    provider_state = pr.get("state")
                    lifecycle_status = "open" if provider_state == "OPEN" else ("merged" if provider_state == "MERGED" else "closed")

                    merged_at = pr.get("updated_on") if pr.get("state") == "MERGED" else None

                    pr_dict = dict(pr)
                    pr_dict.update({
                        "provider": "bitbucket",
                        "provider_state": provider_state,
                        "lifecycle_status": lifecycle_status,
                        "number": pr.get("id"),
                        "title": pr.get("title"),
                        "body": pr.get("description"),
                        "state": "open" if lifecycle_status == "open" else "closed",
                        "created_at": pr.get("created_on"),
                        "updated_at": pr.get("updated_on"),
                        "closed_at": pr.get("updated_on") if pr.get("state") in ("MERGED", "DECLINED") else None,
                        "merged_at": merged_at,
                        "user": {"login": (pr.get("author") or {}).get("nickname")} if pr.get("author") else None,
                        "html_url": (pr.get("links", {}).get("html") or {}).get("href"),
                        "additions": None,
                        "deletions": None,
                        "changed_files": None,
                        "comments": pr.get("comment_count"),
                        "review_comments": None,
                        "commits": None,
                        "head_sha": (pr.get("source") or {}).get("commit", {}).get("hash"),
                        "base_sha": (pr.get("destination") or {}).get("commit", {}).get("hash"),
                        "source_branch": (pr.get("source") or {}).get("branch", {}).get("name"),
                        "target_branch": (pr.get("destination") or {}).get("branch", {}).get("name"),
                    })
                    pr_list.append(pr_dict)

                # Cloud pagination gives a full "next" URL directly
                url = data.get("next")
                params = None  # already encoded in the next URL

    else:
        # Bitbucket Server / Data Center — REST API 1.0, start/limit pagination
        encoded_project = urllib.parse.quote(workspace, safe="")
        encoded_repo = urllib.parse.quote(repo, safe="")
        url = f"https://{domain}/rest/api/1.0/projects/{encoded_project}/repos/{encoded_repo}/pull-requests"

        start = 0
        limit = 100
        while True:
            params = {"state": "ALL", "start": start, "limit": limit}
            response = callBitbucketApi(url, headers, params)
            if not response:
                break

            data = response.json()
            values = data.get("values", [])
            if not values:
                break

            for pr in values:
                state = pr.get("state")  # OPEN, MERGED, DECLINED
                lifecycle_status = "open" if state == "OPEN" else ("merged" if state == "MERGED" else "closed")

                created_ms = pr.get("createdDate")
                updated_ms = pr.get("updatedDate")
                closed_ms = pr.get("closedDate")

                def _ms_to_iso(ms):
                    if not ms:
                        return None
                    try:
                        return datetime.datetime.utcfromtimestamp(ms / 1000).isoformat() + "Z"
                    except Exception:
                        return None

                pr_dict = dict(pr)
                from_ref = pr.get("fromRef") or {}
                to_ref = pr.get("toRef") or {}
                pr_dict.update({
                    "provider": "bitbucket",
                    "provider_state": state,
                    "lifecycle_status": lifecycle_status,
                    "number": pr.get("id"),
                    "title": pr.get("title"),
                    "body": pr.get("description"),
                    "state": "open" if lifecycle_status == "open" else "closed",
                    "created_at": _ms_to_iso(created_ms),
                    "updated_at": _ms_to_iso(updated_ms),
                    "closed_at": _ms_to_iso(closed_ms) if state in ("MERGED", "DECLINED") else None,
                    "merged_at": _ms_to_iso(closed_ms) if state == "MERGED" else None,
                    "user": {"login": (pr.get("author", {}).get("user") or {}).get("name")} if pr.get("author") else None,
                    "html_url": next(
                        (l.get("href") for l in pr.get("links", {}).get("self", [])), None
                    ) if pr.get("links") else None,
                    "additions": None,
                    "deletions": None,
                    "changed_files": None,
                    "comments": None,
                    "review_comments": None,
                    "commits": None,
                    "head_sha": from_ref.get("latestCommit") or from_ref.get("latestChangeset"),
                    "base_sha": to_ref.get("latestCommit") or to_ref.get("latestChangeset"),
                    "source_branch": from_ref.get("displayId"),
                    "target_branch": to_ref.get("displayId"),
                })
                pr_list.append(pr_dict)

            if data.get("isLastPage", True):
                break
            start = data.get("nextPageStart", start + limit)

    return pr_list


def runBitbucketPrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    Bitbucket PR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "total_pr_comments_count": 0,
        "open_pr_comments_count": 0,
        "closed_pr_comments_count": 0,
        "merged_pr_comments_count": 0,
        "total_pr_review_comment_count": 0,
        "open_pr_review_comment_count": 0,
        "closed_pr_review_comment_count": 0,
        "merged_pr_review_comment_count": 0,
        "github_pr_analysis_available": False,
        "pr_content_tokens": _emptyPrTokenMetrics(),
        "pr_content_tokens_lexical": _emptyPrTokenMetrics(),
    }

    env_token = os.getenv("BITBUCKET_TOKEN") or os.getenv("BB_TOKEN")
    final_token = env_token or token

    info = extractBitbucketRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a Bitbucket repository or remote URL. Skipping PR analysis.")
        return defaults

    domain, workspace, repo_name, is_cloud = info

    if not final_token:
        logger.warning(
            "Bitbucket token missing. Please set BITBUCKET_TOKEN or BB_TOKEN env variables "
            "(or pass --bitbucket-token) to enable Bitbucket PR analytics."
        )
        return defaults

    try:
        prs = fetchBitbucketPullRequests(domain, workspace, repo_name, final_token, is_cloud)

        metrics = calculatePrMetrics(prs)
        metrics["github_pr_analysis_available"] = True
        metrics["_merge_commit_identities"] = buildMergeCommitIdentities(prs)
        metrics.update(enrichBitbucketPrContentTokens(
            prs, domain, workspace, repo_name, final_token, is_cloud
        ))
        metrics.update(calculatePrCommentMetrics(prs))
        metrics.update(extractBitbucketIssuesAndCi(
            domain, workspace, repo_name, final_token, is_cloud, prs, outputDir
        ))

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"Bitbucket PR analysis failed: {e}. Falling back to default metrics.")
        return defaults

def extractGitHubRepoInfo(targetDir: str, inputTarget: str) -> Optional[Tuple[str, str]]:
    """
    Detect GitHub repository owner and name from inputTarget URL or local git configuration.
    """
    for candidate in [inputTarget, targetDir]:
        if candidate:
            m = re.search(r"github\.com[/:]([^/]+)/([^/]+)", candidate)
            if m:
                owner = m.group(1)
                repo = m.group(2)
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return owner, repo

    # If not found directly, check git config remote origin
    if os.path.isdir(os.path.join(targetDir, ".git")):
        remote_url = runGit(["config", "--get", "remote.origin.url"], targetDir)
        if remote_url:
            m = re.search(r"github\.com[/:]([^/]+)/([^/]+)", remote_url)
            if m:
                owner = m.group(1)
                repo = m.group(2)
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return owner, repo
    return None


def getCoreRateLimit(g: Github):
    """
    Safely get the core rate limit object from Github instance,
    supporting different PyGithub versions.
    """
    try:
        rate_limit = g.get_rate_limit()
        if hasattr(rate_limit, "resources") and hasattr(rate_limit.resources, "core"):
            return rate_limit.resources.core
        elif hasattr(rate_limit, "core"):
            return rate_limit.core
        elif hasattr(rate_limit, "rate"):
            return rate_limit.rate
    except Exception as e:
        logger.warning(f"Failed to get rate limit object: {e}")
    return None


def handleRateLimit(g: Github, minimum_remaining: int = 10):
    """
    Checks the current GitHub rate limit and sleeps if it is close to exhausted.
    """
    core = getCoreRateLimit(g)
    if core is None:
        return
    try:
        if core.remaining < minimum_remaining:
            reset_val = core.reset
            if hasattr(reset_val, "timestamp"):
                reset_time = reset_val.timestamp()
            else:
                reset_time = float(reset_val)
            now = time.time()
            sleep_duration = max(0.0, reset_time - now) + 5
            logger.warning(
                f"GitHub API Rate limit low ({core.remaining}/{core.limit}). "
                f"Sleeping for {sleep_duration:.1f} seconds until reset..."
            )
            time.sleep(sleep_duration)
    except Exception as e:
        logger.warning(f"Failed to check rate limit: {e}")


def callWithRetry(g: Github, func, *args, max_retries=5, **kwargs):
    """
    Executes a PyGithub function with rate limit checks and retries.
    """
    retries = 0
    backoff = 2
    while retries < max_retries:
        try:
            handleRateLimit(g)
            return func(*args, **kwargs)
        except RateLimitExceededException:
            logger.warning("Rate limit exceeded during GitHub API call. Checking/sleeping for reset...")
            handleRateLimit(g)
            retries += 1
        except GithubException as e:
            if getattr(e, "status", None) == 403 and "abuse" in str(e).lower():
                sleep_time = backoff ** retries
                logger.warning(f"GitHub abuse detection triggered. Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            elif getattr(e, "status", None) in [500, 502, 503, 504]:
                sleep_time = backoff ** retries
                logger.warning(f"GitHub API server error ({e.status}). Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                retries += 1
            else:
                raise e
        except Exception as e:
            sleep_time = backoff ** retries
            logger.warning(f"Unexpected GitHub API error: {e}. Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            retries += 1
    raise Exception(f"Failed after {max_retries} retries")


def fetchPullRequests(g: Github, repo) -> List[Dict[str, Any]]:
    """
    Fetch all PRs for the repository using PyGithub, paginating and handling rate limits.
    """
    pr_list = []
    logger.info("Fetching GitHub Pull Requests...")

    pulls = repo.get_pulls(state="all")

    core = getCoreRateLimit(g)
    if core is not None:
        remaining = core.remaining
    else:
        remaining = 5000  # Default fallback

    logger.info(f"Initial GitHub API rate limit: {remaining} remaining")

    count = 0
    for pr in pulls:
        count += 1
        if count % 100 == 0:
            logger.info(f"Fetched {count} PR metadata entries...")

        pr_dict = {}
        try:
            pr_dict = dict(pr._raw_data) if hasattr(pr, "_raw_data") and pr._raw_data else {}
        except Exception:
            pass

        pr_dict.update({
            "provider": "github",
            "provider_state": pr.state,
            "lifecycle_status": "merged" if pr.merged_at else pr.state,
            "number": pr.number,
            "title": pr.title,
            "body": pr.body,
            "state": pr.state,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "user": {"login": pr.user.login} if pr.user else None,
            "html_url": pr.html_url,
            "head_sha": getattr(getattr(pr, "head", None), "sha", None),
            "base_sha": getattr(getattr(pr, "base", None), "sha", None),
            "merge_commit_sha": getattr(pr, "merge_commit_sha", None),
            "source_branch": getattr(getattr(pr, "head", None), "ref", None),
            "target_branch": getattr(getattr(pr, "base", None), "ref", None),
        })

        # Avoid one extra API request per PR here. Content enrichment below
        # fetches only the data needed for token counting, concurrently.
        pr_dict.update({
            "additions": pr_dict.get("additions"),
            "deletions": pr_dict.get("deletions"),
            "changed_files": pr_dict.get("changed_files"),
            "comments": pr_dict.get("comments"),
            "review_comments": pr_dict.get("review_comments"),
            "commits": pr_dict.get("commits"),
        })

        pr_list.append(pr_dict)

    return pr_list


def calculate_pr_velocity_profile(prs):
    """Calculate 1-week and 1-month PR velocity percentages and blacklist status.

    Only merged PRs are considered, both for the total (denominator) and for
    the recent-activity counts. Recency is measured from each PR's merged_at
    timestamp, since that's when it actually landed in the codebase.
    """
    merged_prs = [pr for pr in prs if getPrLifecycleStatus(pr) == "merged"]

    if not merged_prs:
        return "1w:0.00% / 1m:0.00%", "OK", 0.0, 0.0

    total = len(merged_prs)
    now = datetime.datetime.now(datetime.timezone.utc)
    week_ago = now - datetime.timedelta(days=7)
    month_ago = now - datetime.timedelta(days=30)

    week_count = 0
    month_count = 0

    for pr in merged_prs:
        merged_str = pr.get("merged_at")
        if not merged_str:
            continue
        try:
            merged_str = str(merged_str).replace("Z", "+00:00")
            merged = datetime.datetime.fromisoformat(merged_str)
            if merged.tzinfo is None:
                merged = merged.replace(tzinfo=datetime.timezone.utc)
            if merged >= week_ago:
                week_count += 1
            if merged >= month_ago:
                month_count += 1
        except Exception:
            continue

    week_pct = (week_count / total) * 100
    month_pct = (month_count / total) * 100

    profile = f"1w:{week_pct:.2f}% / 1m:{month_pct:.2f}%"
    status = "BLACKLISTED" if week_pct >= 30 or month_pct >= 30 else "OK"

    return profile, status, week_pct, month_pct


def calculatePrMetrics(prs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate PR counts based on the retrieved PR list.
    """
    total_pr_count = len(prs)
    open_pr_count = 0
    closed_pr_count = 0
    merged_pr_count = 0

    for pr in prs:
        status = getPrLifecycleStatus(pr)

        if status == "open":
            open_pr_count += 1
        elif status == "merged":
            merged_pr_count += 1
        elif status == "closed":
            closed_pr_count += 1

    pr_velocity_profile, blacklist_status, _, _ = calculate_pr_velocity_profile(prs)

    return {
        "total_pr_count": total_pr_count,
        "open_pr_count": open_pr_count,
        "closed_pr_count": closed_pr_count,
        "merged_pr_count": merged_pr_count,
        "github_pr_analysis_available": True,
        "pr_velocity_profile": pr_velocity_profile,
        "blacklist_status": blacklist_status,
    }


def buildMergeCommitIdentities(prs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build SHA/PR-number lookups for provider usernames on merge commits."""
    bySha = {}
    byNumber = {}
    for pr in prs:
        user = pr.get("user") or {}
        username = user.get("login") or user.get("username") or user.get("name")
        number = pr.get("number")
        if not username:
            continue
        identity = {"username": username, "pr_number": number,
                    "provider": pr.get("provider")}
        if number is not None:
            byNumber[str(number)] = identity
        rawMergeCommit = pr.get("merge_commit") or {}
        candidateShas = (
            pr.get("merge_commit_sha"), pr.get("squash_commit_sha"),
            rawMergeCommit.get("hash") if isinstance(rawMergeCommit, dict) else None,
        )
        for sha in candidateShas:
            if sha:
                bySha[str(sha).lower()] = identity
    return {"by_sha": bySha, "by_number": byNumber}


def attachMergeCommitUsernames(
    commits: List[Dict[str, Any]], identities: Dict[str, Dict[str, Any]]
) -> None:
    """Attach API username without replacing immutable Git author metadata."""
    bySha = identities.get("by_sha", {})
    byNumber = identities.get("by_number", {})
    prPattern = re.compile(r"\b(?:pull request|merge request)\s*[#!](\d+)\b", re.IGNORECASE)
    githubBranchPattern = re.compile(
        r"\bmerge\s+pull\s+request\s+#(\d+)\s+from\s+([^/\s]+)/",
        re.IGNORECASE,
    )
    for commit in commits:
        identity = bySha.get(str(commit.get("hash") or "").lower())
        source = "merge_commit_sha" if identity else None
        if not identity:
            match = prPattern.search(commit.get("message") or "")
            if match:
                identity = byNumber.get(match.group(1))
                source = "merge_message_pr_number" if identity else None
        if not identity:
            match = githubBranchPattern.search(commit.get("message") or "")
            if match:
                identity = {"username": match.group(2), "pr_number": int(match.group(1))}
                source = "merge_message_branch_owner"
        commit["username"] = identity.get("username", "") if identity else ""
        commit["pr_number"] = identity.get("pr_number", "") if identity else ""
        commit["identity_source"] = source or "git_author_only"


def normalizeCommitAuthorsToUsernames(commits: List[Dict[str, Any]]) -> None:
    """
    Make ``author`` a username consistently using aliases observed for the same
    Git email. Preserve the original Git display name in ``author_name``.
    """
    usernamePattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    candidatesByEmail = defaultdict(lambda: defaultdict(int))

    for commit in commits:
        originalAuthor = str(commit.get("author") or "").strip()
        email = str(commit.get("email") or "").strip().lower()
        if email and usernamePattern.fullmatch(originalAuthor):
            candidatesByEmail[email][originalAuthor] += 1

    canonicalByEmail = {}
    for email, candidates in candidatesByEmail.items():
        canonicalByEmail[email] = max(candidates.items(), key=lambda item: (item[1], item[0]))[0]

    for commit in commits:
        originalAuthor = str(commit.get("author") or "").strip()
        email = str(commit.get("email") or "").strip().lower()
        commit["author_name"] = originalAuthor
        resolvedUsername = canonicalByEmail.get(email)
        if not resolvedUsername and usernamePattern.fullmatch(originalAuthor):
            resolvedUsername = originalAuthor
        # A PR author's username is a different identity and must not be used
        # as the merge commit author's username.
        commit["pr_author_username"] = commit.pop("username", "")
        commit["author"] = resolvedUsername or ""
        commit["author_identity_source"] = (
            "git_email_alias" if email in canonicalByEmail else
            "git_author_username" if resolvedUsername else
            "unresolved"
        )

def _emptyPrTokenMetrics() -> Dict[str, int]:
    return {"total": 0, "titles_bodies": 0, "discussion": 0, "commit_messages": 0}


def countPrMetadataTokens(prs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count provider-supplied PR title/body text without additional requests."""
    llm = _emptyPrTokenMetrics()
    lexical = _emptyPrTokenMetrics()
    for pr in prs:
        text_value = "\n".join(filter(None, (pr.get("title"), pr.get("body"))))
        pr["llm_token_count"] = getLlmTokens(text_value)
        pr["lexical_token_count"] = len(countLexicalTokens(text_value))
        llm["titles_bodies"] += pr["llm_token_count"]
        lexical["titles_bodies"] += pr["lexical_token_count"]
    llm["total"] = llm["titles_bodies"]
    lexical["total"] = lexical["titles_bodies"]
    return {"pr_content_tokens": llm, "pr_content_tokens_lexical": lexical,
            "prs_detailed_count": 0}


def _addPrContentTokens(
    prs: List[Dict[str, Any]],
    detailsByNumber: Dict[Any, Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach fetched provider content and count it once for both tokenizers."""
    llm = _emptyPrTokenMetrics()
    lexical = _emptyPrTokenMetrics()
    detailedCount = 0

    for pr in prs:
        titleBody = "\n".join(filter(None, (pr.get("title"), pr.get("body"))))
        pr["llm_token_count"] = getLlmTokens(titleBody)
        pr["lexical_token_count"] = len(countLexicalTokens(titleBody))
        llm["titles_bodies"] += pr["llm_token_count"]
        lexical["titles_bodies"] += pr["lexical_token_count"]

        details = detailsByNumber.get(pr.get("number"))
        if not details:
            continue

        issueComments = details.get("comments", [])
        reviewComments = details.get("review_comments", [])
        commits = details.get("commit_messages", [])
        pr["comment_details"] = issueComments
        pr["review_comment_details"] = reviewComments
        pr["review_details"] = details.get("reviews", [])
        pr["file_details"] = details.get("files", [])
        pr["commit_message_details"] = commits
        pr["comments"] = len(issueComments)
        pr["review_comments"] = len(reviewComments)
        pr["commits"] = len(commits)
        pr["additions"] = details.get("additions")
        pr["deletions"] = details.get("deletions")
        pr["changed_files"] = details.get("changed_files")

        discussionText = "\n".join(
            item.get("body", "") for item in issueComments + reviewComments if item.get("body")
        )
        commitText = "\n".join(item.get("message", "") for item in commits if item.get("message"))
        discussionLlm = getLlmTokens(discussionText)
        commitLlm = getLlmTokens(commitText)
        discussionLexical = len(countLexicalTokens(discussionText))
        commitLexical = len(countLexicalTokens(commitText))
        pr["llm_token_count"] += discussionLlm + commitLlm
        pr["lexical_token_count"] += discussionLexical + commitLexical
        llm["discussion"] += discussionLlm
        llm["commit_messages"] += commitLlm
        lexical["discussion"] += discussionLexical
        lexical["commit_messages"] += commitLexical
        detailedCount += 1

    llm["total"] = llm["titles_bodies"] + llm["discussion"] + llm["commit_messages"]
    lexical["total"] = lexical["titles_bodies"] + lexical["discussion"] + lexical["commit_messages"]
    return {"pr_content_tokens": llm, "pr_content_tokens_lexical": lexical,
            "prs_detailed_count": detailedCount}


def countUnifiedDiffChanges(diffText: str) -> Tuple[int, int]:
    """Count added/removed content lines while excluding diff file headers."""
    additions = 0
    deletions = 0
    for line in (diffText or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def enrichGitLabPrContentTokens(
    prs: List[Dict[str, Any]], domain: str, projectPath: str, token: Optional[str]
) -> Dict[str, Any]:
    """Fetch GitLab MR discussions and commit messages with bounded parallel I/O."""
    import urllib.parse
    headers = {"PRIVATE-TOKEN": token} if token else {}
    encodedProject = urllib.parse.quote_plus(projectPath)

    def fetchPaged(url: str) -> List[Dict[str, Any]]:
        items = []
        page = 1
        while True:
            response = callGitLabApi(url, headers, {"per_page": 100, "page": page})
            if not response:
                break
            data = response.json()
            if not isinstance(data, list):
                break
            items.extend(data)
            nextPage = response.headers.get("X-Next-Page")
            if not nextPage:
                break
            page = int(nextPage)
        return items

    def fetchOne(number: int) -> Tuple[int, Dict[str, Any]]:
        base = f"https://{domain}/api/v4/projects/{encodedProject}/merge_requests/{number}"
        discussions = fetchPaged(f"{base}/discussions")
        commitsRaw = fetchPaged(f"{base}/commits")
        additions = deletions = changedFiles = None
        changesResponse = callGitLabApi(f"{base}/changes", headers)
        if changesResponse:
            changes = changesResponse.json().get("changes", [])
            additions = deletions = 0
            changedFiles = len(changes)
            for change in changes:
                added, removed = countUnifiedDiffChanges(change.get("diff") or "")
                additions += added
                deletions += removed
        comments, reviewComments = [], []
        seenNoteIds = set()
        for discussion in discussions:
            for note in discussion.get("notes", []):
                noteId = note.get("id")
                if noteId in seenNoteIds or note.get("system"):
                    continue
                seenNoteIds.add(noteId)
                author = note.get("author") or {}
                entry = {
                    "id": noteId,
                    "author": author.get("username"),
                    "body": note.get("body") or "",
                    "created_at": note.get("created_at"),
                    "updated_at": note.get("updated_at"),
                    "resolved": note.get("resolved"),
                    "resolvable": note.get("resolvable"),
                    "position": note.get("position"),
                }
                (reviewComments if note.get("position") else comments).append(entry)
        commits = [{
            "id": item.get("id"),
            "short_id": item.get("short_id"),
            "author": item.get("author_name"),
            "message": item.get("message") or item.get("title") or "",
            "created_at": item.get("created_at"),
        } for item in commitsRaw]
        fileDetails = [{
            "filename": item.get("new_path"),
            "previous_filename": item.get("old_path"),
            "status": "renamed" if item.get("renamed_file") else
                      "added" if item.get("new_file") else
                      "deleted" if item.get("deleted_file") else "modified",
            "patch": item.get("diff"),
            "new_file": item.get("new_file"),
            "deleted_file": item.get("deleted_file"),
            "renamed_file": item.get("renamed_file"),
        } for item in (changes if changesResponse else [])]
        return number, {"comments": comments, "review_comments": reviewComments,
                        "commit_messages": commits, "additions": additions,
                        "deletions": deletions, "changed_files": changedFiles,
                        "files": fileDetails}

    details = {}
    numbers = [pr.get("number") for pr in prs if pr.get("number") is not None]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(numbers)))) as executor:
        futures = {executor.submit(fetchOne, number): number for number in numbers}
        for future in as_completed(futures):
            number = futures[future]
            try:
                _, details[number] = future.result()
            except Exception as exc:
                logger.warning(f"[PR tokens] GitLab MR !{number} details skipped: {exc}")
    return _addPrContentTokens(prs, details)


def enrichBitbucketPrContentTokens(
    prs: List[Dict[str, Any]], domain: str, workspace: str, repoName: str,
    token: Optional[str], isCloud: bool,
) -> Dict[str, Any]:
    """Fetch Bitbucket PR comments/review comments and commit messages."""
    import urllib.parse
    headers = _bitbucketAuthHeaders(token, isCloud)
    encodedWorkspace = urllib.parse.quote(workspace, safe="")
    encodedRepo = urllib.parse.quote(repoName, safe="")
    prsByNumber = {pr.get("number"): pr for pr in prs if pr.get("number") is not None}

    def fetchCloud(url: str) -> List[Dict[str, Any]]:
        items = []
        params = {"pagelen": 100}
        while url:
            response = callBitbucketApi(url, headers, params)
            if not response:
                break
            data = response.json()
            items.extend(data.get("values", []))
            url = data.get("next")
            params = None
        return items

    def fetchServer(url: str) -> List[Dict[str, Any]]:
        items, start = [], 0
        while True:
            response = callBitbucketApi(url, headers, {"limit": 100, "start": start})
            if not response:
                break
            data = response.json()
            items.extend(data.get("values", []))
            if data.get("isLastPage", True):
                break
            start = data.get("nextPageStart", start + 100)
        return items

    def fetchOne(number: int) -> Tuple[int, Dict[str, Any]]:
        providerPr = prsByNumber.get(number, {})
        if isCloud:
            base = f"https://api.bitbucket.org/2.0/repositories/{encodedWorkspace}/{encodedRepo}/pullrequests/{number}"
            commentsRaw = fetchCloud(f"{base}/comments")
            commitsRaw = fetchCloud(f"{base}/commits")
            diffstat = fetchCloud(f"{base}/diffstat")
            additions = sum(int(item.get("lines_added") or 0) for item in diffstat)
            deletions = sum(int(item.get("lines_removed") or 0) for item in diffstat)
            changedFiles = len(diffstat)
            fileDetails = [{
                "filename": ((item.get("new") or {}).get("path")),
                "previous_filename": ((item.get("old") or {}).get("path")),
                "status": item.get("status"),
                "additions": item.get("lines_added"),
                "deletions": item.get("lines_removed"),
                "patch": None,
            } for item in diffstat]
            diffResponse = callBitbucketApi(f"{base}/diff", headers)
            if diffResponse:
                fileDetails.append({"filename": None, "status": "aggregate_diff",
                                    "patch": diffResponse.text})
            comments, reviewComments = [], []
            for item in commentsRaw:
                content = item.get("content") or {}
                user = item.get("user") or {}
                entry = {
                    "id": item.get("id"), "author": user.get("nickname") or user.get("display_name"),
                    "body": content.get("raw") or "", "created_at": item.get("created_on"),
                    "updated_at": item.get("updated_on"), "inline": item.get("inline"),
                    "deleted": item.get("deleted", False),
                }
                (reviewComments if item.get("inline") else comments).append(entry)
            commits = [{
                "id": item.get("hash"),
                "author": ((item.get("author") or {}).get("user") or {}).get("display_name"),
                "message": item.get("message") or "", "created_at": item.get("date"),
            } for item in commitsRaw]
            reviews = [{
                "author": (item.get("user") or {}).get("nickname") or
                          (item.get("user") or {}).get("display_name"),
                "state": "APPROVED" if item.get("approved") else
                         ("CHANGES_REQUESTED" if item.get("state") == "changes_requested" else
                          item.get("state")),
                "approved": item.get("approved"),
                "role": item.get("role"),
            } for item in providerPr.get("participants", [])]
        else:
            base = f"https://{domain}/rest/api/1.0/projects/{encodedWorkspace}/repos/{encodedRepo}/pull-requests/{number}"
            activities = fetchServer(f"{base}/activities")
            commitsRaw = fetchServer(f"{base}/commits")
            changesRaw = fetchServer(f"{base}/changes")
            changedFiles = len(changesRaw)
            additions = deletions = None
            diffResponse = callBitbucketApi(f"{base}/diff", headers)
            if diffResponse:
                additions, deletions = countUnifiedDiffChanges(diffResponse.text)
            fileDetails = [{
                "filename": ((item.get("path") or {}).get("toString") or
                             (item.get("path") or {}).get("name")),
                "status": item.get("type"),
                "patch": None,
            } for item in changesRaw]
            if diffResponse:
                fileDetails.append({"filename": None, "status": "aggregate_diff",
                                    "patch": diffResponse.text})
            comments, reviewComments = [], []
            for activity in activities:
                comment = activity.get("comment")
                if not comment:
                    continue
                author = comment.get("author") or {}
                entry = {
                    "id": comment.get("id"), "author": author.get("name") or author.get("displayName"),
                    "body": comment.get("text") or "", "created_at": activity.get("createdDate"),
                    "updated_at": comment.get("updatedDate"), "anchor": comment.get("anchor"),
                    "resolved": comment.get("resolved"),
                }
                (reviewComments if comment.get("anchor") else comments).append(entry)
            commits = [{
                "id": item.get("id"),
                "author": (item.get("author") or {}).get("name"),
                "message": item.get("message") or "", "created_at": item.get("authorTimestamp"),
            } for item in commitsRaw]
            reviews = [{
                "author": ((item.get("user") or {}).get("name") or
                           (item.get("user") or {}).get("displayName")),
                "state": item.get("status") or ("APPROVED" if item.get("approved") else None),
                "approved": item.get("approved"),
                "role": item.get("role"),
            } for item in providerPr.get("reviewers", [])]
        return number, {"comments": comments, "review_comments": reviewComments,
                        "commit_messages": commits, "additions": additions,
                        "deletions": deletions, "changed_files": changedFiles,
                        "files": fileDetails, "reviews": reviews}

    details = {}
    numbers = [pr.get("number") for pr in prs if pr.get("number") is not None]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(numbers)))) as executor:
        futures = {executor.submit(fetchOne, number): number for number in numbers}
        for future in as_completed(futures):
            number = futures[future]
            try:
                _, details[number] = future.result()
            except Exception as exc:
                logger.warning(f"[PR tokens] Bitbucket PR #{number} details skipped: {exc}")
    return _addPrContentTokens(prs, details)


def enrichGitHubPrContentTokens(
    g: Github, repo, prs: List[Dict[str, Any]], outputDir: Optional[str] = None,
) -> Dict[str, Any]:
    """Retain GitHub PR details and count the same content retained for GitLab."""
    llm = _emptyPrTokenMetrics()
    lexical = _emptyPrTokenMetrics()

    for pr in prs:
        text_value = "\n".join(filter(None, (pr.get("title"), pr.get("body"))))
        pr["llm_token_count"] = getLlmTokens(text_value)
        pr["lexical_token_count"] = len(countLexicalTokens(text_value))
        llm["titles_bodies"] += pr["llm_token_count"]
        lexical["titles_bodies"] += pr["lexical_token_count"]

    def rawData(item: Any) -> Dict[str, Any]:
        for attribute in ("_rawData", "_raw_data"):
            try:
                value = getattr(item, attribute, None)
                if value:
                    return dict(value)
            except Exception:
                pass
        return {}

    def objectValue(item: Any, attribute: str, default: Any = None) -> Any:
        try:
            value = getattr(item, attribute, default)
            return default if value is None else value
        except Exception:
            return default

    def isoValue(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    def fetch_details(number: int) -> Tuple[int, Dict[str, Any]]:
        gh_pr = repo.get_pull(number)
        issueComments = list(gh_pr.get_issue_comments())
        reviewComments = list(gh_pr.get_review_comments())
        reviews = list(gh_pr.get_reviews())
        changedFiles = list(gh_pr.get_files())
        prCommits = list(gh_pr.get_commits())
        comments = []
        for item in issueComments:
            raw = rawData(item)
            user = raw.get("user") or {}
            itemUser = objectValue(item, "user")
            comments.append({
                **raw,
                "id": raw.get("id", getattr(item, "id", None)),
                "author": user.get("login") or objectValue(itemUser, "login"),
                "body": raw.get("body") or getattr(item, "body", None) or "",
                "created_at": raw.get("created_at") or isoValue(objectValue(item, "created_at")),
                "updated_at": raw.get("updated_at") or isoValue(objectValue(item, "updated_at")),
            })

        reviewDetails = []
        for item in reviewComments:
            raw = rawData(item)
            user = raw.get("user") or {}
            itemUser = objectValue(item, "user")
            reviewDetails.append({
                **raw,
                "id": raw.get("id", getattr(item, "id", None)),
                "author": user.get("login") or objectValue(itemUser, "login"),
                "body": raw.get("body") or getattr(item, "body", None) or "",
                "created_at": raw.get("created_at") or isoValue(objectValue(item, "created_at")),
                "updated_at": raw.get("updated_at") or isoValue(objectValue(item, "updated_at")),
                "position": raw.get("position", objectValue(item, "position")),
                "path": raw.get("path") or objectValue(item, "path"),
                "commit_id": raw.get("commit_id") or objectValue(item, "commit_id"),
            })

        reviewSubmissions = []
        for item in reviews:
            raw = rawData(item)
            user = raw.get("user") or {}
            itemUser = objectValue(item, "user")
            reviewSubmissions.append({
                "id": raw.get("id", objectValue(item, "id")),
                "author": user.get("login") or objectValue(itemUser, "login"),
                "state": raw.get("state") or objectValue(item, "state"),
                "body": raw.get("body") or objectValue(item, "body", ""),
                "submitted_at": raw.get("submitted_at") or isoValue(objectValue(item, "submitted_at")),
                "commit_id": raw.get("commit_id") or objectValue(item, "commit_id"),
            })

        fileDetails = []
        for item in changedFiles:
            raw = rawData(item)
            fileDetails.append({
                "filename": raw.get("filename") or objectValue(item, "filename"),
                "previous_filename": raw.get("previous_filename") or objectValue(item, "previous_filename"),
                "status": raw.get("status") or objectValue(item, "status"),
                "additions": raw.get("additions", objectValue(item, "additions", 0)),
                "deletions": raw.get("deletions", objectValue(item, "deletions", 0)),
                "changes": raw.get("changes", objectValue(item, "changes", 0)),
                # GitHub omits this for binary files and may truncate very large patches.
                "patch": raw.get("patch") or objectValue(item, "patch"),
                "sha": raw.get("sha") or objectValue(item, "sha"),
            })

        commits = []
        for item in prCommits:
            raw = rawData(item)
            commit = raw.get("commit") or {}
            authorInfo = commit.get("author") or {}
            committerInfo = commit.get("committer") or {}
            authorUser = raw.get("author") or {}
            committerUser = raw.get("committer") or {}
            sha = raw.get("sha") or getattr(item, "sha", None)
            itemCommit = objectValue(item, "commit")
            itemAuthorInfo = objectValue(itemCommit, "author")
            itemCommitterInfo = objectValue(itemCommit, "committer")
            itemAuthorUser = objectValue(item, "author")
            commits.append({
                "id": sha,
                "short_id": sha[:8] if sha else None,
                "author": authorUser.get("login") or objectValue(itemAuthorUser, "login")
                          or authorInfo.get("name") or objectValue(itemAuthorInfo, "name"),
                "message": commit.get("message") or objectValue(itemCommit, "message", ""),
                "created_at": authorInfo.get("date") or isoValue(objectValue(itemAuthorInfo, "date"))
                              or committerInfo.get("date") or isoValue(objectValue(itemCommitterInfo, "date")),
            })

        return number, {
            "provider_metadata": rawData(gh_pr),
            "comments": comments,
            "review_comments": reviewDetails,
            "reviews": reviewSubmissions,
            "files": fileDetails,
            "commit_messages": commits,
            "additions": gh_pr.additions,
            "deletions": gh_pr.deletions,
            "changed_files": gh_pr.changed_files,
        }

    def exceptionHeaders(exc: Exception) -> Dict[str, Any]:
        headers = getattr(exc, "headers", None) or {}
        return {str(key).lower(): value for key, value in headers.items()}

    def retryWaitSeconds(exc: Exception, attempt: int) -> float:
        """Use GitHub's headers for primary/secondary limits, then back off safely."""
        headers = exceptionHeaders(exc)
        retryAfter = headers.get("retry-after")
        if retryAfter is not None:
            try:
                return max(1.0, float(retryAfter)) + random.uniform(1.0, 3.0)
            except (TypeError, ValueError):
                pass

        remaining = headers.get("x-ratelimit-remaining")
        resetAt = headers.get("x-ratelimit-reset")
        if str(remaining) == "0" and resetAt is not None:
            try:
                return max(1.0, float(resetAt) - time.time()) + 5.0
            except (TypeError, ValueError):
                pass

        # GitHub recommends waiting at least a minute for a secondary limit.
        return min(15 * 60, 60 * (2 ** attempt)) + random.uniform(1.0, 5.0)

    def fetchDetailsWithRetry(number: int) -> Tuple[int, Dict[str, Any]]:
        maxAttempts = 6
        for attempt in range(maxAttempts):
            try:
                return fetch_details(number)
            except Exception as exc:
                status = getattr(exc, "status", None)
                retryable = status in {403, 429, 500, 502, 503, 504}
                if not retryable or attempt == maxAttempts - 1:
                    raise
                if status in {403, 429}:
                    waitSeconds = retryWaitSeconds(exc, attempt)
                else:
                    waitSeconds = min(60, 2 ** (attempt + 1)) + random.uniform(0.0, 1.0)
                logger.warning(
                    f"[GitHub PR #{number}] HTTP {status}; retrying in {waitSeconds:.1f}s "
                    f"({attempt + 1}/{maxAttempts - 1})..."
                )
                time.sleep(waitSeconds)

    cacheDir = None
    if outputDir:
        repoName = sanitizeFilename(getattr(repo, "name", None) or "repository")
        cacheDir = os.path.join(outputDir, ".github_pr_checkpoint", repoName)
        os.makedirs(cacheDir, exist_ok=True)

    def cachePath(number: int) -> Optional[str]:
        return os.path.join(cacheDir, f"{number}.json") if cacheDir else None

    def loadCachedDetails(number: int, updatedAt: Any) -> Optional[Dict[str, Any]]:
        path = cachePath(number)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as cacheFile:
                cached = json.load(cacheFile)
            if cached.get("schema_version") == 2 and cached.get("updated_at") == updatedAt:
                return cached.get("details")
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"[GitHub PR #{number}] Ignoring invalid checkpoint: {exc}")
        return None

    def saveCachedDetails(number: int, updatedAt: Any, details: Dict[str, Any]):
        path = cachePath(number)
        if not path:
            return
        tempPath = f"{path}.tmp"
        try:
            with open(tempPath, "w", encoding="utf-8") as cacheFile:
                json.dump({"schema_version": 2, "updated_at": updatedAt, "details": details}, cacheFile)
            os.replace(tempPath, path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"[GitHub PR #{number}] Could not save checkpoint: {exc}")

    numbers = [pr.get("number") for pr in prs if pr.get("number") is not None]
    prsByNumber = {pr.get("number"): pr for pr in prs if pr.get("number") is not None}
    detailed_count = 0
    failedNumbers = []
    # GitHub recommends serial requests to avoid secondary rate limits. A large
    # repository can require several API calls per PR, so parallel workers can
    # trigger a 403 even when the primary hourly quota is still available.
    for index, number in enumerate(numbers, 1):
        try:
            pr = prsByNumber.get(number)
            updatedAt = (pr or {}).get("updated_at")
            details = loadCachedDetails(number, updatedAt)
            fromCheckpoint = details is not None
            if details is None:
                # Check periodically rather than spending another API request for
                # every PR. The exception path still reacts immediately to limits.
                if index == 1 or index % 25 == 0:
                    handleRateLimit(g, minimum_remaining=100)
                _, details = fetchDetailsWithRetry(number)
                saveCachedDetails(number, updatedAt, details)
            issueComments = details["comments"]
            reviewComments = details["review_comments"]
            commits = details["commit_messages"]
            discussion_text = "\n".join(
                item.get("body", "") for item in issueComments + reviewComments if item.get("body")
            )
            commit_text = "\n".join(item.get("message", "") for item in commits if item.get("message"))
            discussionLlm = getLlmTokens(discussion_text)
            commitLlm = getLlmTokens(commit_text)
            discussionLexical = len(countLexicalTokens(discussion_text))
            commitLexical = len(countLexicalTokens(commit_text))
            llm["discussion"] += discussionLlm
            llm["commit_messages"] += commitLlm
            lexical["discussion"] += discussionLexical
            lexical["commit_messages"] += commitLexical
            if pr is not None:
                providerMetadata = details["provider_metadata"]
                for key, value in providerMetadata.items():
                    pr.setdefault(key, value)
                pr["author"] = providerMetadata.get("user") or pr.get("user")
                pr["description"] = pr.get("body")
                pr["web_url"] = pr.get("html_url")
                pr["sha"] = pr.get("head_sha")
                pr["reviewers"] = providerMetadata.get("requested_reviewers", [])
                pr["work_in_progress"] = bool(providerMetadata.get("draft"))
                pr["has_conflicts"] = providerMetadata.get("mergeable") is False
                pr["merge_status"] = providerMetadata.get("mergeable_state")
                pr["detailed_merge_status"] = providerMetadata.get("mergeable_state")
                pr["reference"] = f"#{number}"
                pr["comment_details"] = issueComments
                pr["review_comment_details"] = reviewComments
                pr["review_details"] = details.get("reviews", [])
                pr["file_details"] = details.get("files", [])
                pr["commit_message_details"] = commits
                pr["llm_token_count"] += discussionLlm + commitLlm
                pr["lexical_token_count"] += discussionLexical + commitLexical
                pr["additions"] = details["additions"]
                pr["deletions"] = details["deletions"]
                pr["changed_files"] = details["changed_files"]
                pr["comments"] = len(issueComments)
                pr["review_comments"] = len(reviewComments)
                pr["commits"] = len(commits)
            detailed_count += 1
            if index % 25 == 0:
                logger.info(f"Enriched {index}/{len(numbers)} GitHub PRs...")
            if not fromCheckpoint:
                # A small jitter prevents a predictable burst pattern and greatly
                # reduces the chance of GitHub's secondary rate limit firing.
                time.sleep(random.uniform(1.0, 2.0))
        except Exception as exc:
            failedNumbers.append(number)
            logger.warning(f"[PR tokens] GitHub PR #{number} details skipped: {exc}")

    llm["total"] = llm["titles_bodies"] + llm["discussion"] + llm["commit_messages"]
    lexical["total"] = lexical["titles_bodies"] + lexical["discussion"] + lexical["commit_messages"]
    return {
        "pr_content_tokens": llm,
        "pr_content_tokens_lexical": lexical,
        "prs_detailed_count": detailed_count,
        "pr_analysis_complete": detailed_count == len(numbers),
        "pr_detail_failures": failedNumbers,
    }


def sanitizeFilename(name: str) -> str:
    """Sanitize the repository name to make it OS-safe."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def getPrLifecycleStatus(pr: Dict[str, Any]) -> str:
    """Return one mutually-exclusive, provider-neutral PR lifecycle state."""
    explicit = str(pr.get("lifecycle_status") or "").lower()
    if explicit in {"open", "merged", "closed"}:
        return explicit

    provider_state = str(pr.get("provider_state") or pr.get("state") or "").lower()
    if pr.get("merged_at") is not None or provider_state == "merged":
        return "merged"
    if provider_state in {"open", "opened"}:
        return "open"
    return "closed"


GITLAB_PR_JSON_FIELDS = (
    "id", "iid", "project_id", "title", "description", "state", "created_at",
    "updated_at", "merged_by", "merge_user", "merged_at", "closed_by", "closed_at",
    "target_branch", "source_branch", "user_notes_count", "upvotes", "downvotes",
    "author", "assignees", "assignee", "reviewers", "source_project_id",
    "target_project_id", "labels", "draft", "work_in_progress", "milestone",
    "merge_when_pipeline_succeeds", "merge_status", "detailed_merge_status",
    "merge_after", "sha", "merge_commit_sha", "squash_commit_sha",
    "discussion_locked", "should_remove_source_branch", "force_remove_source_branch",
    "prepared_at", "reference", "references", "url", "html_url", "time_stats", "squash",
    "squash_on_merge", "task_completion_status", "has_conflicts",
    "blocking_discussions_resolved", "approvals_before_merge", "imported",
    "imported_from", "provider", "provider_state", "lifecycle_status", "number",
    "body", "user", "html_url", "additions", "deletions", "changed_files",
    "comments", "review_comments", "commits", "head_sha", "comment_details",
    "review_comment_details", "review_details", "file_details",
    "commit_message_details", "llm_token_count",
    "lexical_token_count",
)

GITLAB_ONLY_PR_FIELDS = {
    "iid", "upvotes", "downvotes", "merge_when_pipeline_succeeds", "merge_after",
    "squash_commit_sha", "should_remove_source_branch", "force_remove_source_branch",
    "prepared_at", "time_stats", "squash", "squash_on_merge", "task_completion_status",
    "blocking_discussions_resolved", "approvals_before_merge", "imported", "imported_from",
}


def normalizeGithubPrToGitLabSchema(pr: Dict[str, Any]) -> Dict[str, Any]:
    """Return only GitLab-schema fields, populated with GitHub equivalents where available."""
    number = pr.get("number")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    baseRepo = base.get("repo") or {}
    headRepo = head.get("repo") or {}
    fullName = baseRepo.get("full_name")
    reference = pr.get("reference") or (f"#{number}" if number is not None else None)
    references = pr.get("references") or {
        "short": reference,
        "relative": reference,
        "full": f"{fullName}#{number}" if fullName and number is not None else reference,
    }

    comments = [{
        "id": item.get("id"),
        "author": item.get("author"),
        "body": item.get("body") or "",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "resolved": item.get("resolved"),
        "resolvable": item.get("resolvable"),
        "position": item.get("position"),
    } for item in pr.get("comment_details", [])]

    reviewComments = [{
        "id": item.get("id"),
        "author": item.get("author"),
        "body": item.get("body") or "",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "resolved": item.get("resolved"),
        "resolvable": item.get("resolvable"),
        "position": item.get("position"),
    } for item in pr.get("review_comment_details", [])]

    reviews = [{
        "id": item.get("id"),
        "author": item.get("author"),
        "state": item.get("state"),
        "body": item.get("body") or "",
        "submitted_at": item.get("submitted_at"),
        "commit_id": item.get("commit_id"),
    } for item in pr.get("review_details", [])]

    commits = [{
        "id": item.get("id"),
        "short_id": item.get("short_id"),
        "author": item.get("author"),
        "message": item.get("message") or "",
        "created_at": item.get("created_at"),
    } for item in pr.get("commit_message_details", [])]

    mapped = dict(pr)
    mapped.update({
        "iid": number,
        "project_id": pr.get("project_id") or baseRepo.get("id"),
        "description": pr.get("body"),
        "author": pr.get("author") or pr.get("user"),
        "reviewers": pr.get("reviewers") or pr.get("requested_reviewers", []),
        "source_project_id": pr.get("source_project_id") or headRepo.get("id"),
        "target_project_id": pr.get("target_project_id") or baseRepo.get("id"),
        "user_notes_count": int(pr.get("comments") or 0) + int(pr.get("review_comments") or 0),
        "work_in_progress": bool(pr.get("draft")),
        "merge_user": pr.get("merge_user") or pr.get("merged_by"),
        "merge_status": pr.get("merge_status") or pr.get("mergeable_state"),
        "detailed_merge_status": pr.get("detailed_merge_status") or pr.get("mergeable_state"),
        "sha": pr.get("sha") or pr.get("head_sha"),
        "discussion_locked": pr.get("discussion_locked", pr.get("locked")),
        "reference": reference,
        "references": references,
        "url": pr.get("url") or pr.get("web_url") or pr.get("html_url"),
        "html_url": pr.get("html_url") or pr.get("web_url") or pr.get("url"),
        "has_conflicts": pr.get("has_conflicts", pr.get("mergeable") is False),
        "provider": "github",
        "comment_details": comments,
        "review_comment_details": reviewComments,
        "review_details": reviews,
        "file_details": pr.get("file_details", []),
        "commit_message_details": commits,
    })
    return {
        field: mapped.get(field)
        for field in GITLAB_PR_JSON_FIELDS
        if field not in GITLAB_ONLY_PR_FIELDS
    }


def removeUnwantedUrlFields(value: Any) -> Any:
    """Recursively retain only exact `url` and `html_url` URL fields."""
    if isinstance(value, list):
        return [removeUnwantedUrlFields(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {}
    for key, item in value.items():
        if "url" in key.lower() and key not in {"url", "html_url"}:
            continue
        cleaned[key] = removeUnwantedUrlFields(item)
    return cleaned


def dumpPrJsonFiles(outputDir: str, sanitizedRepoName: str, prs: List[Dict[str, Any]]):
    """
    Dump all, open, closed, and merged PR lists into JSON files in outputDir.
    """
    os.makedirs(outputDir, exist_ok=True)

    outputPrs = []
    for pr in prs:
        outputPr = normalizeGithubPrToGitLabSchema(pr) if pr.get("provider") == "github" else dict(pr)
        outputPr["url"] = outputPr.get("url") or outputPr.get("web_url") or outputPr.get("html_url")
        outputPr["html_url"] = outputPr.get("html_url") or outputPr.get("web_url") or outputPr.get("url")
        outputPrs.append(removeUnwantedUrlFields(outputPr))
    open_prs = [pr for pr in outputPrs if getPrLifecycleStatus(pr) == "open"]
    closed_prs = [pr for pr in outputPrs if getPrLifecycleStatus(pr) == "closed"]
    merged_prs = [pr for pr in outputPrs if getPrLifecycleStatus(pr) == "merged"]

    files_to_write = {
        f"all_prs_{sanitizedRepoName}.json": outputPrs,
        f"open_prs_{sanitizedRepoName}.json": open_prs,
        f"closed_prs_{sanitizedRepoName}.json": closed_prs,
        f"merged_prs_{sanitizedRepoName}.json": merged_prs,
    }

    for filename, data in files_to_write.items():
        filepath = os.path.join(outputDir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved PR JSON dump: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save PR JSON dump {filepath}: {e}")


def addGrandTokenCountsToPrJsonFiles(
    outputDir: str, llmPrs: int, lexicalPrs: int,
):
    """Write one file-level token summary aligned with the summary CSV values."""
    if not os.path.isdir(outputDir):
        return

    for filename in os.listdir(outputDir):
        if not filename.endswith(".json") or "_prs_" not in filename:
            continue
        filepath = os.path.join(outputDir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                payload = json.load(f)
            prs = payload.get("pull_requests", []) if isinstance(payload, dict) else payload
            if not isinstance(prs, list):
                continue
            if not prs:
                with open(filepath, "w", encoding="utf-8"):
                    pass
                continue
            for pr in prs:
                pr.pop("llm_repository_token_count", None)
                pr.pop("llm_all_prs_token_count", None)
                pr.pop("llm_grand_token_count", None)
                pr.pop("lexical_repository_token_count", None)
                pr.pop("lexical_all_prs_token_count", None)
                pr.pop("lexical_grand_token_count", None)
            payload = {
                "pull_requests": prs,
                "token_summary": {
                    "llm_all_prs_token_count": llmPrs,
                    "lexical_all_prs_token_count": lexicalPrs,
                },
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to add grand token counts to {filepath}: {e}")

def dumpCommitsCsv(outputDir: str, sanitizedRepoName: str, commits: List[Dict[str, Any]]):
    os.makedirs(outputDir, exist_ok=True)
    filepath = os.path.join(outputDir, f"commits_{sanitizedRepoName}.csv")
    display_headers = ["hash", "author", "email", "date", "message", "Meaningful"]
    try:
        with CSV_LOCK:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=display_headers)
                writer.writeheader()
                for row in commits:
                    writer.writerow({
                        "hash": row.get("hash", ""),
                        "author": row.get("author", ""),
                        "email": row.get("email", ""),
                        "date": row.get("date", ""),
                        "message": row.get("message", ""),
                        "Meaningful": row.get("meaningful", ""),
                    })
        logger.info(f"Saved commit-level CSV: {filepath}")
    except Exception as e:
        logger.error(f"Failed to save commits CSV {filepath}: {e}")   


def dumpJson(filepath: str, payload: Any) -> None:
    """Atomically write a JSON extraction artifact."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tempPath = f"{filepath}.tmp"
    with open(tempPath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    os.replace(tempPath, filepath)


def extractLocalHistoryAndAutomation(repoDir: str, outputDir: str, repoName: str,
                                     commits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Export fetched Git refs plus test/CI definitions and retained reports."""
    branchesRaw = runGit(["for-each-ref", "--format=%(refname:short)%09%(objectname)",
                          "refs/heads", "refs/remotes"], repoDir, timeout=300) or ""
    tagsRaw = runGit(["for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(creatordate:iso-strict)",
                      "refs/tags"], repoDir, timeout=300) or ""
    branches = []
    for line in branchesRaw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and not parts[0].endswith("/HEAD"):
            branches.append({"name": parts[0], "sha": parts[1]})
    tags = []
    for line in tagsRaw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            tags.append({"name": parts[0], "sha": parts[1],
                         "created_at": parts[2] if len(parts) > 2 else None})

    safeName = sanitizeFilename(repoName)
    shallow = os.path.isfile(os.path.join(repoDir, ".git", "shallow"))
    history = {
        "scope": "all_fetched_refs",
        "complete_claimed": False,
        "limitations": (["shallow_clone"] if shallow else []) +
                       ["Deleted/unfetched/unreachable history cannot be recovered from the clone."],
        "shallow_clone": shallow,
        "commits": commits,
        "branches": branches,
        "tags": tags,
    }
    dumpJson(os.path.join(outputDir, f"git_history_{safeName}.json"), history)

    ciNames = {"Jenkinsfile", ".gitlab-ci.yml", ".gitlab-ci.yaml", ".travis.yml",
               "azure-pipelines.yml", "azure-pipelines.yaml", "bitbucket-pipelines.yml",
               "circle.yml", "appveyor.yml", "buildkite.yml"}
    reportNames = {value.lower() for value in COVERAGE_FILES} | {
        "junit.xml", "test-results.xml", "pytest.xml", "test-report.xml",
        "coverage.json", "clover.xml", "jacoco.xml", "surefire-reports",
    }
    testPatterns = re.compile(r"(^|[/\\])(tests?|specs?|__tests__)([/\\]|$)|"
                              r"(^|[/\\]).*(test|spec)\.[^.]+$", re.IGNORECASE)
    ciFiles, testFiles, retainedReports = [], [], []
    for root, dirs, files in os.walk(repoDir):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "vendor", ".venv", "venv"}]
        for filename in files:
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, repoDir).replace("\\", "/")
            lower = relative.lower()
            if (filename in ciNames or lower.startswith(".github/workflows/") or
                    lower.startswith(".circleci/") or lower.startswith(".azure-pipelines/")):
                ciFiles.append(relative)
            if testPatterns.search(relative):
                testFiles.append(relative)
            if filename.lower() in reportNames or any(lower.endswith("/" + name) for name in reportNames):
                retainedReports.append(relative)

    reportSummaries = []
    for relative in sorted(set(retainedReports)):
        path = os.path.join(repoDir, relative.replace("/", os.sep))
        summary: Dict[str, Any] = {"path": relative}
        try:
            summary["size_bytes"] = os.path.getsize(path)
            lowerName = relative.lower()
            if lowerName.endswith(".xml"):
                import xml.etree.ElementTree as ET
                root = ET.parse(path).getroot()
                summary.update({
                    key.replace("-", "_"): root.attrib.get(key)
                    for key in ("tests", "failures", "errors", "skipped",
                                "time", "line-rate", "branch-rate")
                    if key in root.attrib
                })
            elif lowerName.endswith("lcov.info"):
                totals = {"lines_found": 0, "lines_hit": 0,
                          "branches_found": 0, "branches_hit": 0}
                prefixes = {"LF:": "lines_found", "LH:": "lines_hit",
                            "BRF:": "branches_found", "BRH:": "branches_hit"}
                with open(path, "r", encoding="utf-8", errors="replace") as reportFile:
                    for line in reportFile:
                        for prefix, key in prefixes.items():
                            if line.startswith(prefix):
                                totals[key] += int(line[len(prefix):].strip() or 0)
                summary.update(totals)
        except Exception as exc:
            summary["parse_error"] = str(exc)
        reportSummaries.append(summary)

    automation = {
        "test_files": sorted(set(testFiles)),
        "ci_configuration_files": sorted(set(ciFiles)),
        "retained_test_or_coverage_reports": sorted(set(retainedReports)),
        "retained_test_report_summaries": reportSummaries,
        "test_execution_performed": False,
        "ci_results_source": "provider_api_when_available",
    }
    dumpJson(os.path.join(outputDir, f"tests_ci_inventory_{safeName}.json"), automation)
    return {"git_history": history, "tests_ci_inventory": automation}


def calculatePrCommentMetrics(prs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return general and review-comment totals split by PR lifecycle."""
    metrics = {
        "total_pr_comments_count": 0,
        "open_pr_comments_count": 0,
        "closed_pr_comments_count": 0,
        "merged_pr_comments_count": 0,
        "total_pr_review_comment_count": 0,
        "open_pr_review_comment_count": 0,
        "closed_pr_review_comment_count": 0,
        "merged_pr_review_comment_count": 0,
    }
    for pr in prs:
        status = getPrLifecycleStatus(pr)
        comments = int(pr.get("comments") or 0)
        reviewComments = int(pr.get("review_comments") or 0)
        metrics["total_pr_comments_count"] += comments
        metrics["total_pr_review_comment_count"] += reviewComments
        if status in {"open", "closed", "merged"}:
            metrics[f"{status}_pr_comments_count"] += comments
            metrics[f"{status}_pr_review_comment_count"] += reviewComments
    return metrics


def extractGitHubIssuesAndCi(repo: Any, outputDir: str, repoName: str) -> Dict[str, Any]:
    """Extract issue collaboration history and retained Actions/check metadata."""
    safeName = sanitizeFilename(repoName)
    issues = []
    issueErrors = []
    try:
        for issue in repo.get_issues(state="all"):
            raw = dict(getattr(issue, "_raw_data", {}) or {})
            if raw.get("pull_request") or getattr(issue, "pull_request", None):
                continue
            comments = []
            try:
                for comment in issue.get_comments():
                    cRaw = dict(getattr(comment, "_raw_data", {}) or {})
                    comments.append({
                        "id": cRaw.get("id", getattr(comment, "id", None)),
                        "author": (cRaw.get("user") or {}).get("login"),
                        "body": cRaw.get("body", getattr(comment, "body", "")),
                        "created_at": cRaw.get("created_at"),
                        "updated_at": cRaw.get("updated_at"),
                    })
            except Exception as exc:
                issueErrors.append({"issue": issue.number, "section": "comments", "error": str(exc)})
            issues.append({
                "number": issue.number, "title": issue.title, "body": issue.body,
                "state": issue.state, "state_reason": raw.get("state_reason"),
                "author": (raw.get("user") or {}).get("login"),
                "assignees": [item.get("login") for item in raw.get("assignees", [])],
                "labels": [item.get("name") for item in raw.get("labels", [])],
                "milestone": (raw.get("milestone") or {}).get("title"),
                "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
                "closed_at": raw.get("closed_at"), "comments": comments,
            })
    except Exception as exc:
        issueErrors.append({"section": "issues", "error": str(exc)})
    dumpJson(os.path.join(outputDir, f"issues_{safeName}.json"), {
        "complete_claimed": False, "issues": issues, "errors": issueErrors,
    })

    runs, ciErrors = [], []
    try:
        for run in repo.get_workflow_runs():
            raw = dict(getattr(run, "_raw_data", {}) or {})
            jobs = []
            try:
                for job in run.jobs():
                    jRaw = dict(getattr(job, "_raw_data", {}) or {})
                    jobs.append({
                        "id": jRaw.get("id"), "name": jRaw.get("name"),
                        "status": jRaw.get("status"), "conclusion": jRaw.get("conclusion"),
                        "started_at": jRaw.get("started_at"), "completed_at": jRaw.get("completed_at"),
                        "steps": jRaw.get("steps", []),
                    })
            except Exception as exc:
                ciErrors.append({"run": raw.get("id"), "section": "jobs", "error": str(exc)})
            runs.append({
                "id": raw.get("id"), "name": raw.get("name"), "event": raw.get("event"),
                "status": raw.get("status"), "conclusion": raw.get("conclusion"),
                "workflow_id": raw.get("workflow_id"), "run_number": raw.get("run_number"),
                "head_branch": raw.get("head_branch"), "head_sha": raw.get("head_sha"),
                "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
                "run_started_at": raw.get("run_started_at"), "jobs": jobs,
            })
    except Exception as exc:
        ciErrors.append({"section": "workflow_runs", "error": str(exc)})
    dumpJson(os.path.join(outputDir, f"ci_results_{safeName}.json"), {
        "complete_claimed": False,
        "limitations": ["Expired logs/artifacts and inaccessible historical runs cannot be recovered."],
        "workflow_runs": runs, "errors": ciErrors,
    })
    return {"issues_extracted": len(issues), "ci_runs_extracted": len(runs),
            "issues_errors": len(issueErrors), "ci_errors": len(ciErrors)}


def extractGitLabIssuesAndCi(domain: str, projectPath: str, token: Optional[str],
                             outputDir: str, repoName: str) -> Dict[str, Any]:
    """Extract GitLab issues, notes, pipelines and jobs when accessible."""
    import urllib.parse
    headers = {"PRIVATE-TOKEN": token} if token else {}
    encoded = urllib.parse.quote_plus(projectPath)
    base = f"https://{domain}/api/v4/projects/{encoded}"

    def fetchPaged(url: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        result, page = [], 1
        while True:
            query = dict(params or {})
            query.update({"per_page": 100, "page": page})
            response = callGitLabApi(url, headers, query)
            if not response:
                break
            data = response.json()
            if not isinstance(data, list):
                break
            result.extend(data)
            nextPage = response.headers.get("X-Next-Page")
            if not nextPage:
                break
            page = int(nextPage)
        return result

    errors, issues = [], []
    try:
        for issue in fetchPaged(f"{base}/issues", {"scope": "all", "state": "all"}):
            iid = issue.get("iid")
            notes = fetchPaged(f"{base}/issues/{iid}/notes") if iid is not None else []
            issues.append({
                "iid": iid, "title": issue.get("title"), "description": issue.get("description"),
                "state": issue.get("state"), "author": (issue.get("author") or {}).get("username"),
                "assignees": [item.get("username") for item in issue.get("assignees", [])],
                "labels": issue.get("labels", []), "milestone": issue.get("milestone"),
                "created_at": issue.get("created_at"), "updated_at": issue.get("updated_at"),
                "closed_at": issue.get("closed_at"),
                "comments": [{
                    "id": note.get("id"), "author": (note.get("author") or {}).get("username"),
                    "body": note.get("body"), "created_at": note.get("created_at"),
                    "updated_at": note.get("updated_at"), "system": note.get("system"),
                } for note in notes],
            })
    except Exception as exc:
        errors.append({"section": "issues", "error": str(exc)})
    dumpJson(os.path.join(outputDir, f"issues_{sanitizeFilename(repoName)}.json"), {
        "complete_claimed": False, "issues": issues, "errors": errors,
    })

    pipelines = []
    try:
        for pipeline in fetchPaged(f"{base}/pipelines"):
            pipelineId = pipeline.get("id")
            jobs = fetchPaged(f"{base}/pipelines/{pipelineId}/jobs") if pipelineId else []
            pipelines.append({**pipeline, "jobs": jobs})
    except Exception as exc:
        errors.append({"section": "pipelines", "error": str(exc)})
    dumpJson(os.path.join(outputDir, f"ci_results_{sanitizeFilename(repoName)}.json"), {
        "complete_claimed": False,
        "limitations": ["Expired artifacts/logs and inaccessible runs cannot be recovered."],
        "pipelines": pipelines, "errors": errors,
    })
    return {"issues_extracted": len(issues), "ci_runs_extracted": len(pipelines),
            "supplemental_errors": len(errors)}


def extractBitbucketIssuesAndCi(domain: str, workspace: str, repoName: str,
                                token: Optional[str], isCloud: bool,
                                prs: List[Dict[str, Any]], outputDir: str) -> Dict[str, Any]:
    """Extract Bitbucket Cloud issues/pipelines or Server build statuses."""
    import urllib.parse
    headers = _bitbucketAuthHeaders(token, isCloud)
    encodedWorkspace = urllib.parse.quote(workspace, safe="")
    encodedRepo = urllib.parse.quote(repoName, safe="")
    safeName = sanitizeFilename(repoName)
    errors: List[Dict[str, Any]] = []

    def fetchCloud(url: str, params: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], bool]:
        result, first = [], True
        while url:
            response = callBitbucketApi(url, headers, params if first else None)
            first = False
            if not response:
                return result, False
            data = response.json()
            result.extend(data.get("values", []))
            url = data.get("next")
        return result, True

    def fetchServer(url: str) -> Tuple[List[Dict[str, Any]], bool]:
        result, start = [], 0
        while True:
            response = callBitbucketApi(url, headers, {"limit": 100, "start": start})
            if not response:
                return result, False
            data = response.json()
            result.extend(data.get("values", []))
            if data.get("isLastPage", True):
                return result, True
            start = data.get("nextPageStart", start + 100)

    issues: List[Dict[str, Any]] = []
    issueAvailable = False
    if isCloud:
        base = f"https://api.bitbucket.org/2.0/repositories/{encodedWorkspace}/{encodedRepo}"
        rawIssues, issueAvailable = fetchCloud(f"{base}/issues", {"pagelen": 100})
        for issue in rawIssues:
            issueId = issue.get("id")
            comments, commentsOk = fetchCloud(f"{base}/issues/{issueId}/comments", {"pagelen": 100})
            if not commentsOk:
                errors.append({"issue": issueId, "section": "comments",
                               "error": "Unavailable or permission denied"})
            issues.append({
                "id": issueId, "title": issue.get("title"),
                "content": (issue.get("content") or {}).get("raw"),
                "state": issue.get("state"), "kind": issue.get("kind"),
                "priority": issue.get("priority"), "component": issue.get("component"),
                "milestone": issue.get("milestone"), "version": issue.get("version"),
                "reporter": (issue.get("reporter") or {}).get("display_name"),
                "assignee": (issue.get("assignee") or {}).get("display_name"),
                "created_at": issue.get("created_on"), "updated_at": issue.get("updated_on"),
                "comments": [{
                    "id": item.get("id"),
                    "author": (item.get("user") or {}).get("display_name"),
                    "body": (item.get("content") or {}).get("raw"),
                    "created_at": item.get("created_on"), "updated_at": item.get("updated_on"),
                    "deleted": item.get("deleted", False),
                } for item in comments],
            })
        if not issueAvailable:
            errors.append({"section": "issues", "error":
                           "Issue tracker unavailable, disabled, or permission denied"})
    else:
        errors.append({"section": "issues", "error":
                       "Bitbucket Server/Data Center has no native repository issues API; issues may live in Jira"})

    dumpJson(os.path.join(outputDir, f"issues_{safeName}.json"), {
        "provider": "bitbucket_cloud" if isCloud else "bitbucket_server",
        "available": issueAvailable, "complete_claimed": False,
        "issues": issues, "errors": [e for e in errors if e.get("section") in {"issues", "comments"}],
    })

    ciRuns: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    ciAvailable = False
    if isCloud:
        pipelines, ciAvailable = fetchCloud(f"{base}/pipelines/", {"pagelen": 50, "sort": "-created_on"})
        for pipeline in pipelines:
            pipelineUuid = pipeline.get("uuid")
            encodedUuid = urllib.parse.quote(str(pipelineUuid), safe="{}")
            steps, stepsOk = fetchCloud(f"{base}/pipelines/{encodedUuid}/steps/", {"pagelen": 100})
            if not stepsOk:
                errors.append({"pipeline": pipelineUuid, "section": "steps",
                               "error": "Unavailable or permission denied"})
            ciRuns.append({
                "uuid": pipelineUuid, "build_number": pipeline.get("build_number"),
                "state": pipeline.get("state"), "trigger": pipeline.get("trigger"),
                "target": pipeline.get("target"), "created_at": pipeline.get("created_on"),
                "completed_at": pipeline.get("completed_on"), "duration": pipeline.get("duration_in_seconds"),
                "steps": [{
                    "uuid": step.get("uuid"), "name": step.get("name"),
                    "state": step.get("state"), "started_at": step.get("started_on"),
                    "completed_at": step.get("completed_on"),
                    "duration": step.get("duration_in_seconds"),
                } for step in steps],
            })
        if not ciAvailable:
            errors.append({"section": "pipelines", "error":
                           "Pipelines unavailable, disabled, or permission denied"})
    else:
        statusBase = f"https://{domain}/rest/build-status/1.0/commits"
        seenShas = set()
        for pr in prs:
            sha = pr.get("head_sha") or pr.get("merge_commit_sha")
            if not sha or sha in seenShas:
                continue
            seenShas.add(sha)
            rawStatuses, ok = fetchServer(f"{statusBase}/{urllib.parse.quote(str(sha), safe='')}")
            ciAvailable = ciAvailable or ok
            if not ok:
                errors.append({"commit": sha, "section": "build_statuses",
                               "error": "Unavailable or permission denied"})
                continue
            for item in rawStatuses:
                statuses.append({
                    "commit": sha, "key": item.get("key"), "name": item.get("name"),
                    "state": item.get("state"), "description": item.get("description"),
                    "created_at": item.get("dateAdded"), "updated_at": item.get("lastUpdated"),
                })

    dumpJson(os.path.join(outputDir, f"ci_results_{safeName}.json"), {
        "provider": "bitbucket_cloud" if isCloud else "bitbucket_server",
        "available": ciAvailable, "complete_claimed": False,
        "limitations": ["Expired logs/artifacts and inaccessible historical results cannot be recovered."],
        "pipeline_runs": ciRuns, "commit_build_statuses": statuses,
        "errors": [e for e in errors if e.get("section") not in {"issues", "comments"}],
    })
    return {"issues_extracted": len(issues), "ci_runs_extracted": len(ciRuns),
            "build_statuses_extracted": len(statuses), "supplemental_errors": len(errors)}


def runGitHubPrAnalysis(
    targetDir: str,
    inputTarget: str,
    token: Optional[str],
    outputDir: str,
) -> Dict[str, Any]:
    """
    GitHub PR analytics module (Stage 0.5/Enrichment stage).
    """
    defaults = {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "total_pr_comments_count": 0,
        "open_pr_comments_count": 0,
        "closed_pr_comments_count": 0,
        "merged_pr_comments_count": 0,
        "total_pr_review_comment_count": 0,
        "open_pr_review_comment_count": 0,
        "closed_pr_review_comment_count": 0,
        "merged_pr_review_comment_count": 0,
        "github_pr_analysis_available": False,
        "pr_content_tokens": _emptyPrTokenMetrics(),
        "pr_content_tokens_lexical": _emptyPrTokenMetrics(),
    }

    env_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    final_token = env_token or token

    info = extractGitHubRepoInfo(targetDir, inputTarget)
    if not info:
        logger.info("Not a GitHub repository or remote URL. Skipping PR analysis.")
        return defaults

    owner, repo_name = info

    if not final_token:
        logger.warning(
            "GitHub token missing. Please set GITHUB_TOKEN or GH_TOKEN env variables "
            "(or pass --github-token) to enable GitHub PR analytics."
        )
        return defaults

    if not HAS_PYGITHUB:
        logger.warning("PyGithub is not installed. Skipping GitHub PR analytics.")
        return defaults

    try:
        try:
            from github import Auth
            auth = Auth.Token(final_token)
            # Centralize rate-limit handling below. PyGithub's default retry
            # layer can otherwise sleep internally before our checkpoint/retry
            # logic gets a chance to inspect GitHub's response headers.
            g = Github(auth=auth, retry=None)
        except (ImportError, AttributeError):
            g = Github(final_token, retry=None)
        repo = g.get_repo(f"{owner}/{repo_name}")

        prs = fetchPullRequests(g, repo)

        metrics = calculatePrMetrics(prs)
        metrics["_merge_commit_identities"] = buildMergeCommitIdentities(prs)

        token_metrics = enrichGitHubPrContentTokens(g, repo, prs, outputDir)
        metrics.update(token_metrics)
        metrics.update(calculatePrCommentMetrics(prs))

        supplemental = extractGitHubIssuesAndCi(repo, outputDir, repo_name)
        metrics.update(supplemental)

        sanitized_name = sanitizeFilename(repo_name)
        dumpPrJsonFiles(outputDir, sanitized_name, prs)

        return metrics

    except Exception as e:
        logger.warning(f"GitHub PR analysis failed: {e}. Falling back to default metrics.")
        return defaults


def enrichViaGithubApi(
    repoUrl: str,
    token: Optional[str],
) -> Dict[str, Any]:
    """
    Optional GitHub API enrichment (requires PyGithub + valid token for private repos).
    Fetches: open/closed status, stars, forks, topics, description, visibility,
    subscriber count, default branch, and whether repo was ever public.
    """
    if not HAS_PYGITHUB:
        return {"available": False, "reason": "PyGithub not installed"}
    if not token:
        return {"available": False, "reason": "No GitHub token provided"}
    m = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repoUrl)
    if not m:
        return {"available": False, "reason": "Cannot parse GitHub URL"}

    try:
        try:
            from github import Auth
            g = Github(auth=Auth.Token(token), retry=None)
        except (ImportError, AttributeError):
            g = Github(token, retry=None)
        repo = g.get_repo(f"{m.group(1)}/{m.group(2)}")


        return {
            "available":       True,
            "full_name":       repo.full_name,
            "owner":           repo.owner.login,
            "description":     repo.description,
            "is_private":      repo.private,
            "is_fork":         repo.fork,
            "stars":           repo.stargazers_count,
            "forks":           repo.forks_count,
            "open_issues":     repo.open_issues_count,
            "topics":          repo.get_topics(),
            "default_branch":  repo.default_branch,
            "created_at":      str(repo.created_at.date()),
            "pushed_at":       str(repo.pushed_at.date()),
            "size_kb":         repo.size,
            "license":         (repo.license.spdx_id if repo.license else None),
            "language":        repo.language,
            "subscribers":     repo.subscribers_count,  # Watchers
            "visibility":      "private" if repo.private else "public",
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def saveReport(
    reportData: Dict[str, Any],
    repoPath: str,
    inputTarget: str,
    outputRootDir: str,
    isUrl: bool
) -> str:
    """
    Save analysis report to JSON and generate two separate summary CSVs.
    """
    absRepo = os.path.abspath(repoPath)
    repoName = os.path.basename(absRepo)

    if isUrl:
        safeName = inputTarget.rstrip("/").split("/")[-1].replace(".git", "")
        outDir = os.path.join(outputRootDir, safeName)
    else:
        outDir = os.path.join(outputRootDir, repoName)

    os.makedirs(outDir, exist_ok=True)
    outFile = os.path.join(outDir, f"{repoName}_report.json")

    gt = reportData.get("ground_truth", {})
    tm = reportData.get("tool_metrics", {})
    he = reportData.get("heuristics", {})
    gh = reportData.get("github_api", {})

    primaryLangs = gt.get("languages", {}).get("core_list", [])
    primaryLangs = list(dict.fromkeys([LANG_MERGE_MAP.get(lang, lang) for lang in primaryLangs]))

    frontendLangs = sorted(list(set(primaryLangs) & FRONTEND_LANGUAGES))
    backendLangs = sorted(list(set(primaryLangs) & BACKEND_LANGUAGES))

    allFwFound = set()
    for fw_list in he.get("frameworks", {}).values():
        for fw in fw_list:
            # Normalize to formal name if known, otherwise keep as is
            normFw = FORMAL_FRAMEWORK_NAMES.get(fw.lower(), fw)
            allFwFound.add(normFw)

    # Case-insensitive deduplication for the final sets
    def _dedupe_fws(fws, category_set):
        seen_lower = set()
        unique_fws = []
        for f in sorted(list(fws)):
            if f.lower() in category_set and f.lower() not in seen_lower:
                unique_fws.append(f)
                seen_lower.add(f.lower())
        return unique_fws

    frontendFws = _dedupe_fws(allFwFound, FRONTEND_FRAMEWORKS)
    backendFws = _dedupe_fws(allFwFound, BACKEND_FRAMEWORKS)

    lexTokCount = tm.get("tokens", {}).get("lexical", 0)
    infra = he.get("infrastructure", {})
    doc = he.get("docs", {})

    freq = 0
    if gt.get("git", {}).get("active_span_months", 0) > 0:
        freq = round(gt.get("git", {}).get("commit_count", 0) / gt.get("git", {}).get("active_span_months"), 1)

    sec_count = tm.get("secrets", {}).get("findings_count", 0)
    sec_status = "Clean" if tm.get("secrets", {}).get("clean", True) else f"Review Required ({sec_count})"

    # Note: Recalculated lang percent is not used directly since byte percent is output below

    byte_breakdown = reportData.get("heuristics", {}).get("byte_breakdown", {})
    consolidatedLangs = defaultdict(int)
    consolidatedFws = defaultdict(int)

    for label, byte_count in byte_breakdown.items():
        if label in NON_CORE_FORMATS:
            continue

        if "(" in label:
            parts = label.split("(")
            fwName = parts[0].strip()
            langName = parts[1].replace(")", "").strip()
            identityLang = LANG_MERGE_MAP.get(langName, langName)
            consolidatedLangs[identityLang] += byte_count
            consolidatedFws[fwName] += byte_count
        else:
            identityLang = LANG_MERGE_MAP.get(label, label)
            consolidatedLangs[identityLang] += byte_count

    totalLangBytes = sum(consolidatedLangs.values())
    langBytePctStr = "N/A"
    if totalLangBytes > 0:
        lang_pcts = []
        for name, size in consolidatedLangs.items():
            pct = (size / totalLangBytes) * 100
            if pct >= 5.0:
                lang_pcts.append((name, pct))
        lang_pcts.sort(key=lambda x: x[1], reverse=True)
        langBytePctStr = " | ".join([f"{name} ({pct:.1f}%)" for name, pct in lang_pcts]) or "None > 5%"

    # Process Framework %
    totalFwBytes = sum(consolidatedFws.values())
    fwBytePctStr = "N/A"
    if totalFwBytes > 0:
        fw_pcts = []
        for name, size in consolidatedFws.items():
            pct = (size / totalFwBytes) * 100
            if pct >= 5.0:
                fw_pcts.append((name, pct))
        fw_pcts.sort(key=lambda x: x[1], reverse=True)
        fwBytePctStr = " | ".join([f"{name} ({pct:.1f}%)" for name, pct in fw_pcts]) or "None > 5%"
    else:
        fwBytePctStr = "None Detected"

    prs_data = reportData.get("github_prs", {
        "total_pr_count": 0,
        "open_pr_count": 0,
        "closed_pr_count": 0,
        "merged_pr_count": 0,
        "total_pr_comments_count": 0,
        "open_pr_comments_count": 0,
        "closed_pr_comments_count": 0,
        "merged_pr_comments_count": 0,
        "total_pr_review_comment_count": 0,
        "open_pr_review_comment_count": 0,
        "closed_pr_review_comment_count": 0,
        "merged_pr_review_comment_count": 0,
        "github_pr_analysis_available": False,
    })
    pr_velocity_profile = prs_data.get("pr_velocity_profile", "N/A")
    blacklist_status = prs_data.get("blacklist_status", "N/A")
    prLlmTokens = prs_data.get("pr_content_tokens", _emptyPrTokenMetrics())
    prLexicalTokens = prs_data.get("pr_content_tokens_lexical", _emptyPrTokenMetrics())
    tokenMetrics = tm.get("tokens", {})

    all_headers = [
        "repo_name", "is_git", "license_type",
        "first_commit_date", "last_commit_date",
        "authored_loc", "non_authored_loc", "loc_comment", "loc_blank", "loc_files",
        "lang_count", "languages", "languages_frontend", "languages_backend",
        "commits", "contributors", "all_contributors_count",
        "all_contributors", "branch_count", "default_branch", "analysis_branch",
        "branch_selection_source", "meaningful_commit_count",
        "development_span_months", "commits_per_month", "git_history_intact",
        "tokens_llm_code", "tokens_llm_docs", "tokens_llm_config",
        "tokens_llm_repository", "tokens_llm_pr", "tokens_llm_grand_total",
        "lexical_token_code", "lexical_token_docs", "lexical_token_config",
        "lexical_token_repository", "lexical_token_pr", "lexical_token_grand_total",
        "duplication_weighted_percent",
        "code_complexity", "ai_detection_percent", "frameworks",
        "framework_frontend", "framework_backend",
        "databases_used", "third_party_apis", "setup_guidelines",
        "security_findings", "documentation_quality",
        "repo_rating_score", "repo_rating_label",
        "languages_percentage_bytes(>5%)", "frameworks_percentage_bytes(>5%)",
        "total_time_seconds",
        "total_pr_count", "open_pr_count", "closed_pr_count",
        "merged_pr_count",
        "total_pr_comments_count", "open_pr_comments_count",
        "closed_pr_comments_count", "merged_pr_comments_count",
        "total_pr_review_comment_count", "open_pr_review_comment_count",
        "closed_pr_review_comment_count", "merged_pr_review_comment_count",
        "github_pr_analysis_available",
        "pr_velocity_profile", "blacklist_status"
    ]

    all_row = {
        "repo_name":          reportData.get("repo"),
        "is_git":             reportData.get("is_git"),
        "license_type":       tm.get("compliance", {}).get("license_type", "N/A"),
        "first_commit_date":  gt.get("git", {}).get("first_commit", "N/A"),
        "last_commit_date":   gt.get("git", {}).get("last_update", "N/A"),
        "authored_loc":       gt.get("loc", {}).get("breakdown", {}).get("authored", 0),
        "non_authored_loc":   gt.get("loc", {}).get("breakdown", {}).get("non_authored", 0),
        "loc_comment":        gt.get("loc", {}).get("breakdown", {}).get("comment", 0),
        "loc_blank":          gt.get("loc", {}).get("breakdown", {}).get("blank", 0),
        "loc_files":          gt.get("loc", {}).get("breakdown", {}).get("nFiles", 0),
        "lang_count":         len(primaryLangs),
        "languages":          " | ".join(primaryLangs),
        "languages_frontend": " | ".join(frontendLangs),
        "languages_backend":  " | ".join(backendLangs),
        "commits":            gt.get("git", {}).get("commit_count", 0),
        "contributors":       gt.get("git", {}).get("unique_contributors", 0),
        "all_contributors_count": gt.get("git", {}).get("all_contributors_count", 0),
        "all_contributors":   ", ".join([c.get("name", "") for c in gt.get("git", {}).get("all_contributors", [])]),
        "branch_count":       gt.get("git", {}).get("branch_count", 0),
        "default_branch":     gt.get("git", {}).get("default_branch", ""),
        "analysis_branch":    gt.get("git", {}).get("analysis_branch", ""),
        "branch_selection_source": gt.get("git", {}).get("branch_selection_source", ""),
        "meaningful_commit_count": gt.get("git", {}).get("meaningful_commit_count", 0),
        "development_span_months": gt.get("git", {}).get("active_span_months", 0),
        "commits_per_month":  f"{freq} commits/mo",
        "git_history_intact": gt.get("git", {}).get("history_integrity", {}).get("appears_intact", False),
        "tokens_llm_code":    tokenMetrics.get("llm_code", 0),
        "tokens_llm_docs":    tokenMetrics.get("llm_docs", 0),
        "tokens_llm_config":  tokenMetrics.get("llm_config", 0),
        "tokens_llm_repository": tokenMetrics.get("llm_repository", tokenMetrics.get("llm", 0)),
        "tokens_llm_pr":      prLlmTokens.get("total", 0),
        "tokens_llm_grand_total": tokenMetrics.get("llm", 0),
        "lexical_token_code": tokenMetrics.get("lexical_code", 0),
        "lexical_token_docs": tokenMetrics.get("lexical_docs", 0),
        "lexical_token_config": tokenMetrics.get("lexical_config", 0),
        "lexical_token_repository": tokenMetrics.get("lexical_repository", lexTokCount),
        "lexical_token_pr":   prLexicalTokens.get("total", 0),
        "lexical_token_grand_total": tokenMetrics.get("lexical", 0),
        "duplication_weighted_percent":   f"{tm.get('duplication', {}).get('token_weighted', 0)*100:.1f}%",
        "code_complexity":    he.get("complexity", "Low"),
        "ai_detection_percent": f"{he.get('ai_detection', {}).get('repo_score', 0)*100:.1f}%",
        "frameworks":         " | ".join([f"{k}: {','.join(v)}" for k, v in he.get("frameworks", {}).items()]),
        "framework_frontend": " | ".join(frontendFws),
        "framework_backend":  " | ".join(backendFws),
        "databases_used":     " | ".join(infra.get("databases", [])),
        "third_party_apis":   " | ".join(infra.get("apis", [])),
        "setup_guidelines":   "Present" if doc.get("has_setup") else "Not found in README",
        "security_findings":  sec_status,
        "documentation_quality": doc.get("doc_quality", "Low"),
        "repo_rating_score":  he.get("repo_rating", {}).get("rating", 0),
        "repo_rating_label":  he.get("repo_rating", {}).get("label", "N/A"),
        "languages_percentage_bytes(>5%)": langBytePctStr,
        "frameworks_percentage_bytes(>5%)": fwBytePctStr,
        "total_time_seconds": reportData.get("performance", {}).get("total_seconds", 0),
        "total_pr_count":     prs_data.get("total_pr_count", 0),
        "open_pr_count":      prs_data.get("open_pr_count", 0),
        "closed_pr_count":     prs_data.get("closed_pr_count", 0),
        "merged_pr_count":     prs_data.get("merged_pr_count", 0),
        "total_pr_comments_count": prs_data.get("total_pr_comments_count", 0),
        "open_pr_comments_count": prs_data.get("open_pr_comments_count", 0),
        "closed_pr_comments_count": prs_data.get("closed_pr_comments_count", 0),
        "merged_pr_comments_count": prs_data.get("merged_pr_comments_count", 0),
        "total_pr_review_comment_count": prs_data.get("total_pr_review_comment_count", 0),
        "open_pr_review_comment_count": prs_data.get("open_pr_review_comment_count", 0),
        "closed_pr_review_comment_count": prs_data.get("closed_pr_review_comment_count", 0),
        "merged_pr_review_comment_count": prs_data.get("merged_pr_review_comment_count", 0),
        "github_pr_analysis_available": prs_data.get("github_pr_analysis_available", False),
    }

    meta_headers = [
        "repo_name", "tokens_llm_code", "tokens_llm_docs", "tokens_llm_config",
        "tokens_llm_repository", "tokens_llm_pr", "tokens_llm_grand_total",
        "lexical_token_code", "lexical_token_docs", "lexical_token_config",
        "lexical_token_repository", "lexical_token_pr", "lexical_token_grand_total",
        "framework_frontend", "framework_backend",
        "languages_frontend", "languages_backend",
        "language_framework_details", "domain_industry",
        "commercial_usage_summary", "security_scrubbing_confirmation",
        "full_git_history", "default_branch", "analysis_branch", "branch_selection_source",
        "repo_rating_score", "first_commit_date", "last_commit_date",
        "languages_percentage_bytes(>5%)", "frameworks_percentage_bytes(>5%)",
        "development_span_months", "commits_per_month", "unique_contributors",
        "all_contributors_count", "all_contributors",
        "total_pr_count", "open_pr_count", "closed_pr_count", "merged_pr_count",
        "total_pr_comments_count", "open_pr_comments_count",
        "closed_pr_comments_count", "merged_pr_comments_count",
        "total_pr_review_comment_count", "open_pr_review_comment_count",
        "closed_pr_review_comment_count", "merged_pr_review_comment_count",
        "github_pr_analysis_available",
        "pr_velocity_profile", "blacklist_status"
    ]

    license_type_str = tm.get("compliance", {}).get("license_type", "Unknown") or "Unknown"
    is_permissive = any(word in license_type_str.lower() for word in ["mit", "apache", "bsd", "isc"])
    commercial_summary = "Permissive" if is_permissive else "Restrictive"

    meta_row = {
        "repo_name":                  reportData.get("repo"),
        "tokens_llm_code":            tokenMetrics.get("llm_code", 0),
        "tokens_llm_docs":            tokenMetrics.get("llm_docs", 0),
        "tokens_llm_config":          tokenMetrics.get("llm_config", 0),
        "tokens_llm_repository":      tokenMetrics.get("llm_repository", tokenMetrics.get("llm", 0)),
        "tokens_llm_pr":              prLlmTokens.get("total", 0),
        "tokens_llm_grand_total":     tokenMetrics.get("llm", 0),
        "lexical_token_code":         tokenMetrics.get("lexical_code", 0),
        "lexical_token_docs":         tokenMetrics.get("lexical_docs", 0),
        "lexical_token_config":       tokenMetrics.get("lexical_config", 0),
        "lexical_token_repository":   tokenMetrics.get("lexical_repository", lexTokCount),
        "lexical_token_pr":           prLexicalTokens.get("total", 0),
        "lexical_token_grand_total":  tokenMetrics.get("lexical", 0),
        "framework_frontend":         "|".join(frontendFws),
        "framework_backend":          "|".join(backendFws),
        "languages_frontend":         "|".join(frontendLangs),
        "languages_backend":          "|".join(backendLangs),
        "language_framework_details": "|".join([f"{k}:{','.join(v)}" for k, v in he.get("frameworks", {}).items()]),
        "domain_industry":            "Unknown",
        "commercial_usage_summary":   commercial_summary,
        "security_scrubbing_confirmation": sec_status,
        "full_git_history":           all_row["git_history_intact"],
        "default_branch":             all_row["default_branch"],
        "analysis_branch":            all_row["analysis_branch"],
        "branch_selection_source":    all_row["branch_selection_source"],
        "repo_rating_score":          all_row["repo_rating_score"],
        "first_commit_date":          all_row["first_commit_date"],
        "last_commit_date":           all_row["last_commit_date"],
        "languages_percentage_bytes(>5%)": all_row["languages_percentage_bytes(>5%)"],
        "frameworks_percentage_bytes(>5%)": all_row["frameworks_percentage_bytes(>5%)"],
        "development_span_months":    all_row["development_span_months"],
        "commits_per_month":          all_row["commits_per_month"],
        "unique_contributors":        all_row["contributors"],
        "all_contributors_count":     all_row["all_contributors_count"],
        "all_contributors":           all_row["all_contributors"],
        "total_pr_count":             prs_data.get("total_pr_count", 0),
        "open_pr_count":              prs_data.get("open_pr_count", 0),
        "closed_pr_count":             prs_data.get("closed_pr_count", 0),
        "merged_pr_count":             prs_data.get("merged_pr_count", 0),
        "total_pr_comments_count":     prs_data.get("total_pr_comments_count", 0),
        "open_pr_comments_count":      prs_data.get("open_pr_comments_count", 0),
        "closed_pr_comments_count":    prs_data.get("closed_pr_comments_count", 0),
        "merged_pr_comments_count":    prs_data.get("merged_pr_comments_count", 0),
        "total_pr_review_comment_count": prs_data.get("total_pr_review_comment_count", 0),
        "open_pr_review_comment_count": prs_data.get("open_pr_review_comment_count", 0),
        "closed_pr_review_comment_count": prs_data.get("closed_pr_review_comment_count", 0),
        "merged_pr_review_comment_count": prs_data.get("merged_pr_review_comment_count", 0),
        "github_pr_analysis_available": prs_data.get("github_pr_analysis_available", False),
        "pr_velocity_profile":        pr_velocity_profile,
        "blacklist_status":           blacklist_status,
    }

    reportData["full_report_snapshot"] = {
        **all_row,  # Include everything currently in all_row
        "deployment_env":     "|".join(infra.get("deployment", [])),
        "environment_variables": doc.get("env_vars", "N/A"),
        "stars":              gh.get("stars", "N/A"),
        "forks":              gh.get("forks", "N/A"),
        "watchers":           gh.get("subscribers", "N/A"),
        "open_issues":        gh.get("open_issues", "N/A"),
        "creation_date":      gh.get("created_at", "N/A"),
        "authored_loc_verified": gt.get("loc", {}).get("breakdown", {}).get("authored", 0),
        "non_authored_loc":   gt.get("loc", {}).get("breakdown", {}).get("non_authored", 0),
        "tokenizer_method":   tm.get("tokens", {}).get("tokenizer", "N/A"),
        "pr_content_tokens_breakdown": prLlmTokens,
    }

    addGrandTokenCountsToPrJsonFiles(
        outDir,
        prLlmTokens.get("total", 0),
        prLexicalTokens.get("total", 0),
    )

    with open(outFile, "w", encoding="utf-8") as f:
        json.dump(reportData, f, indent=2)

    def appendToCsv(filename, headers, row_data):
        path = os.path.join(outputRootDir, filename)
        repoKey = "repo_name" if "repo_name" in headers else "Repository Name"
        with CSV_LOCK:
            existingRows = []
            if os.path.isfile(path):
                with open(path, "r", newline="", encoding="utf-8") as f:
                    latestByRepo = {}
                    for row in csv.DictReader(f):
                        latestByRepo[row.get(repoKey)] = row
                    existingRows = [
                        row for repoName, row in latestByRepo.items()
                        if repoName != row_data.get(repoKey)
                    ]
            tempPath = path + ".tmp"
            with open(tempPath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(existingRows)
                writer.writerow(row_data)
            os.replace(tempPath, path)

    pass

    appendToCsv("summary_all.csv", all_headers, all_row)
    appendToCsv("summary_metadata.csv", meta_headers, meta_row)

    legacy_headers = [
        "Repository Name",
        "Is Git Repository",
        "License Type",
        "Git History Intact",
        "First Commit Date",
        "Last Commit Date",
        "Development Span (Months)",
        "Authored LOC",
        "Non-Authored LOC",
        "Lines of Code (Comments)",
        "Lines of Code (Blank)",
        "Total Files",
        "Total Commits",
        "Meaningful Commit Count",
        "Commits per Month",
        "total_pr_count",
        "open_pr_count",
        "closed_pr_count",
        "merged_pr_count",
        "total_pr_comments_count",
        "open_pr_comments_count",
        "closed_pr_comments_count",
        "merged_pr_comments_count",
        "total_pr_review_comment_count",
        "open_pr_review_comment_count",
        "closed_pr_review_comment_count",
        "merged_pr_review_comment_count",
        "github_pr_analysis_available",
        "pr_velocity_profile",
        "blacklist_status",
        "Number of Contributors with commits",
        "Total Contributors (All-Time)",
        "Contributor List",
        "Branch Count",
        "Default Branch",
        "Analysis Branch",
        "Branch Selection Source",
        "LLM Tokens (Code)",
        "LLM Tokens (Docs)",
        "LLM Tokens (Config)",
        "LLM Tokens (Repository)",
        "LLM Tokens (PR/MR Content)",
        "LLM Tokens (Grand Total)",
        "Lexical Tokens (Code)",
        "Lexical Tokens (Docs)",
        "Lexical Tokens (Config)",
        "Lexical Tokens (Repository)",
        "Lexical Tokens (PR/MR Content)",
        "Lexical Tokens (Grand Total)",
        "Code Duplication (Weighted %)",
        "Code Complexity",
        "AI Detection Percentage",
        "Language Count",
        "Languages Used",
        "Frontend Languages",
        "Backend Languages",
        "Frameworks Used",
        "Frontend Frameworks",
        "Backend Frameworks",
        "Languages Percentage (Bytes > 5%)",
        "Frameworks Percentage (Bytes > 5%)",
        "Databases Used",
        "Third-Party APIs",
        "Setup Guidelines",
        "Security Findings",
        "Documentation Quality",
        "Repository Rating Score",
        "Repository Rating Label",
        "Acceptance Decision",
        "Rejection Reason"
    ]

    legacy_row = {
        "Repository Name": all_row.get("repo_name", ""),
        "Is Git Repository": all_row.get("is_git", ""),
        "License Type": all_row.get("license_type", ""),
        "Git History Intact": all_row.get("git_history_intact", ""),
        "First Commit Date": all_row.get("first_commit_date", ""),
        "Last Commit Date": all_row.get("last_commit_date", ""),
        "Development Span (Months)": all_row.get("development_span_months", ""),
        "Authored LOC": all_row.get("authored_loc", ""),
        "Non-Authored LOC": all_row.get("non_authored_loc", ""),
        "Lines of Code (Comments)": all_row.get("loc_comment", ""),
        "Lines of Code (Blank)": all_row.get("loc_blank", ""),
        "Total Files": all_row.get("loc_files", ""),
        "Total Commits": all_row.get("commits", ""),
        "Meaningful Commit Count": all_row.get("meaningful_commit_count", ""),
        "Commits per Month": all_row.get("commits_per_month", ""),
        "total_pr_count": all_row.get("total_pr_count", ""),
        "open_pr_count": all_row.get("open_pr_count", ""),
        "closed_pr_count": all_row.get("closed_pr_count", ""),
        "merged_pr_count": all_row.get("merged_pr_count", ""),
        "total_pr_comments_count": all_row.get("total_pr_comments_count", ""),
        "open_pr_comments_count": all_row.get("open_pr_comments_count", ""),
        "closed_pr_comments_count": all_row.get("closed_pr_comments_count", ""),
        "merged_pr_comments_count": all_row.get("merged_pr_comments_count", ""),
        "total_pr_review_comment_count": all_row.get("total_pr_review_comment_count", ""),
        "open_pr_review_comment_count": all_row.get("open_pr_review_comment_count", ""),
        "closed_pr_review_comment_count": all_row.get("closed_pr_review_comment_count", ""),
        "merged_pr_review_comment_count": all_row.get("merged_pr_review_comment_count", ""),
        "github_pr_analysis_available": all_row.get("github_pr_analysis_available", ""),
        "Number of Contributors with commits": all_row.get("contributors", ""),
        "Total Contributors (All-Time)": all_row.get("all_contributors_count", ""),
        "Contributor List": all_row.get("all_contributors", ""),
        "Branch Count": all_row.get("branch_count", ""),
        "Default Branch": all_row.get("default_branch", ""),
        "Analysis Branch": all_row.get("analysis_branch", ""),
        "Branch Selection Source": all_row.get("branch_selection_source", ""),
        "LLM Tokens (Code)": all_row.get("tokens_llm_code", ""),
        "LLM Tokens (Docs)": all_row.get("tokens_llm_docs", ""),
        "LLM Tokens (Config)": all_row.get("tokens_llm_config", ""),
        "LLM Tokens (Repository)": all_row.get("tokens_llm_repository", ""),
        "LLM Tokens (PR/MR Content)": all_row.get("tokens_llm_pr", ""),
        "LLM Tokens (Grand Total)": all_row.get("tokens_llm_grand_total", ""),
        "Lexical Tokens (Code)": all_row.get("lexical_token_code", ""),
        "Lexical Tokens (Docs)": all_row.get("lexical_token_docs", ""),
        "Lexical Tokens (Config)": all_row.get("lexical_token_config", ""),
        "Lexical Tokens (Repository)": all_row.get("lexical_token_repository", ""),
        "Lexical Tokens (PR/MR Content)": all_row.get("lexical_token_pr", ""),
        "Lexical Tokens (Grand Total)": all_row.get("lexical_token_grand_total", ""),
        "Code Duplication (Weighted %)": all_row.get("duplication_weighted_percent", ""),
        "Code Complexity": all_row.get("code_complexity", ""),
        "AI Detection Percentage": all_row.get("ai_detection_percent", ""),
        "Language Count": all_row.get("lang_count", ""),
        "Languages Used": all_row.get("languages", ""),
        "Frontend Languages": all_row.get("languages_frontend", ""),
        "Backend Languages": all_row.get("languages_backend", ""),
        "Frameworks Used": all_row.get("frameworks", ""),
        "Frontend Frameworks": all_row.get("framework_frontend", ""),
        "Backend Frameworks": all_row.get("framework_backend", ""),
        "Languages Percentage (Bytes > 5%)": all_row.get("languages_percentage_bytes(>5%)", ""),
        "Frameworks Percentage (Bytes > 5%)": all_row.get("frameworks_percentage_bytes(>5%)", ""),
        "Databases Used": all_row.get("databases_used", ""),
        "Third-Party APIs": all_row.get("third_party_apis", ""),
        "Setup Guidelines": all_row.get("setup_guidelines", ""),
        "Security Findings": all_row.get("security_findings", ""),
        "Documentation Quality": all_row.get("documentation_quality", ""),
        "Repository Rating Score": all_row.get("repo_rating_score", ""),
        "Repository Rating Label": all_row.get("repo_rating_label", "N/A"),
        # Project-mode values are finalized after every repository belonging to
        # the project has been evaluated. Single/batch analysis retains N/A.
        "Acceptance Decision": "N/A",
        "Rejection Reason": ""
    }

    legacyPath = os.path.join(outputRootDir, "legacy.csv")
    if os.path.isfile(legacyPath):
        # Transparently migrate legacy.csv files created by older versions so
        # appended rows always align with the expanded header.
        with CSV_LOCK:
            with open(legacyPath, "r", encoding="utf-8-sig", newline="") as f:
                existingReader = csv.DictReader(f)
                existingRows = list(existingReader)
                retiredLegacyHeaders = {
                    "Lines of Code (Code)", "total_pr_comments", "total_review_comments"
                }
                existingHeaders = [
                    header for header in (existingReader.fieldnames or [])
                    if header not in retiredLegacyHeaders
                ]
            if existingHeaders:
                # Preserve any user-defined legacy columns and append only the
                # fields introduced by this version.
                mergedLegacyHeaders = existingHeaders + [
                    header for header in legacy_headers if header not in existingHeaders
                ]
                # Keep repository PR/MR comment totals immediately to the right
                # of merged_pr_count, including when migrating an older CSV.
                commentHeaders = [
                    "total_pr_comments_count", "open_pr_comments_count",
                    "closed_pr_comments_count", "merged_pr_comments_count",
                    "total_pr_review_comment_count", "open_pr_review_comment_count",
                    "closed_pr_review_comment_count", "merged_pr_review_comment_count",
                ]
                for header in commentHeaders:
                    if header in mergedLegacyHeaders:
                        mergedLegacyHeaders.remove(header)
                mergedIndex = mergedLegacyHeaders.index("merged_pr_count") + 1
                mergedLegacyHeaders[mergedIndex:mergedIndex] = commentHeaders
                legacy_headers = mergedLegacyHeaders
            if existingHeaders and existingHeaders != legacy_headers:
                with open(legacyPath, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=legacy_headers, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(existingRows)

    appendToCsv("legacy.csv", legacy_headers, legacy_row)

    return outFile


def runInteractiveMode() -> Optional[argparse.Namespace]:
    """
    Guided terminal UI for configuring analysis parameters.
    Loops until valid configuration or exit.
    """
    while True:
        print("\n" + "=" * 34)
        print("   REPOSITORY ANALYSIS TOOL")
        print("=" * 34)
        print("1. Analyze Local Repository")
        print("2. Analyze Git Repository")
        print("3. Exit")

        choice = input("\nSelect an option: ").strip()

        if choice == "3":
            return None

        elif choice == "1":
            inputTarget = input("\nEnter local repository path: ").strip().strip('"')
            if not inputTarget or not os.path.isdir(inputTarget):
                print("[!] Error: Invalid or non-existent directory path.")
                continue

        elif choice == "2":
            inputTarget = input("\nEnter Git repository URL: ").strip().strip('"')
            if not (
                inputTarget.startswith("http://")
                or inputTarget.startswith("https://")
                or inputTarget.startswith("git@")
            ):
                print("[!] Error: Invalid Git URL.")
                continue

        else:
            print("[!] Invalid choice. Please select 1, 2, or 3.")
            continue

        print("\nSelect Analysis Mode:")
        print("1. Stage 1 (Pre-check)")
        print("2. Stage 2 (Deep Analysis)")
        print("3. Full Pipeline (Recommended)")
        mChoice = input("\nEnter choice: ").strip()

        mode = "full"
        if mChoice == "1":
            mode = "stage1"
        elif mChoice == "2":
            mode = "stage2"
        elif mChoice == "3":
            mode = "full"
        else:
            print("Invalid choice, defaulting to Full Pipeline.")

        print("\nConfigure Options:")
        mf = input("Max files to process (Press Enter for ALL files): ").strip()
        maxFiles = int(mf) if mf.isdigit() else None

        od = input("Output directory (default: ./outputs): ").strip()
        outDir = od if od else "./outputs"

        proceed = input("\nProceed? (y/n, or press Enter for yes): ").strip().lower()
        if proceed not in ("", "y", "yes"):
            continue

        print("\n" + "=" * 34)
        print("Execution Summary")
        print("=" * 34)
        print(f"Input Type   : {'Local' if choice == '1' else 'Git'}")
        print(f"Target       : {inputTarget}")
        print(f"Mode         : {mode.capitalize()}")
        print(f"Output Dir   : {outDir}")
        print(f"Max Files    : {maxFiles}")
        print("=" * 34)
        print("Starting analysis...\n")

        return argparse.Namespace(
            input=inputTarget,
            output_dir=outDir,
            clone_dir=None,
            branch=None,
            mode=mode,
            max_files=maxFiles
        )


def runAnalysis(args: argparse.Namespace) -> Any:
    """
    Execute complete analysis pipeline (Stages 0–5).
    """
    inputTarget = args.input
    requestedBranch = (getattr(args, "branch", None) or "").strip() or None
    githubToken = getattr(args, "github_token", None)
    if not githubToken:
        githubToken = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    gitlabToken = getattr(args, "gitlab_token", None)
    if not gitlabToken:
        gitlabToken = os.getenv("GITLAB_TOKEN") or os.getenv("GL_TOKEN")
    bitbucketToken = getattr(args, "bitbucket_token", None)
    if not bitbucketToken:
        bitbucketToken = os.getenv("BITBUCKET_TOKEN") or os.getenv("BB_TOKEN")
    
    startTime = time.time()

    isUrl = (
        inputTarget.startswith("http://")
        or inputTarget.startswith("https://")
        or inputTarget.startswith("git@")
    )
    targetDir = inputTarget
    branchContext: Dict[str, str] = {}

    try:
        if isUrl:
            cloneBase = args.clone_dir if args.clone_dir else os.path.join(os.getcwd(), "cloned_repos")
            os.makedirs(cloneBase, exist_ok=True)

            repoName = os.path.basename(inputTarget.rstrip("/").replace(".git", ""))
            targetDir = os.path.join(cloneBase, repoName)

            if os.path.exists(targetDir):
                logger.info(f"[cloneRepo] Using existing repository: {targetDir}")
            else:
                logger.info(f"[cloneRepo] Cloning repository to: {targetDir}")
                print("\nCloning repository (this may take a moment)...")
                success = cloneRepo(
                    inputTarget, targetDir, githubToken, gitlabToken,
                    bitbucketToken, branch=requestedBranch
                )
                if not success:
                    return False
                logger.info(f"[cloneRepo] Repository available at: {targetDir}")
            branchReady, branchContext = prepareAnalysisBranch(targetDir, requestedBranch)
            if not branchReady:
                return False
        else:
            if not os.path.exists(inputTarget):
                logger.error(f"[runAnalysis] Path does not exist: {inputTarget}")
                return False
            if requestedBranch:
                logger.error(
                    "[branch] Explicit branch selection is supported for cloned repository URLs only; "
                    "local repositories are never switched automatically."
                )
                return False

        # Strict isolation: ensure we are at the repo root or targeting a directory
        if not os.path.isdir(targetDir):
            logger.error(f"[runAnalysis] Target is not a directory: {targetDir}")
            return False

        # Smart Git root discovery
        gitDir = os.path.join(targetDir, ".git")
        if not os.path.isdir(gitDir):
            try:
                for d in os.listdir(targetDir):
                    sub = os.path.join(targetDir, d)
                    if os.path.isdir(sub) and os.path.isdir(os.path.join(sub, ".git")):
                        logger.info(f"[runAnalysis] Found nested .git in: {sub}")
                        targetDir = sub
                        gitDir = os.path.join(targetDir, ".git")
                        break
            except Exception:
                pass

        isGit = os.path.isdir(gitDir)

        report = {
            "repo": os.path.basename(targetDir),
            "target_path": targetDir,
            "mode": args.mode,
            "is_git": isGit,
            "ground_truth": {},
            "tool_metrics": {},
            "heuristics": {},
            "performance": {}
        }
        fileList = []

        print("[Stage 0] Extracting git metadata...")
        s0Start = time.time()
        gitData = runStage0GitAnalysis(targetDir)
        if isUrl:
            gitData.update(branchContext)
        elif isGit:
            gitData["branch_selection_source"] = "local_current_branch"
        report["ground_truth"]["git"] = {
            "commit_count": gitData.get("commit_count", 0),
            "unique_contributors": gitData.get("unique_contributors", 0),
            "branch_count": gitData.get("branch_count", 0),
            "meaningful_commit_count": gitData.get("meaningful_commit_count", 0),
            "last_update": gitData.get("last_update"),
            "first_commit": gitData.get("first_commit_date"),
            "active_span_months": gitData.get("active_span_months", 0),
            "history_integrity": gitData.get("history_integrity", {}),
            "all_contributors": gitData.get("all_contributors", []),
            "all_contributors_count": len(gitData.get("all_contributors", [])),
            "default_branch": gitData.get("default_branch"),
            "analysis_branch": gitData.get("analysis_branch"),
            "branch_selection_source": gitData.get("branch_selection_source"),
            "source": "git"
        }
        report["performance"]["stage0_seconds"] = round(time.time() - s0Start, 2)

        # Calculate OS-safe outDir matching saveReport logic
        repoName = os.path.basename(os.path.abspath(targetDir))
        if isUrl:
            safeName = inputTarget.rstrip("/").split("/")[-1].replace(".git", "")
            prOutDir = os.path.join(args.output_dir, safeName)
        else:
            prOutDir = os.path.join(args.output_dir, repoName)

        gitlab_info = extractGitLabRepoInfo(targetDir, inputTarget)
        bitbucket_info = None if gitlab_info else extractBitbucketRepoInfo(targetDir, inputTarget)

        if gitlab_info:
            print("[Stage 0.5] Running GitLab MR analytics...")
            s05Start = time.time()
            prMetrics = runGitLabMrAnalysis(targetDir, inputTarget, gitlabToken, prOutDir)
            report["github_prs"] = prMetrics
            report["performance"]["stage0_5_seconds"] = round(time.time() - s05Start, 2)
        elif bitbucket_info:
            print("[Stage 0.5] Running Bitbucket PR analytics...")
            s05Start = time.time()
            prMetrics = runBitbucketPrAnalysis(targetDir, inputTarget, bitbucketToken, prOutDir)
            report["github_prs"] = prMetrics
            report["performance"]["stage0_5_seconds"] = round(time.time() - s05Start, 2)
        else:
            print("[Stage 0.5] Running GitHub PR analytics...")
            s05Start = time.time()
            prMetrics = runGitHubPrAnalysis(targetDir, inputTarget, githubToken, prOutDir)
            report["github_prs"] = prMetrics
            report["performance"]["stage0_5_seconds"] = round(time.time() - s05Start, 2)

        mergeCommitIdentities = prMetrics.pop("_merge_commit_identities", {})
        commitRows = gitData.get("all_commits_detailed", [])
        attachMergeCommitUsernames(commitRows, mergeCommitIdentities)
        normalizeCommitAuthorsToUsernames(commitRows)
        dumpCommitsCsv(prOutDir, sanitizeFilename(repoName), commitRows)
        extractionInventory = extractLocalHistoryAndAutomation(
            targetDir, prOutDir, repoName, commitRows
        )
        report["supplemental_extraction"] = {
            "git_history_commit_count": len(extractionInventory["git_history"]["commits"]),
            "branch_count": len(extractionInventory["git_history"]["branches"]),
            "tag_count": len(extractionInventory["git_history"]["tags"]),
            "test_file_count": len(extractionInventory["tests_ci_inventory"]["test_files"]),
            "ci_configuration_file_count": len(extractionInventory["tests_ci_inventory"]["ci_configuration_files"]),
            "retained_test_report_count": len(extractionInventory["tests_ci_inventory"]["retained_test_or_coverage_reports"]),
        }

        print("[Stage 1] Scanning repository structure and signals...")
        stage1Data, fileList = runStage1Analysis(targetDir, maxFiles=args.max_files)
        report["heuristics"]["signals"] = stage1Data.get("signals", {})
        report["heuristics"]["structure"] = stage1Data.get("structure", {})

        print("[Layer 1] Fetching total and authored LOC via SCC...")
        totalSccData = runScc(targetDir, authoredOnly=False)
        authoredSccData = runScc(targetDir, authoredOnly=True)
        authoredCodeFiles = authoredSccData.get("source_files", [])
        if totalSccData.get("totals") and authoredSccData.get("totals"):
            totalStats = totalSccData["totals"]
            # This is the exact filtered SCC Code total requested by the LOC
            # policy. Source-code tokens use the narrower source_files manifest;
            # SCC-recognized docs/configs remain separate token categories.
            authoredStats = authoredSccData["totals"]
            authoredLoc = authoredStats["code"]
            totalLoc = totalStats["code"]
            nonAuthoredLoc = max(0, totalLoc - authoredLoc)
            report["ground_truth"]["loc"] = {
                "value": authoredLoc,
                "breakdown": {
                    "authored": authoredLoc,
                    "non_authored": nonAuthoredLoc,
                    "total": totalLoc,
                    "comment": totalStats["comment"],
                    "blank": totalStats["blank"],
                    "nFiles": totalStats["nFiles"],
                    "authored_comment": authoredStats["comment"],
                    "authored_blank": authoredStats["blank"],
                    "authored_nFiles": authoredStats["nFiles"],
                },
                "source": "scc",
                "definition": (
                    "Authored LOC excludes SCC-detected generated files and configured "
                    "non-authored dependency/vendor/build/migration directories."
                ),
            }

            # Language analysis follows the authored/eligible SCC population.
            langBreakdown = {}
            for lang, stats in authoredSccData.get("languages", {}).items():
                langBreakdown[lang] = {
                    "loc": stats["code"],
                    "files": stats["nFiles"],
                    "pct": round((stats["code"] / authoredLoc * 100), 2) if authoredLoc > 0 else 0
                }

            # Differentiate between core (code) and total (all) languages
            coreLangs = [lang for lang in langBreakdown.keys() if lang not in NON_CORE_FORMATS]

            report["ground_truth"]["languages"] = {
                "total_count": len(langBreakdown),
                "core_count": len(coreLangs),
                "core_list": coreLangs,
                "list": list(langBreakdown.keys()),
                "breakdown": langBreakdown,
                "source": "scc"
            }
        else:
            logger.warning("[Layer 1] SCC failed or returned empty; verified LOC is unavailable.")
            authoredCodeFiles = []
            report["ground_truth"]["loc"] = {"value": 0, "source": "none", "notes": "scc failure"}
            report["ground_truth"]["languages"] = {
                "count": 0,
                "list": [],
                "breakdown": {},
                "source": "none"
            }


        loc_val = report["ground_truth"].get("loc", {}).get("value", 0)

        if args.mode in {"stage2", "full"}:
            print("[Stage 2] Running deep analysis (Tokens & Duplication)...")
            s2Start = time.time()

            stage2Result, fileStats, fileTokensMap, framework_findings = runStage2Analysis(
                fileList, authoredCodeFiles=authoredCodeFiles
            )
            llmMetrics = stage2Result.get("metrics", {}).get("llm_tokens", {})
            lexicalMetrics = stage2Result.get("metrics", {}).get("lexical_tokens", {})
            repoLlmTokens = llmMetrics.get("total", 0)
            repoLexicalTokens = lexicalMetrics.get("total", 0)
            prLlmTokens = prMetrics.get("pr_content_tokens", {}).get("total", 0)
            prLexicalTokens = prMetrics.get("pr_content_tokens_lexical", {}).get("total", 0)
            report["tool_metrics"]["tokens"] = {
                "llm": repoLlmTokens + prLlmTokens,
                "llm_repository": repoLlmTokens,
                "llm_code": llmMetrics.get("code", 0),
                "llm_docs": llmMetrics.get("docs", 0),
                "llm_config": llmMetrics.get("config", 0),
                "llm_pr": prLlmTokens,
                "lexical": repoLexicalTokens + prLexicalTokens,
                "lexical_repository": repoLexicalTokens,
                "lexical_code": lexicalMetrics.get("code", 0),
                "lexical_docs": lexicalMetrics.get("docs", 0),
                "lexical_config": lexicalMetrics.get("config", 0),
                "lexical_pr": prLexicalTokens,
            }
            report["tool_metrics"]["duplication"] = stage2Result.get("duplication_metrics", {})
            report["performance"]["stage2_seconds"] = round(time.time() - s2Start, 2)

            print("[Layer 3] Computing heuristic scores (AI detection, Rating)...")
            aiData = runAiDetectionAnalysis(
                fileStats,
                report["tool_metrics"]["duplication"],
                fileTokensMap,
            )
            report["heuristics"]["ai_detection"] = aiData

            languages = report.get("ground_truth", {}).get("languages", {}).get("list", [])
            manifestFws = detectFrameworks(targetDir)
            hasJs = any(lang in ["JavaScript", "TypeScript"] for lang in languages)

            hasPackageJson = os.path.exists(os.path.join(targetDir, "package.json"))

            nodeRuntime = []
            if hasJs and hasPackageJson:
                nodeRuntime.append("Node.js")
            finalFws = manifestFws
            for k, v in framework_findings.items():
                if k not in finalFws:
                    finalFws[k] = []
                finalFws[k].extend(v)
                finalFws[k] = list(set(finalFws[k]))

            report["heuristics"]["frameworks"] = finalFws
            report["heuristics"]["runtime"] = nodeRuntime
            report["tool_metrics"]["compliance"] = detectOpenSource(targetDir)

            # Infrastructure, Testing & Documentation
            report["heuristics"]["infrastructure"] = detectInfrastructure(targetDir)
            report["heuristics"]["byte_breakdown"] = calculateByteBreakdown(targetDir)
            report["heuristics"]["docs"] = analyzeDocumentation(targetDir)
            report["heuristics"]["testing"] = {
                "coverage": detectCoverage(targetDir),
                "case_count": estimateTestCases(fileList)
            }
            report["heuristics"]["complexity"] = computeComplexityScore(
                loc_val,
                report["tool_metrics"]["tokens"]["llm_repository"],
                len(fileList)
            )
            report["tool_metrics"]["code_age"] = analyzeCodeAge(targetDir, fileList)
            ratingData = computeRepoRating(
                gitData,
                report["heuristics"],
                report["ground_truth"],  # Pass full ground truth
                report["ground_truth"]["languages"],
            )
            report["heuristics"]["repo_rating"] = ratingData
            report["performance"]["stage4_seconds"] = round(time.time() - s2Start, 2)

        if args.mode in {"full"}:
            print("[Stage 5] Scanning for secrets...")
            s5Start = time.time()
            secretData = scanSecrets(fileList)
            report["tool_metrics"]["secrets"] = secretData
            report["performance"]["stage5_seconds"] = round(time.time() - s5Start, 2)

        if isUrl:
            gitlab_info = extractGitLabRepoInfo(targetDir, inputTarget)
            if gitlab_info:
                domain, project_path, _ = gitlab_info
                print("[GitLab API] Enriching via GitLab REST API...")
                glData = enrichViaGitLabApi(domain, project_path, gitlabToken)
                report["github_api"] = glData
            elif bitbucket_info:
                domain, workspace, repo, is_cloud = bitbucket_info
                print("[Bitbucket API] Enriching via Bitbucket REST API...")
                bbData = enrichViaBitbucketApi(domain, workspace, repo, bitbucketToken, is_cloud)
                report["github_api"] = bbData
            elif githubToken or HAS_PYGITHUB:
                print("[GitHub API] Enriching via GitHub REST API...")
                ghData = enrichViaGithubApi(inputTarget, githubToken)
                report["github_api"] = ghData

        report["performance"]["total_seconds"] = round(time.time() - startTime, 2)
        outFile = saveReport(report, targetDir, inputTarget, args.output_dir, isUrl)

        gt = report.get("ground_truth", {})
        tm = report.get("tool_metrics", {})
        he = report.get("heuristics", {})

        loc_val = gt.get("loc", {}).get("value", 0)
        llm_tok = tm.get("tokens", {}).get("llm", 0)
        repo_llm_tok = tm.get("tokens", {}).get("llm_repository", llm_tok)
        pr_llm_tok = tm.get("tokens", {}).get("llm_pr", 0)
        lexical_tok = tm.get("tokens", {}).get("lexical", 0)
        repo_lexical_tok = tm.get("tokens", {}).get("lexical_repository", lexical_tok)
        pr_lexical_tok = tm.get("tokens", {}).get("lexical_pr", 0)
        commits = gt.get("git", {}).get("commit_count", "N/A")
        persons = gt.get("git", {}).get("unique_contributors", "N/A")
        all_persons = gt.get("git", {}).get("all_contributors_count", "N/A")
        all_persons_list = gt.get("git", {}).get("all_contributors", [])
        all_persons_names = ", ".join([c.get("name", "") for c in all_persons_list])
        if len(all_persons_names) > 120:
            all_persons_names = all_persons_names[:117] + "..."
        langs = gt.get("languages", {}).get("list", [])
        rating = he.get("repo_rating", {})
        dup_pct = tm.get("duplication", {}).get("token_weighted", 0.0) * 100
        sec_count = tm.get("secrets", {}).get("findings_count", 0)
        is_os = tm.get("compliance", {}).get("is_open_source", "N/A")
        git_ok = gt.get("git", {}).get("history_integrity", {}).get("appears_intact", "N/A")

        prMetrics = report.get("github_prs", {})
        pr_avail = prMetrics.get("github_pr_analysis_available", False)
        pr_total = prMetrics.get("total_pr_count", "N/A") if pr_avail else "N/A"
        pr_open = prMetrics.get("open_pr_count", "N/A") if pr_avail else "N/A"
        pr_closed = prMetrics.get("closed_pr_count", "N/A") if pr_avail else "N/A"
        pr_merged = prMetrics.get("merged_pr_count", "N/A") if pr_avail else "N/A"

        primaryLangs = [lang for lang in langs if lang not in NON_CORE_FORMATS]

        print("\n" + "=" * 44)
        print("  Analysis Complete — v3.0.0 (Production Refactor)")
        print("=" * 44)
        non_authored_loc = gt.get("loc", {}).get("breakdown", {}).get("non_authored", 0)
        print(f"  Authored LOC     : {loc_val:,}")
        print(f"  Non-Authored LOC : {non_authored_loc:,}")
        print(f"  LLM Tokens (Repo): {repo_llm_tok:,}")
        print(f"  LLM Tokens (PRs) : {pr_llm_tok:,}")
        print(f"  LLM Tokens Total : {llm_tok:,}")
        print(f"  Lexical (Repo)   : {repo_lexical_tok:,}")
        print(f"  Lexical (PRs)    : {pr_lexical_tok:,}")
        print(f"  Lexical Total    : {lexical_tok:,}")
        print(f"  Commits          : {commits}")
        print(f"  Contributors (U) : {persons}")
        print(f"  Contributors (All): {all_persons}")
        print(f"  All Contrib Names: {all_persons_names}")
        print(f"  Languages        : {', '.join(primaryLangs[:5])}")
        print(f"  Rating           : {rating.get('rating', 'N/A')} / 10  ({rating.get('label', '')})")
        print(f"  Duplication      : {dup_pct:.1f}% (token-weighted)")
        print(f"  Secrets found    : {sec_count}")
        print(f"  Open Source      : {is_os}")
        print(f"  Git History OK   : {git_ok}")
        if gitlab_info:
            pr_label = "GitLab MRs"
        elif bitbucket_info:
            pr_label = "Bitbucket PRs"
        else:
            pr_label = "GitHub PRs"
        print(f"  {pr_label} (Tot) : {pr_total}")
        print(f"  {pr_label} (Opn) : {pr_open}")
        print(f"  {pr_label} (Cld) : {pr_closed}")
        print(f"  {pr_label} (Mrg) : {pr_merged}")
        print(f"\n  Report saved to  :\n  {outFile}")
        if isUrl:
            print(f"  Repo cloned to   :\n  {targetDir}")
        print("=" * 44)

        if getattr(args, "capture_report", False):
            return {
                "success": True,
                "report": report,
                "report_file": outFile,
                "target_dir": targetDir,
            }
        return True

    except KeyboardInterrupt:
        print("\n[!] Pipeline interrupted safely.")
        return False
    except Exception as e:
        logger.error(f"[runAnalysis] Pipeline failed for '{inputTarget}': {e}", exc_info=True)
        print(f"\n[!] Analysis failed for {inputTarget}: {e}")
        return False


def _safeOutputName(value: str) -> str:
    """Return a stable directory-safe name for a user supplied project name."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return safe or "project"


def runProjectsAnalysis(args: argparse.Namespace) -> str:
    """Analyze and evaluate all projects declared in a user-friendly CSV."""
    from project_acceptance import (
        evaluate_project,
        evaluate_repository,
        load_policy,
        load_projects_csv,
        normalize_repository_metrics,
        print_portfolio_summary,
        save_combined_legacy,
        save_portfolio_results,
        update_legacy_acceptance,
    )

    projects = load_projects_csv(args.projects)
    policy = load_policy(getattr(args, "policy", None))
    total_repositories = sum(len(project["repositories"]) for project in projects)

    print("\nProject Input Summary")
    print("=" * 44)
    print(f"Projects     : {len(projects)}")
    print(f"Repositories : {total_repositories}")
    for project in projects:
        print(f"  - {project['name']}: {len(project['repositories'])} repo(s)")
    print("=" * 44)

    project_results = []
    project_outputs = []
    for project_index, project in enumerate(projects, 1):
        print(f"\n[Project {project_index}/{len(projects)}] {project['name']}")
        repo_results = []
        safe_project_name = _safeOutputName(project["name"])
        project_output = os.path.join(args.output_dir, safe_project_name)
        clone_base = args.clone_dir if args.clone_dir else os.path.join(os.getcwd(), "cloned_repos")
        project_clone_dir = os.path.join(clone_base, safe_project_name)

        for repo_index, repository in enumerate(project["repositories"], 1):
            print(
                f"\n[Repository {repo_index}/{len(project['repositories'])}] "
                f"{repository['name']} "
                f"(branch: {repository.get('branch') or 'repository default'})"
            )
            repo_args = argparse.Namespace(
                input=repository["target"],
                output_dir=project_output,
                clone_dir=project_clone_dir,
                branch=repository.get("branch"),
                mode=args.mode,
                max_files=args.max_files,
                github_token=args.github_token,
                gitlab_token=args.gitlab_token,
                bitbucket_token=args.bitbucket_token,
                capture_report=True,
            )
            analysis = runAnalysis(repo_args)
            if not isinstance(analysis, dict) or not analysis.get("success"):
                repo_results.append({
                    "name": repository["name"],
                    "target": repository["target"],
                    "branch": repository.get("branch"),
                    "decision": "ANALYSIS_ERROR",
                    "metrics": {},
                    "checks": [],
                    "error": "Repository analysis did not complete",
                })
                continue

            metrics = normalize_repository_metrics(analysis["report"])
            evaluation = evaluate_repository(metrics, policy)
            repo_results.append({
                "name": repository["name"],
                "target": repository["target"],
                "branch": repository.get("branch"),
                "report_file": analysis["report_file"],
                "metrics": metrics,
                **evaluation,
            })

        project_result = evaluate_project(project, repo_results, policy)
        project_results.append(project_result)
        update_legacy_acceptance(project_output, project_result)
        project_outputs.append((project_result, project_output))

    saved = save_portfolio_results(project_results, args.output_dir)
    saved["combined_legacy"] = save_combined_legacy(project_outputs, args.output_dir)
    print_portfolio_summary(project_results, saved)
    return saved["decision"]


def main():
    """Main entry point for the repository analysis tool."""
    dotenv_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        if key and key not in os.environ:
                            os.environ[key] = val
        except Exception as e:
            print(f"[!] Warning: Failed to load .env file: {e}")

    try:
        validateSccVersion()
    except RuntimeError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Repository Intelligence CLI Tool— Multi-stage code analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Analyse a local repo (full pipeline):
    python Repo_analysis_tool.py -i ./my-project

  Analyse a remote GitHub repo:
    python Repo_analysis_tool.py -i https://github.com/user/repo.git

  Analyse a private repo with GitHub API enrichment:
    python Repo_analysis_tool.py -i https://github.com/org/private-repo.git --github-token ghp_xxx

  Batch-analyse from a text file (one URL/path per line):
    python Repo_analysis_tool.py --batch repos.txt -o ./reports

  Evaluate one or more projects from a simple CSV:
    python Repo_analysis_tool.py --projects projects.csv --mode full

  Create the projects CSV through an interactive wizard:
    python Repo_analysis_tool.py --project-wizard

  Stage 1 structure-only pre-check:
    python Repo_analysis_tool.py -i ./my-app --mode stage1
        """,
    )
    parser.add_argument("--input",  "-i", type=str, help="Local folder path OR Git repository URL")
    parser.add_argument("--output-dir", "-o", type=str, default="./outputs",
                        help="Output directory for reports (default: ./outputs)")
    parser.add_argument("--clone-dir", "-c", type=str, default=None,
                        help="Base directory for cloned repositories (default: ./cloned_repos)")
    parser.add_argument("--branch", type=str, default=None,
                        help="Exact branch to clone/analyze for a repository URL (default: remote default branch)")
    parser.add_argument("--mode", "-m", choices=["stage1", "stage2", "full"], default="full",
                        help="Pipeline mode: stage1=structure, stage2=deep, full=all (default: full)")
    parser.add_argument("--max-files", type=int, default=None,
                        help="Soft cap on number of files processed")
    parser.add_argument("--github-token", type=str, default=None,
                        help="GitHub Personal Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--gitlab-token", type=str, default=None,
                        help="GitLab Personal Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--bitbucket-token", type=str, default=None,
                    help="Bitbucket App Password / Access Token for API enrichment (needed for private repos)")
    parser.add_argument("--batch", type=str, default=None,
                        help="Path to a text file containing one repo URL or local path per line")
    parser.add_argument("--projects", type=str, default=None,
                        help="CSV with project_name, repo_url, and branch columns")
    parser.add_argument("--project-wizard", action="store_true",
                        help="Interactively create a projects CSV and analyze it")
    parser.add_argument("--wizard-output", type=str, default="projects.csv",
                        help="CSV path created by --project-wizard (default: projects.csv)")
    parser.add_argument("--policy", type=str, default=None,
                        help="Optional JSON/YAML acceptance-policy override")

    if len(sys.argv) == 1:
        while True:
            args = runInteractiveMode()
            if not args:
                break

            runAnalysis(args)

            print("\nWhat would you like to do next?")
            print("1. Analyze another repository")
            print("2. Exit")
            nextAction = input("\nSelect an option (or press Enter to continue): ").strip()

            if nextAction not in ("", "1"):
                break
    else:
        args = parser.parse_args()
        if args.input:
            args.input = args.input.strip().strip('"')

        if args.project_wizard:
            from project_acceptance import create_projects_csv_wizard, load_projects_csv
            args.projects = create_projects_csv_wizard(args.wizard_output)
            wizard_projects = load_projects_csv(args.projects)
            wizard_repo_count = sum(len(p["repositories"]) for p in wizard_projects)
            print(f"\nSaved {len(wizard_projects)} project(s) and {wizard_repo_count} repository/repositories to:")
            print(f"  {args.projects}")
            if input("Start analysis now? [Y/n]: ").strip().lower() in {"n", "no"}:
                return

        if args.projects:
            try:
                decision = runProjectsAnalysis(args)
            except ValueError as exc:
                print(f"[!] {exc}")
                sys.exit(2)
            sys.exit(0 if decision == "ACCEPT" else 1 if decision == "REJECT" else 3)

        elif args.batch:
            if not os.path.isfile(args.batch):
                print(f"[!] Batch file not found: {args.batch}")
                sys.exit(1)
            with open(args.batch, "r", encoding="utf-8") as f:
                targets = [line.strip().strip('"') for line in f if line.strip() and not line.startswith("#")]
            print(f"[Batch] Processing {len(targets)} repositories...")
            success_count = 0
            for idx, target in enumerate(targets, 1):
                print(f"\n[Batch {idx}/{len(targets)}] {target}")
                batch_args = argparse.Namespace(
                    input=target,
                    output_dir=args.output_dir,
                    clone_dir=args.clone_dir,
                    branch=None,
                    mode=args.mode,
                    max_files=args.max_files,
                    github_token=args.github_token,
                    gitlab_token=args.gitlab_token,
                    bitbucket_token=args.bitbucket_token,
                )
                if runAnalysis(batch_args):
                    success_count += 1
            print(f"\n[Batch] Completed: {success_count}/{len(targets)} succeeded.")
            print(f"[Batch] Summary CSV: {os.path.join(args.output_dir, 'summary_all.csv')}")

        elif args.input:
            runAnalysis(args)

        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
