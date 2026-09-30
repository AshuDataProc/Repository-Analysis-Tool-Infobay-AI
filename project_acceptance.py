"""Project/repository acceptance gates for Repo_analysis_tool.

This module intentionally keeps fact collection separate from policy decisions.
The analyzer produces a report, this module normalizes it and evaluates the
configured repository and project requirements.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_POLICY: Dict[str, Any] = {
    "repository_rules": {
        "private_required": True,
        "license_absent_required": True,
        "readme_required": True,
        "readme_minimum_characters": 200,
        "minimum_eligible_source_files": 20,
        "minimum_human_contributors": 2,
        "git_history_intact_required": True,
    },
    "project_rules": {
        "minimum_merged_prs": 100,
        "minimum_commits_same_repository": 100,
        "minimum_eligible_source_loc": 5000,
        "minimum_development_span_months": 6,
    },
}

BOT_PATTERNS = (
    "[bot]", "dependabot", "renovate", "github-actions", "gitlab-ci",
    "jenkins", "buildkite", "circleci", "travis", "automation",
)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """Load an optional JSON/YAML override over the safe built-in policy."""
    if not path:
        return _deep_merge(DEFAULT_POLICY, {})
    if not os.path.isfile(path):
        raise ValueError(f"Policy file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as handle:
        if ext == ".json":
            override = json.load(handle)
        elif ext in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError("PyYAML is required for YAML policy files") from exc
            override = yaml.safe_load(handle) or {}
        else:
            raise ValueError("Policy must be a .json, .yaml, or .yml file")
    if not isinstance(override, dict):
        raise ValueError("Policy root must be an object/map")
    return _deep_merge(DEFAULT_POLICY, override)


def derive_repo_name(target: str) -> str:
    clean = target.strip().strip('"').rstrip("/\\")
    name = re.split(r"[/\\]", clean)[-1]
    return name[:-4] if name.lower().endswith(".git") else name


def load_projects_csv(path: str) -> List[Dict[str, Any]]:
    """Load project_name, repo_url/repo_path, and required branch from CSV."""
    if not os.path.isfile(path):
        raise ValueError(f"Projects CSV not found: {path}")

    projects: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    seen_targets = set()
    errors: List[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = {str(h).strip().lower() for h in (reader.fieldnames or [])}
        target_column = "repo_url" if "repo_url" in headers else "repo_path" if "repo_path" in headers else None
        if "project_name" not in headers or not target_column or "branch" not in headers:
            raise ValueError("CSV must contain project_name, repo_url, and branch columns")

        # Normalize possibly oddly-cased/spaced headers from Excel.
        for row_number, raw_row in enumerate(reader, start=2):
            row = {str(k).strip().lower(): (v or "").strip() for k, v in raw_row.items() if k is not None}
            project_name = row.get("project_name", "")
            target = (row.get("repo_url") or row.get("repo_path") or "").strip().strip('"')
            branch = row.get("branch", "").strip().strip('"')
            if not project_name:
                errors.append(f"Row {row_number}: project_name is missing")
                continue
            if not target:
                errors.append(f"Row {row_number}: repo_url is missing")
                continue
            if not branch:
                errors.append(f"Row {row_number}: branch name is missing; please mention the branch name")
                continue
            target_key = target.rstrip("/\\").lower()
            if target_key in seen_targets:
                errors.append(f"Row {row_number}: duplicate repository '{target}'")
                continue
            seen_targets.add(target_key)
            project = projects.setdefault(project_name, {"name": project_name, "repositories": []})
            project["repositories"].append({
                "name": derive_repo_name(target),
                "target": target,
                "branch": branch,
            })

    if errors:
        raise ValueError("Invalid projects CSV:\n  " + "\n  ".join(errors))
    if not projects:
        raise ValueError("Projects CSV does not contain any repositories")
    return list(projects.values())


def create_projects_csv_wizard(output_path: str = "projects.csv") -> str:
    """Collect one or more projects interactively and save a reusable CSV."""
    rows: List[Tuple[str, str, str]] = []
    print("\nProject Input Wizard")
    print("Enter one or more projects. Each project may contain one or more repositories.\n")
    while True:
        project_name = input("Project name: ").strip()
        if not project_name:
            print("[!] Project name cannot be empty.")
            continue
        while True:
            target = input("Repository URL or local path: ").strip().strip('"')
            if not target:
                print("[!] Repository URL/path cannot be empty.")
                continue
            while True:
                branch = input("Branch name: ").strip()
                if branch:
                    break
                print("[!] Branch name is required. Please mention the branch name.")
            rows.append((project_name, target, branch))
            more_repos = input(f"Add another repository to '{project_name}'? [y/N]: ").strip().lower()
            if more_repos not in {"y", "yes"}:
                break
        more_projects = input("Add another project? [y/N]: ").strip().lower()
        if more_projects not in {"y", "yes"}:
            break

    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["project_name", "repo_url", "branch"])
        writer.writerows(rows)
    return output_path


def _root_readme(repo_path: str) -> Tuple[Optional[str], Optional[int]]:
    valid_names = {"readme", "readme.md", "readme.rst", "readme.txt", "readme.markdown"}
    try:
        for name in os.listdir(repo_path):
            path = os.path.join(repo_path, name)
            if name.lower() in valid_names and os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    return name, len(handle.read().strip())
    except OSError:
        pass
    return None, None


def _is_bot(contributor: Dict[str, Any]) -> bool:
    identity = f"{contributor.get('name', '')} {contributor.get('email', '')}".lower()
    return any(pattern in identity for pattern in BOT_PATTERNS)


def normalize_repository_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    gt = report.get("ground_truth", {})
    git = gt.get("git", {})
    api = report.get("github_api", {})
    prs = report.get("github_prs", {})
    tool_metrics = report.get("tool_metrics", {})
    compliance_available = "compliance" in tool_metrics
    compliance = tool_metrics.get("compliance", {}) or {}
    license_file = compliance.get("license_file")
    license_type = compliance.get("license_type")
    license_detected = bool(
        license_file or license_type or compliance.get("is_open_source") is True
    ) if compliance_available else None
    structure = report.get("heuristics", {}).get("structure", {})
    repo_path = report.get("target_path", "")
    readme_name, readme_characters = _root_readme(repo_path)

    contributors = git.get("all_contributors", []) or []
    human_contributors = [c for c in contributors if not _is_bot(c)]
    # Fallback preserves existing contributor information if detailed shortlog failed.
    human_count = len(human_contributors) if contributors else git.get("unique_contributors")

    language_breakdown = gt.get("languages", {}).get("breakdown", {}) or {}
    non_core = {
        "JSON", "XML", "YAML", "CSV", "TOML", "INI", "Markdown", "Text",
        "reStructuredText", "TeX", "SVG", "Properties", "Dockerfile",
    }
    loc_source = gt.get("loc", {}).get("source")
    eligible_loc = int(gt.get("loc", {}).get("breakdown", {}).get(
        "authored", gt.get("loc", {}).get("value", 0)
    ) or 0)
    if not eligible_loc and language_breakdown:
        eligible_loc = sum(
            int(stats.get("loc", 0) or 0)
            for language, stats in language_breakdown.items()
            if language not in non_core
        )

    visibility_available = bool(api.get("available")) and isinstance(api.get("is_private"), bool)
    pr_available = bool(prs.get("github_pr_analysis_available"))
    git_available = bool(git.get("available", report.get("is_git", False)))
    loc_available = loc_source == "scc"

    # Stage-1 fileList includes docs/config; extensions lets us conservatively
    # remove known non-source formats while retaining extensionless code files.
    extensions = structure.get("extensions", {}) or {}
    non_source_exts = {
        ".md", ".markdown", ".rst", ".txt", ".adoc", ".tex", ".json",
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml",
        ".csv", ".tsv", ".dat", ".lock", ".excluded", ".unknown",
    }
    eligible_files = sum(int(count or 0) for ext, count in extensions.items() if ext not in non_source_exts)

    return {
        "repository_name": report.get("repo"),
        "target_path": repo_path,
        "is_private": api.get("is_private") if visibility_available else None,
        "visibility_available": visibility_available,
        "visibility_reason": api.get("reason") if not visibility_available else None,
        "license_detection_available": compliance_available,
        "license_detected": license_detected,
        "license_file": license_file,
        "license_type": license_type,
        "readme_name": readme_name,
        "readme_characters": readme_characters,
        "git_available": git_available,
        "default_branch": git.get("default_branch"),
        "analysis_branch": git.get("analysis_branch"),
        "branch_selection_source": git.get("branch_selection_source"),
        "git_history_intact": git.get("history_integrity", {}).get("appears_intact") if git_available else None,
        "eligible_source_file_count": eligible_files,
        "human_contributor_count": human_count if git_available else None,
        "merged_pr_count": prs.get("merged_pr_count") if pr_available else None,
        "pr_analysis_available": pr_available,
        "pr_velocity_profile": prs.get("pr_velocity_profile") if pr_available else None,
        "blacklist_status": prs.get("blacklist_status") if pr_available else None,
        "commit_count": git.get("commit_count") if git_available else None,
        "eligible_source_loc": eligible_loc if loc_available else None,
        "development_span_months": git.get("active_span_months") if git_available else None,
    }


def _check(rule_id: str, actual: Any, expected: str, passed: Optional[bool], reason: str) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "status": "UNKNOWN" if passed is None else "PASS" if passed else "FAIL",
        "expected": expected,
        "actual": actual,
        "reason": reason,
    }


def evaluate_repository(metrics: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    rules = policy["repository_rules"]
    readme_chars = metrics.get("readme_characters")
    min_readme = int(rules["readme_minimum_characters"])
    checks = []
    if rules.get("private_required", True):
        checks.append(_check("private_repository", metrics.get("is_private"), "private", None if not metrics.get("visibility_available") else metrics.get("is_private") is True, "Repository visibility must be verified as private"))
    if rules.get("license_absent_required", True):
        license_actual = {
            "detected": metrics.get("license_detected"),
            "file": metrics.get("license_file"),
            "type": metrics.get("license_type"),
        }
        checks.append(_check(
            "no_detected_license",
            license_actual,
            "no license detected",
            None if not metrics.get("license_detection_available") else metrics.get("license_detected") is False,
            "Any detected license makes the repository ineligible",
        ))
    if rules.get("readme_required", True):
        checks.append(_check("valid_root_readme", readme_chars, f">= {min_readme} characters", False if readme_chars is None else readme_chars >= min_readme, "A meaningful README is required at repository root"))
    if rules.get("git_history_intact_required", True):
        checks.append(_check("git_history_intact", metrics.get("git_history_intact"), "true", None if not metrics.get("git_available") else metrics.get("git_history_intact") is True, "Git history must be intact"))
    checks.extend([
        _check("eligible_source_files", metrics.get("eligible_source_file_count"), f">= {rules['minimum_eligible_source_files']}", metrics.get("eligible_source_file_count", 0) >= int(rules["minimum_eligible_source_files"]), "Minimum eligible source-file count"),
        _check("human_contributors", metrics.get("human_contributor_count"), f">= {rules['minimum_human_contributors']}", None if metrics.get("human_contributor_count") is None else metrics["human_contributor_count"] >= int(rules["minimum_human_contributors"]), "Minimum unique human contributors"),
    ])
    if any(c["status"] == "FAIL" for c in checks):
        decision = "REJECT"
    elif any(c["status"] == "UNKNOWN" for c in checks):
        decision = "MANUAL_REVIEW"
    else:
        decision = "ACCEPT"
    return {"decision": decision, "checks": checks}


def _any_repo_check(repositories: List[Dict[str, Any]], rule_id: str, expected: str, predicate, values) -> Dict[str, Any]:
    for repo in repositories:
        value = values(repo["metrics"])
        if value is not None and predicate(repo["metrics"]):
            result = _check(rule_id, value, expected, True, f"Satisfied by {repo['name']}")
            result["satisfied_by"] = repo["name"]
            return result
    known = [values(repo["metrics"]) for repo in repositories if values(repo["metrics"]) is not None]
    unknown_exists = len(known) != len(repositories)
    return _check(rule_id, known, expected, None if unknown_exists else False, "No repository could be verified as satisfying this rule")


def evaluate_project(project: Dict[str, Any], repositories: List[Dict[str, Any]], policy: Dict[str, Any]) -> Dict[str, Any]:
    rules = policy["project_rules"]
    min_prs = int(rules["minimum_merged_prs"])
    min_commits = int(rules["minimum_commits_same_repository"])
    min_loc = int(rules["minimum_eligible_source_loc"])
    min_span = float(rules["minimum_development_span_months"])

    checks = [
        _any_repo_check(
            repositories, "merged_prs_and_commits_same_repository",
            f"merged PRs >= {min_prs} AND commits >= {min_commits}",
            lambda m: m.get("merged_pr_count", -1) >= min_prs and m.get("commit_count", -1) >= min_commits,
            lambda m: None if m.get("merged_pr_count") is None or m.get("commit_count") is None else {"merged_prs": m["merged_pr_count"], "commits": m["commit_count"]},
        ),
        _any_repo_check(
            repositories, "minimum_eligible_source_loc", f">= {min_loc}",
            lambda m: m.get("eligible_source_loc", -1) >= min_loc,
            lambda m: m.get("eligible_source_loc"),
        ),
        _any_repo_check(
            repositories, "minimum_development_span_months", f">= {min_span:g} months",
            lambda m: m.get("development_span_months", -1) >= min_span,
            lambda m: m.get("development_span_months"),
        ),
    ]

    if any(repo["decision"] == "REJECT" for repo in repositories) or any(c["status"] == "FAIL" for c in checks):
        decision = "REJECT"
    elif any(repo["decision"] in {"MANUAL_REVIEW", "ANALYSIS_ERROR"} for repo in repositories) or any(c["status"] == "UNKNOWN" for c in checks):
        decision = "MANUAL_REVIEW"
    else:
        decision = "ACCEPT"

    return {
        "project_name": project["name"],
        "decision": decision,
        "repositories_declared": len(project["repositories"]),
        "repositories_analyzed": len(repositories),
        "project_checks": checks,
        "repository_results": repositories,
    }


def _short_check_reason(check: Dict[str, Any]) -> str:
    """Turn a failed/unknown check into a compact CSV-friendly reason."""
    rule_labels = {
        "private_repository": "Repository is not verified private",
        "no_detected_license": "A repository license was detected",
        "valid_root_readme": "Valid root README missing",
        "git_history_intact": "Git history is not intact",
        "eligible_source_files": "Eligible source files requirement failed",
        "human_contributors": "Human contributors requirement failed",
        "merged_prs_and_commits_same_repository": "No repo has both required merged PRs and commits",
        "minimum_eligible_source_loc": "No repo meets minimum eligible source LOC",
        "minimum_development_span_months": "No repo meets minimum development span",
    }
    label = rule_labels.get(check.get("rule_id"), check.get("rule_id", "Requirement failed"))
    actual = check.get("actual")
    expected = check.get("expected")
    if check.get("status") == "UNKNOWN":
        return f"{label} (could not be verified)"
    return f"{label}: actual {actual}, expected {expected}"


def update_legacy_acceptance(output_dir: str, project_result: Dict[str, Any]) -> None:
    """Finalize the acceptance columns in a project's legacy.csv."""
    path = os.path.join(output_dir, "legacy.csv")
    if not os.path.isfile(path):
        return

    required_columns = ["Status", "Acceptance Decision", "Rejection Reason"]
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    
    # Insert Status immediately before Acceptance Decision
    # Insert new velocity columns before Acceptance Decision
    for column in reversed(["pr_velocity_profile", "blacklist_status"]):
        if column not in fieldnames:
            if "Acceptance Decision" in fieldnames:
                idx = fieldnames.index("Acceptance Decision")
                fieldnames.insert(idx, column)
            else:
                fieldnames.append(column)

    for column in required_columns:
        if column not in fieldnames:
            if column == "Status" and "Acceptance Decision" in fieldnames:
                idx = fieldnames.index("Acceptance Decision")
                fieldnames.insert(idx, "Status")
            else:
                fieldnames.append(column)

    repo_lookup: Dict[str, Dict[str, Any]] = {}
    for repo in project_result.get("repository_results", []):
        names = {str(repo.get("name", "")).strip().lower()}
        analyzed_name = str(repo.get("metrics", {}).get("repository_name", "")).strip().lower()
        if analyzed_name:
            names.add(analyzed_name)
        for name in names:
            if name:
                repo_lookup[name] = repo

    project_problem_checks = [
        check for check in project_result.get("project_checks", [])
        if check.get("status") in {"FAIL", "UNKNOWN"}
    ]
    project_reasons = [_short_check_reason(check) for check in project_problem_checks]

    for row in rows:
        repo = repo_lookup.get(str(row.get("Repository Name", "")).strip().lower())
        if not repo:
            continue
        repo_problem_checks = [
            check for check in repo.get("checks", [])
            if check.get("status") in {"FAIL", "UNKNOWN"}
        ]
        reasons = [_short_check_reason(check) for check in repo_problem_checks]
        reasons.extend(reason for reason in project_reasons if reason not in reasons)
        if repo.get("decision") == "ANALYSIS_ERROR":
            reasons.insert(0, repo.get("error", "Repository analysis failed"))

        metrics = repo.get("metrics", {})
        row["pr_velocity_profile"] = row.get("pr_velocity_profile") or metrics.get("pr_velocity_profile", "N/A")
        row["blacklist_status"] = row.get("blacklist_status") or metrics.get("blacklist_status", "N/A")
        row["Acceptance Decision"] = (
            f"Repository: {repo.get('decision', 'ANALYSIS_ERROR')} | "
            f"Project: {project_result.get('decision', 'MANUAL_REVIEW')}"
        )
        # Extract project status from the Acceptance Decision string
        project_part = row["Acceptance Decision"].split("Project:")[-1].strip().split()[0]
        row["Status"] = project_part
        row["Rejection Reason"] = "; ".join(reasons) if reasons else "All requirements passed"

    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_path, path)


