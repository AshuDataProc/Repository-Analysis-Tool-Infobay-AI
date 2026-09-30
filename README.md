# Repository Intelligence CLI Tool (v3.0.0)

A high-performance, multi-stage static analysis pipeline designed to transform raw codebases into actionable intelligence. This tool performs deep analysis on Git metadata, code metrics, architectural patterns, and security risks.

## 🛠️ Setup & Requirement

### 1. Python Environment

Ensure you have Python 3.8+ installed. Install the required libraries:

### 2. Create Virtual Environment

```powershell
python -m venv venv
```

### 3. Activate Virtual Environment

```
.\venv\Scripts\Activate.ps1
```

### 4. Variable Naming Consistency

```powershell
pip install -r requirements.txt
```

### 5. Install `scc` (Critical for accuracy)

The tool uses `scc` for total repository LOC, authored/non-authored LOC,
comment/blank counts, language breakdowns, and the authored source-file token manifest.

#### Windows Setup

* **Recommendation**: Install via Chocolatey:
  ```powershell
  choco install scc
  ```

  Or place `scc.exe` in the tool directory or your system's PATH.

#### macOS Setup

If you are using macOS, you can install `scc` using Homebrew:

* **Install Homebrew (if not installed)**:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
* **Install scc**:
  ```bash
  brew install scc
  ```

#### Linux Setup

On Linux, install SCC with Go or use a binary from the official SCC releases:

```bash
go install github.com/boyter/scc/v3@latest
```

#### Verify Installation

```bash
scc --version
```

If the version number is displayed successfully, `scc` is installed correctly.

### 6 Git Installation

Ensure `git` is installed and available in your terminal so the tool can clone remote repositories and analyze commit history.

## 📖 Usage

### Interactive Menu (Recommended)

Simply run the tool without arguments for an easy-to-use menu:

```powershell
python Repo_analysis_tool.py
```

### Batch Mode

To analyze multiple repositories at once, create a `repos.txt` file (one URL or path per line):

```powershell
python Repo_analysis_tool.py --batch repos.txt -o ./outputs
```

### Project acceptance (CSV + wizard)

For project-level acceptance, use a CSV with a required `branch` column:

```csv
project_name,repo_url,branch
Payment Platform,https://github.com/company/payment-api.git,
Payment Platform,https://github.com/company/payment-ui.git,develop
Notification Service,https://github.com/company/notification-api.git,release/2.0
```

Rows with the same `project_name` are evaluated as one project. A project may
contain one or many repositories. A ready-to-copy file is available at
`projects.example.csv`.

Provide an exact branch name for every repository row. Blank or missing branch
values fail explicitly before analysis starts. Missing or invalid requested
branches also fail explicitly; the tool never silently falls back to the
default. Explicit branch selection applies to repository URLs, while local
working copies are never switched automatically.

```powershell
python Repo_analysis_tool.py --projects projects.csv --mode full
```

Each project keeps its own `legacy.csv`. The root output directory also receives
`combined_legacy.csv`, containing every declared project/repository in one file.
Its first columns are `Project Name`, `Repository Target`, and `Requested Branch`.
The combined file is replaced atomically on each portfolio run, so rows from an
older `projects.csv` do not remain in the latest result.

Non-technical users can create the CSV interactively and start analysis without
editing a file:

```powershell
python Repo_analysis_tool.py --project-wizard
```

Default acceptance rules are built in. Advanced users can override thresholds
with `--policy acceptance-policy.yaml`; see `acceptance-policy.example.yaml`.

Repository-level rules apply to every repository: private visibility, no
detected license file/type, a meaningful root README, intact Git history, at
least 20 eligible source files, and at least two human contributors. Project-level rules require any one
repository to have at least 5,000 eligible source LOC, any one repository to
span six months from first to last commit, and the same repository to have at
least 100 merged PRs and 100 commits.

Project evaluation returns `ACCEPT`, `REJECT`, or `MANUAL_REVIEW`. Unavailable
API facts such as visibility or PR data produce `MANUAL_REVIEW`, not a false
rejection. JSON and CSV portfolio reports are written to the output directory.

### CLI Commands

* **Analyze Local Directory**:
  ```bash
  python Repo_analysis_tool.py -i "C:\path\to\project" -o .\outputs --mode full
  ```
* **Analyze Remote Repository**:
  ```bash
  python Repo_analysis_tool.py -i https://github.com/user/repo.git --mode full
  ```
