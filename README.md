# AI Code Review Bot - GitHub Action

This repository contains a GitHub Action that uses AI (Google Gemini) to review Pull Requests based on comments.

## Features

*   Trigger review via PR comment (e.g., `/gemini-review`).
*   Hunk-based analysis for focused feedback.
*   Configurable review instructions and file exclusion patterns via a `.github/gemini-reviewer.yml` file **in the consuming repository**.
*   Posts review comments directly to the relevant lines in the PR.
*   Configurable Gemini model (defaults to `gemini-1.5-flash-latest`).
*   (Planned) Jira integration for context-aware reviews.

## Usage in Your Workflow

To use this action in your own repository within the same organization:

1.  **Ensure Action Permissions:** Your organization settings might need to allow actions created within the organization to run (`Settings` -> `Actions` -> `General`).
2.  **Set Secrets:** Add your Google Gemini API Key as an **Organization Secret** (recommended) or a Repository Secret named `GEMINI_API_KEY` (or pass a differently named secret to the `gemini-api-key` input).
3.  **(Optional) Configuration File:** Create a `.github/gemini-reviewer.yml` file in your repository to customize file exclusions and AI review instructions. See the example `gemini-reviewer.yml` in this repository's root for the format.
4.  **Create Workflow File:** Add a workflow file (e.g., `.github/workflows/ai-code-review.yml`) to your repository:

```yaml
# .github/workflows/ai-code-review.yml
name: AI Code Review

on:
  issue_comment:
    types: [created]

jobs:
  review:
    # Run only if comment is on a PR and starts with the trigger command
    if: startsWith(github.event.comment.body, '/gemini-review') && github.event.issue.pull_request
    runs-on: ubuntu-latest
    # Permissions needed by the action to read code and write comments
    permissions:
      contents: read
      pull-requests: write
      issues: read

    steps:
      - name: Run AI Review Bot Action
        # Replace your-org/ai-review-bot-action with your actual org/repo name
        # Replace @v1 with the specific tag/branch/commit SHA you want to use
        uses: your-org/ai-review-bot-action@v1 
        with:
          # Pass the Gemini API key secret to the action
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }} # Or secrets.YOUR_ORG_SECRET_NAME
          # Optional: Override the default config file path
          # config-path: '.github/my-custom-review-config.yml'
          # Optional: Override the default Gemini model
          # gemini-model-name: 'gemini-pro'
          # Optional: Pass a specific token if needed (defaults to github.token)
          # github-token: ${{ secrets.CUSTOM_GITHUB_TOKEN }}
```

5.  **Trigger:** Comment `/gemini-review` on a Pull Request in your repository.

## Action Inputs

*   `github-token`: (Optional) GitHub token. Defaults to `${{ github.token }}`.
*   `gemini-api-key`: (Required) Your Google Gemini API key.
*   `config-path`: (Optional) Path to the `.yml` config file in the consuming repo. Defaults to `.github/gemini-reviewer.yml`.
*   `gemini-model-name`: (Optional) Gemini model name. Defaults to `gemini-1.5-flash-latest`.

## Development

This repository contains the source code (`src/`), dependencies (`requirements.txt`), and the action definition (`action.yml`).

To contribute:
1. Clone the repository.
2. Make changes to the Python code in `src/`.
3. Test locally if possible (may require setting environment variables manually).
4. Update `action.yml` if inputs/outputs change.
5. Commit, push, and create a Pull Request.
6. Remember to create new version tags (e.g., `v1.1`) for releases. 