def save_combined_legacy(
    project_outputs: List[Tuple[Dict[str, Any], str]], output_dir: str
) -> str:
    """Write one root-level CSV containing every declared project repository."""
    prefix_fields = ["Project Name", "Repository Target", "Requested Branch"]
    fieldnames = list(prefix_fields)
    combined_rows: List[Dict[str, Any]] = []

    for project_result, project_output in project_outputs:
        legacy_path = os.path.join(project_output, "legacy.csv")
        legacy_rows: List[Dict[str, Any]] = []
        if os.path.isfile(legacy_path):
            with open(legacy_path, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for header in reader.fieldnames or []:
                    if header not in fieldnames:
                        fieldnames.append(header)
                legacy_rows = list(reader)

        legacy_by_repo = {
            str(row.get("Repository Name", "")).strip().lower(): row
            for row in legacy_rows
            if str(row.get("Repository Name", "")).strip()
        }

        for repo in project_result.get("repository_results", []):
            names = [
                str(repo.get("name", "")).strip().lower(),
                str(repo.get("metrics", {}).get("repository_name", "")).strip().lower(),
            ]
            legacy_row = next((legacy_by_repo[name] for name in names if name in legacy_by_repo), None)
            if legacy_row is None:
                metrics = repo.get("metrics", {})
                legacy_row = {
                    "Repository Name": repo.get("name", ""),
                    "Status": project_result.get("decision", "MANUAL_REVIEW"),
                    "Acceptance Decision": (
                        f"Repository: {repo.get('decision', 'ANALYSIS_ERROR')} | "
                        f"Project: {project_result.get('decision', 'MANUAL_REVIEW')}"
                    ),
                    "Rejection Reason": repo.get("error", "Repository analysis did not complete"),
                    "pr_velocity_profile": metrics.get("pr_velocity_profile", "N/A"),
                    "blacklist_status": metrics.get("blacklist_status", "N/A"),
                }
                for header in legacy_row:
                    if header not in fieldnames:
                        fieldnames.append(header)

            combined_rows.append({
                "Project Name": project_result.get("project_name", ""),
                "Repository Target": repo.get("target", ""),
                "Requested Branch": repo.get("branch") or "",
                **legacy_row,
            })

    # Ensure correct column order: pr_velocity_profile -> blacklist_status -> Status -> Acceptance Decision
    def _ensure_column_before(fieldnames, col, before_col):
        if col not in fieldnames or before_col not in fieldnames:
            return fieldnames
        col_idx = fieldnames.index(col)
        before_idx = fieldnames.index(before_col)
        if col_idx != before_idx - 1:
            fieldnames.pop(col_idx)
            new_before_idx = fieldnames.index(before_col)
            fieldnames.insert(new_before_idx - 1, col)
        return fieldnames

    fieldnames = _ensure_column_before(fieldnames, "Status", "Acceptance Decision")
    fieldnames = _ensure_column_before(fieldnames, "blacklist_status", "Status")
    fieldnames = _ensure_column_before(fieldnames, "pr_velocity_profile", "blacklist_status")

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "combined_legacy.csv")
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(combined_rows)
    os.replace(temp_path, path)
    return path