* **Analyze Remote Repository with GitHub PR Analytics**:
  Set the environment variable (or pass `--github-token`) to run the PR analytics Stage 0.5:
  ```powershell
  $env:GITHUB_TOKEN="your_pat_token"
  python Repo_analysis_tool.py -i https://github.com/user/repo.git
  ```

## 🚀 Key Features

### 1. Multi-Stage Analysis Pipeline

The tool executes analysis in organized layers to ensure a separation between ground truth (verified tools) and heuristic estimates:

- **Stage 0 (Git Meta)**: Extracts all commits reachable from fetched refs, full commit messages, parents, ref associations, branches, tags, contributor diversity, active span, and history-integrity signals.
- **Stage 0.5 (PR/MR Analytics)**: Extracts PR counts, metadata, general comments, inline review comments, formal review/approval metadata, commit messages, changed-file metadata, and available patch text for GitHub, GitLab, and Bitbucket. All three providers write issue and CI/build-result artifacts when their APIs expose the data. Bitbucket Server/Data Center has no native repository issue API, so linked Jira issues require a separate Jira integration.
- **Stage 1 (Structure)**: Scans directory hierarchy for architectural signals and framework manifests.
- **Stage 2 (Deep Metrics)**: Calculates code, documentation, and configuration token counts (via `tiktoken`), plus cross-file code duplication. Reports expose repository tokens, PR tokens, and their grand total separately.
- **Stage 3 (AI Detection)**: Uses entropy and token distribution heuristics to identify AI-generated code.
- **Stage 4 (Intelligence)**: Categorizes Frontend vs. Backend logic, detects infrastructure (Databases, Cloud, APIs) at all depths, and evaluates documentation quality.
- **Stage 5 (Security)**: Scans for exposed credentials, AWS keys, and database connection strings.

### Supplemental extraction artifacts

Each repository output directory can contain:

- `git_history_<repository>.json`: commits reachable from all fetched refs, branches, tags, and explicit completeness limitations.
- `issues_<repository>.json`: available issues and issue comments for GitHub, GitLab, and Bitbucket Cloud; Bitbucket Server records the native-issue limitation.
- `ci_results_<repository>.json`: available GitHub Actions, GitLab pipeline/job, Bitbucket Cloud Pipelines, or Bitbucket Server build-status results.
- `tests_ci_inventory_<repository>.json`: detected test files, CI definitions, and retained test/coverage reports. Tests are not executed automatically.
- `all_prs_<repository>.json`: PR/MR metadata plus available comments, reviews, changed files, and patches.

No artifact claims absolute completeness. Deleted or unfetched Git objects,
provider-truncated patches, deleted collaboration content, and expired or
permission-restricted CI data cannot be recovered by the extractor.

### 2. Advanced Infrastructure Detection (v3.0.0)

- **Canonical Reporting**: Automatically groups database aliases (e.g., `postgres` and `postgresql` → `PostgreSQL`).
- **Greedy Scanning**: Peeks inside source code files (`.py`, `.js`, `.go`, etc.) to identify library imports and connection strings.
- **Full-Depth Scanning**: Recursively analyzes the entire repository without depth limits.

### 3. Professional CSV Reporting

Generates standardized CSV outputs for at-scale repository auditing:

- **`summary_all.csv`**: A comprehensive dataset featuring 40 parameters including contributor mapping, complexity, architectural splits, and security findings.
- **`summary_metadata.csv`**: A curated executive summary focused on commercial usage, security status, and core architectural labels.

*For a detailed breakdown of what each CSV column means, please refer to the [`csv_schema.md`](./csv_schema.md) document.*
*For more technical insights on how the tool processes data, read the [`architecture.md`](./architecture.md).*

### 4. Robust Input Handling

- **Quote-Resistant Paths**: Automatically strips double-quotes from paths pasted from Windows File Explorer ("Copy as path").
- **Batch Processing**: Supports a single `.txt` file containing a mix of local directory paths and remote Git URLs.

## 📊 Outputs

The tool generates professional-grade reports in the `./outputs` folder:

1. **`summary_all.csv`**: Master dataset for data processing and audit reporting.
2. **`summary_metadata.csv`**: Curated metadata report for executive review.
3. **`{repo}_report.json`**: Deep-dive technical breakdown for each analyzed repository.

---

*Developed for Advanced Repository Intelligence & Technical Auditing.*
