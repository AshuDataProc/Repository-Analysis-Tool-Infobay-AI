# CSV Column Schema & Abbreviations

This document explains the meaning behind the columns generated in the `summary_all.csv` and `summary_metadata.csv` outputs by the Repository Analysis Tool.

## General Information
*   **repo_name**: The name of the analyzed repository or folder.
*   **is_git**: Boolean (`True`/`False`) indicating whether a `.git` folder and valid git history were found.
*   **license_type**: Detected open-source or commercial license (e.g., MIT, Apache License, Unknown).
    Project acceptance rejects the repository when any license is detected.

## Git Metadata & Timeline
*   **first_commit_date**: Timestamp of the very first commit in the repository.
*   **last_commit_date**: Timestamp of the most recent commit.
*   **development_span_months**: The active lifespan of the repository in months (from first commit to last).
*   **commits**: Total number of commits reachable from the selected analysis branch.
*   **meaningful_commit_count**: Commits excluding generic/boilerplate messages like "Update README" or "Merge branch".
*   **commits_per_month**: Average commit velocity per active month.
*   **branch_count**: Total number of git branches discovered in the local repository.
*   **git_history_intact**: Boolean indicating if the git history appears continuous without suspicious single-bulk commits or extreme force-pushes.
*   **default_branch**: Default branch advertised by the cloned repository's `origin` remote.
*   **analysis_branch**: Branch whose checked-out files were used for LOC, token, framework, and repository analysis.
*   **branch_selection_source**: `repository_default`, `requested`, or `local_current_branch`.

## Contributors
*   **contributors**: Count of unique contributor email addresses extracted from Git history. Note that developers using multiple emails might be counted multiple times.
*   **all_contributors_count**: Total number of contributor identities (combining names and emails) found.
*   **all_contributors**: A comma-separated list of contributor names (truncated in the terminal, full list in CSV).

## Code Size & Tokens
*   **authored_loc**: The filtered SCC `--no-gen` Code total. It excludes SCC-detected generated files and the configured non-authored directories.
*   **non_authored_loc**: Total SCC repository code LOC minus `authored_loc`; under the business definition this includes generated, vendor, dependency, build, migration, and other configured excluded code.
*   **loc_comment**: Total repository comment LOC reported by SCC.
*   **loc_blank**: Total repository blank LOC reported by SCC.
*   **loc_files**: Total number of SCC-recognized repository files.
*   **tokens_llm_grand_total**: Grand total calculated using `cl100k_base`: repository code/docs/config plus PR/MR content.
*   **tokens_llm_repository**: Repository-only token count (code, documentation, and configuration).
*   **tokens_llm_code / tokens_llm_docs / tokens_llm_config**: Repository token breakdown by content type.
*   **tokens_llm_pr**: PR/MR title, body, review-discussion, and commit-message tokens available to the analyzer.
*   **lexical_token_grand_total**: Grand lexical-token total: repository code/docs/config plus PR/MR content.
*   **lexical_token_repository**: Repository-only lexical-token subtotal.
*   **lexical_token_code / lexical_token_docs / lexical_token_config**: Repository lexical-token breakdown.
*   **lexical_token_pr**: Lexical tokens from PR/MR content.

## Languages & Frameworks
*   **lang_count**: Total distinct programming languages detected.
*   **languages**: Pipe-separated list of all core programming languages used.
*   **languages_frontend**: Languages specifically categorized for User Interface / Frontend (e.g., HTML, CSS, JavaScript).
*   **languages_backend**: Languages categorized for Server / Backend logic (e.g., Python, Go, Java).
*   **frameworks**: Detailed map of the frameworks found, categorized by where they were detected (e.g., `package.json`, `requirements.txt`, or direct `code_imports`).
*   **framework_frontend**: High-level frontend frameworks detected (e.g., React, Vue, Next.js).
*   **framework_backend**: High-level backend frameworks detected (e.g., Django, Express, FastAPI).
*   **languages_percentage_bytes(>5%)**: The proportion of repository size occupied by each major language (ignoring trace languages < 5%).
*   **frameworks_percentage_bytes(>5%)**: The proportion of repository size occupied by code associated with a specific framework.

## Infrastructure & Integrations
*   **databases_used**: Detected database technologies (e.g., PostgreSQL, Redis, MongoDB).
*   **third_party_apis**: Integrations with external SaaS providers (e.g., Firebase, Stripe, Twilio).
*   **setup_guidelines**: Indicates if instructions for environment setup or deployment exist in the documentation (`Present` or `Not found`).

## Security, Quality, & Heuristics
*   **security_findings**: Summarizes secret exposures. `Clean` if no secrets found. `Review Required (N)` if N potential secrets (e.g., AWS keys, database URIs) were detected.
*   **documentation_quality**: Estimated quality rating (`High`, `Medium`, `Low`) based on README length, structure, and presence of setup guides.
*   **duplication_weighted_percent**: Percentage of the codebase consisting of duplicated blocks/files, weighted by token size.
*   **code_complexity**: A heuristic rating (`Low`, `Moderate`, `High`) based on file lengths, line variance, and nested logic depth.
*   **ai_detection_percent**: An experimental metric predicting the probability that the codebase contains AI-generated segments based on entropy and uniformity.
*   **repo_rating_score**: A composite score (0-10) factoring in Git health, LOC size, testing, and framework presence.
*   **repo_rating_label**: Categorical mapping of the rating score (e.g., `Poor`, `Fair`, `Good`, `Excellent`).

## Process Metrics
*   **total_time_seconds**: Execution time taken by the pipeline to analyze this repository.

## Legacy Acceptance Columns (`legacy.csv`)
*   **repository_rating_label**: Human-readable label associated with the numeric repository rating.
*   **Acceptance Decision**: Combined repository/project result, for example `Repository: ACCEPT | Project: REJECT`. It is `N/A` outside project mode.
*   **Rejection Reason**: Concise failed or unverifiable repository/project requirements; contains `All requirements passed` when accepted.

## Combined Legacy Output (`combined_legacy.csv`)
*   Contains all repositories declared in the current `projects.csv`, across every project.
*   **Project Name**: Project grouping from `projects.csv`.
*   **Repository Target**: Original repository URL/path.
*   **Requested Branch**: Exact requested branch, blank when the remote default was used.
*   Remaining columns mirror project-level `legacy.csv`; analysis failures receive a synthetic row so declared repositories are not omitted.

## GitHub PR Analytics
*   **total_pr_count**: Total number of Pull Requests found on GitHub for this repository.
*   **open_pr_count**: Number of currently open Pull Requests.
*   **closed_pr_count**: Number of closed Pull Requests.
*   **merged_pr_count**: Number of merged Pull Requests (a subset of closed Pull Requests).
*   **github_pr_analysis_available**: Boolean indicating if GitHub Pull Request analytics were successfully run for the repository.

## Extra Metadata Columns (`summary_metadata.csv` specific)
*   **commercial_usage_summary**: Simplifies license detection into `Permissive` (MIT, Apache, etc.) or `Restrictive`.
*   **domain_industry**: (Placeholder) Future-ready field for classifying the industry context of the application.
*   **language_framework_details**: Detailed pipe-separated raw output of how languages and frameworks were identified.