def save_portfolio_results(project_results: List[Dict[str, Any]], output_dir: str) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    overall = "REJECT" if any(p["decision"] == "REJECT" for p in project_results) else "MANUAL_REVIEW" if any(p["decision"] != "ACCEPT" for p in project_results) else "ACCEPT"
    portfolio = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"), "decision": overall, "projects": project_results}
    json_path = os.path.join(output_dir, f"portfolio_decision_{timestamp}.json")
    csv_path = os.path.join(output_dir, f"portfolio_summary_{timestamp}.csv")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(portfolio, handle, indent=2, ensure_ascii=False)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = ["project_name", "project_decision", "repository_name", "repository_decision", "failed_rules", "unknown_rules"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for project in project_results:
            for repo in project["repository_results"]:
                writer.writerow({
                    "project_name": project["project_name"],
                    "project_decision": project["decision"],
                    "repository_name": repo["name"],
                    "repository_decision": repo["decision"],
                    "failed_rules": " | ".join(c["rule_id"] for c in repo.get("checks", []) if c["status"] == "FAIL"),
                    "unknown_rules": " | ".join(c["rule_id"] for c in repo.get("checks", []) if c["status"] == "UNKNOWN"),
                })
    return {"decision": overall, "json": json_path, "csv": csv_path}


def print_portfolio_summary(project_results: List[Dict[str, Any]], saved: Dict[str, str]) -> None:
    print("\n" + "=" * 62)
    print(f"PORTFOLIO DECISION: {saved['decision']}")
    print("=" * 62)
    for project in project_results:
        print(f"{project['project_name']}: {project['decision']} ({project['repositories_analyzed']} repositories)")
        for repo in project["repository_results"]:
            print(f"  - {repo['name']}: {repo['decision']}")
        for check in project["project_checks"]:
            print(f"  [{check['status']}] {check['rule_id']}: {check['reason']}")
    print(f"JSON report: {saved['json']}")
    print(f"CSV summary: {saved['csv']}")
    if saved.get("combined_legacy"):
        print(f"Combined legacy CSV: {saved['combined_legacy']}")
    print("=" * 62)
