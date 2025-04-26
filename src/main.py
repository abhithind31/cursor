# src/main.py - Core logic entry point
import os
import sys
import json
import re # Import regex for parsing summary comment
from functools import lru_cache # For caching file content

# Import our modules
from github_api import GitHubAPI
from gemini_client import GeminiClient
from config import load_config
# We will need more advanced patch parsing later
# TODO: Update util imports after utils.py is refactored
from utils import parse_hunks_from_patch, should_exclude_file # Assume parse_hunks_from_patch exists/will be added
from utils import map_review_to_file_line # Assume this will be the new mapping function
from utils import find_best_patch_for_line # Assume this will be the new remapping function
from utils import extract_context_around_hunk # Needs verification/update

# Define the trigger command
TRIGGER_COMMAND = "/gemini-review"

# --- Constants for Summary Comment ---
SUMMARY_COMMENT_TAG = "<!-- Gemini Review Summary -->"
RAW_SUMMARY_START_TAG = "<details><summary>Raw Summary</summary>\\n\\n```text"
RAW_SUMMARY_END_TAG = "```\\n\\n</details>"
SHORT_SUMMARY_START_TAG = "<details><summary>Short Summary</summary>\\n\\n"
SHORT_SUMMARY_END_TAG = "\\n\\n</details>"
COMMIT_ID_TAG = "<!-- Last Reviewed Commit: "
COMMIT_ID_END_TAG = " -->"

# --- Prompt Templates ---
# Existing review prompt - might need minor tweaks later
REVIEW_PROMPT_TEMPLATE = """
You are an AI assistant reviewing a code change pull request.

**Pull Request Details:**
- **Title:** {pr_title}
- **Description:**
{pr_description}

**File Being Reviewed:** `{file_path}`

**Jira Ticket Context:**
{jira_context}

**Custom Review Instructions:**
{custom_instructions}

**Relevant Code Context:**
```python
{code_context}
```

**Specific Changes (Diff Hunk):**
```diff
{hunk_content}
```

**Your Task:**
Review the **Specific Changes (Diff Hunk)** within the context of the **Relevant Code Context** provided above.
Focus *exclusively* on identifying potential bugs, security vulnerabilities, performance issues, or violations of the **Custom Review Instructions** based *only* on the provided information.
If you find specific lines within the **Diff Hunk** that require changes, provide your feedback.
If the hunk looks good according to the instructions and context, respond with an empty list.

**Output Format:**
Respond *only* with a JSON object containing a list called "reviews". Each item in the list should be an object with "lineNumber" (integer, relative to the *start of the diff hunk*, starting at 1) and "reviewComment" (string).

Example valid JSON response:
{{"reviews": [{{"lineNumber": 5, "reviewComment": "This loop could lead to an infinite loop if the condition is never met."}}]}}

Example response for no issues:
{{"reviews": []}}

Do NOT suggest adding code comments or making purely stylistic changes unless specifically requested by the custom instructions.
Do NOT comment on code outside the provided **Diff Hunk**.
"""

# TODO: Add prompts for summarization (file diff summary, final summary, short summary)
SUMMARIZE_FILE_DIFF_PROMPT = "You are reviewing a code change. Below is the diff for the file `{filename}`. Describe the main purpose and logic changes introduced by this diff in a few concise bullet points. Focus on *what* changed and *why*, if apparent. Do not describe line-by-line changes.\\n\\n```diff\\n{file_diff}\\n```"
SUMMARIZE_CHANGESETS_PROMPT = "You are summarizing a pull request. Below are summaries for individual file changes. Combine these into a coherent high-level overview suitable for a changelog or progress report. Group related changes if possible. Focus on the overall impact and key features/fixes introduced.\\n\\nIndividual Summaries:\\n{raw_summary}"
SUMMARIZE_FINAL_PROMPT = "You are writing a summary for a pull request based on the raw summary of changes below. Write a paragraph or two describing the main goals achieved and key changes made in this update. Target audience is fellow developers and potentially product managers. Avoid excessive technical jargon where possible.\\n\\nRaw Summary:\\n{raw_summary}"
SUMMARIZE_SHORT_PROMPT = "Provide a very short (1-2 sentence) summary based on this raw summary:\\n{raw_summary}"

# --- Constants for Token Limits (using character count as proxy) ---
# TODO: Move these to config.py and potentially use a proper tokenizer
MAX_CHARS_FILE_SUMMARY_DIFF = 15000
MAX_CHARS_RAW_SUMMARY_INPUT = 25000
MAX_CHARS_REVIEW_PROMPT = 10000

def build_review_prompt(pr_details, file_path, code_context, hunk_content, custom_instructions, jira_context):
    """Builds the prompt string for reviewing a hunk."""
    return REVIEW_PROMPT_TEMPLATE.format(
        pr_title=pr_details['title'],
        pr_description=pr_details.get('description', '') or "N/A", # Handle empty description
        file_path=file_path,
        jira_context=jira_context,
        custom_instructions=custom_instructions or "N/A", # Handle empty instructions
        code_context=code_context,
        hunk_content=hunk_content
    )

# --- Helper functions for Summary Comment Parsing ---
def extract_from_tag(text, start_tag, end_tag):
    start_index = text.find(start_tag)
    if start_index == -1:
        return None
    start_index += len(start_tag)
    end_index = text.find(end_tag, start_index)
    if end_index == -1:
        return None # Malformed tag?
    return text[start_index:end_index].strip()

# LRU Cache decorator to avoid fetching the same file content multiple times
@lru_cache(maxsize=32) # Cache up to 32 files/refs
def get_cached_file_content(github_api, file_path, commit_id):
    print(f"  Fetching content for: {file_path} @ {commit_id[:7]}")
    return github_api.get_file_content(file_path, commit_id)

def main():
    print("Starting AI Review Bot (Incremental Mode)...")

    # 1. Get event payload path
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        print(f"Error: GITHUB_EVENT_PATH '{event_path}' is invalid or file does not exist.", file=sys.stderr)
        sys.exit(1)

    # 2. Parse event payload (still expecting issue_comment for trigger)
    try:
        with open(event_path, 'r') as f:
            event_payload = json.load(f)
        comment_body = event_payload["comment"]["body"]
        if "pull_request" not in event_payload["issue"]:
             print("Comment is not on a Pull Request. Skipping.")
             sys.exit(0)
        pr_number = event_payload["issue"]["number"]
    except (KeyError, json.JSONDecodeError, Exception) as e:
        print(f"Error parsing event payload or extracting required fields: {e}", file=sys.stderr)
        # print("Payload dump:", json.dumps(event_payload, indent=2)) # Uncomment for debugging
        sys.exit(1)

    print(f"Processing comment on PR #{pr_number}...")

    # 3. Check trigger command
    if not comment_body.strip().startswith(TRIGGER_COMMAND):
        print(f"Comment does not start with trigger command '{TRIGGER_COMMAND}'. Skipping.")
        sys.exit(0)

    print("Trigger command detected.")

    # 4. Instantiate GitHubAPI & Load Config
    try:
        github_api = GitHubAPI()
        config = load_config()
        print(f"\n--- Loaded Configuration ---")
        print(f"Exclude patterns: {config.get('exclude')}")
        print(f"Custom instructions: {config.get('custom_instructions', '')[:100]}...")
        print("--------------------------")
    except (ValueError, Exception) as e: # Catch API init errors or config load errors
        print(f"Error during initialization or config loading: {e}", file=sys.stderr)
        sys.exit(1)

    # 5. Fetch PR Metadata
    print(f"\n--- Fetching PR Metadata for #{pr_number} ---")
    pr_metadata = github_api.get_pr_metadata(pr_number)
    if not pr_metadata or not pr_metadata.get('base_sha') or not pr_metadata.get('head_sha'):
        print("Error: Could not fetch essential PR metadata (base/head SHAs).", file=sys.stderr)
        sys.exit(1)
    
    current_head_sha = pr_metadata['head_sha']
    pr_base_sha = pr_metadata['base_sha']
    print(f"Title: {pr_metadata['title']}")
    print(f"Base SHA: {pr_base_sha}")
    print(f"Head SHA: {current_head_sha}")
    print("------------------------------------")

    # 6. Find Existing Summary Comment and Last Reviewed Commit
    print("\n--- Checking for Existing Review Summary ---")
    last_reviewed_commit_sha = None
    existing_summarize_cmt = github_api.find_comment_with_tag(pr_number, SUMMARY_COMMENT_TAG)
    existing_summarize_cmt_body = ""
    existing_summarize_cmt_id = None

    if existing_summarize_cmt:
        existing_summarize_cmt_id = existing_summarize_cmt['id']
        existing_summarize_cmt_body = existing_summarize_cmt.get('body', '')
        print(f"Found existing summary comment ID: {existing_summarize_cmt_id}")
        # Extract last reviewed commit
        last_reviewed_commit_sha = extract_from_tag(existing_summarize_cmt_body, COMMIT_ID_TAG, COMMIT_ID_END_TAG)
        if last_reviewed_commit_sha:
            print(f"Last reviewed commit found: {last_reviewed_commit_sha}")
        else:
            print("No last reviewed commit SHA found in existing comment.")
    else:
        print("No existing summary comment found.")
    print("------------------------------------------")

    # 7. Determine Diff Range and Compare Commits
    base_for_diff = last_reviewed_commit_sha or pr_base_sha
    print(f"\n--- Comparing Commits: {base_for_diff[:7]}...{current_head_sha[:7]} ---")

    if base_for_diff == current_head_sha:
        print("Skipped: Head commit is the same as the base for diff. No new changes to review.")
        # Optionally update the comment timestamp or add a "checked" message
        sys.exit(0)

    comparison_data = github_api.compare_commits(base_for_diff, current_head_sha)

    if not comparison_data:
        print("Error: Failed to compare commits.", file=sys.stderr)
        sys.exit(1)

    if comparison_data.get('status') == 'identical':
        print("Skipped: No difference between commits.")
        # Update summary comment to reflect the new head SHA was checked
        final_summary_comment_body = f"{SUMMARY_COMMENT_TAG}\nNo changes detected since last review.\n{COMMIT_ID_TAG}{current_head_sha}{COMMIT_ID_END_TAG}"
        if existing_summarize_cmt_id:
            github_api.update_comment(existing_summarize_cmt_id, final_summary_comment_body)
        else:
            github_api.post_pr_comment(pr_number, final_summary_comment_body)
        sys.exit(0)

    if comparison_data.get('status') == 'behind':
        print("Warning: Head commit is behind the base for diff. This might indicate a force push or unusual history. Reviewing changes anyway.")
        # Potentially add logic to reset last_reviewed_commit_sha?

    comparison_files = comparison_data.get('files', [])
    comparison_commits = comparison_data.get('commits', []) # List of commit objects in the range

    if not comparison_files:
        print("Skipped: No file changes found in the comparison.")
        # Update summary comment to reflect the new head SHA was checked
        final_summary_comment_body = f"{SUMMARY_COMMENT_TAG}\nNo file changes detected since last review.\n{COMMIT_ID_TAG}{current_head_sha}{COMMIT_ID_END_TAG}"
        if existing_summarize_cmt_id:
            github_api.update_comment(existing_summarize_cmt_id, final_summary_comment_body)
        else:
            github_api.post_pr_comment(pr_number, final_summary_comment_body)
        sys.exit(0)

    print(f"Found {len(comparison_files)} files changed in comparison.")
    print(f"Found {len(comparison_commits)} commits in comparison.")
    print("---------------------------------------------------")

    # 8. Filter Files
    print("\n--- Filtering Files ---")
    filtered_files_to_process = []
    file_patches_map = {} # Store patch info {filename: [patch_info1, patch_info2,...]}
    excluded_files_count = 0
    for file_data in comparison_files:
        # The comparison endpoint includes 'filename', 'status' (added, modified, removed), 'patch', etc.
        file_path = file_data.get('filename')
        if not file_path:
             print("Warning: Skipping file data with missing filename.")
             continue
             
        if file_data.get('status') == 'removed':
             print(f"Skipping removed file: {file_path}")
             continue # Can't review removed files

        if should_exclude_file(file_path, config['exclude']):
            print(f"Excluding file: {file_path}")
            excluded_files_count += 1
        elif not file_data.get('patch'):
            print(f"Skipping file with no patch data: {file_path}") # Should not happen for added/modified
        else:
            # TODO: Parse hunks here using updated utils to get patch line info
            # Example structure for patch_info: {'header': str, 'content': str, 'new_start_line': int, 'new_end_line': int}
            parsed_hunks = parse_hunks_from_patch(file_data['patch']) 
            if not parsed_hunks:
                 print(f"  Warning: Could not parse hunks for {file_path}. Skipping review for this file.")
                 continue # Skip if parsing fails

            file_patches_map[file_path] = parsed_hunks
            file_data['parsed_hunks'] = parsed_hunks # Attach parsed hunks for later use
            filtered_files_to_process.append(file_data)


    print(f"Total files in comparison: {len(comparison_files)}")
    print(f"Files excluded: {excluded_files_count}")
    print(f"Files to review/summarize: {len(filtered_files_to_process)}")
    print("-----------------------")

    if not filtered_files_to_process:
        print("No files left to review after filtering. Exiting.")
        # Update summary comment? (Maybe add status that files were excluded)
        # Consider creating/updating the comment here similar to the no-change cases above.
        sys.exit(0)

    # 9. Initialize Gemini Client
    try:
        gemini = GeminiClient()
    except ValueError as e:
        print(f"Error initializing Gemini Client: {e}", file=sys.stderr)
        sys.exit(1)

    # 10. Summarization Phase
    print("\n--- Summarization Phase ---")
    # Initialize summaries from existing comment if available
    raw_summary = extract_from_tag(existing_summarize_cmt_body, RAW_SUMMARY_START_TAG, RAW_SUMMARY_END_TAG) or ""
    short_summary = extract_from_tag(existing_summarize_cmt_body, SHORT_SUMMARY_START_TAG, SHORT_SUMMARY_END_TAG) or ""
    # Final summary is always regenerated
    final_summary = "" 
    summaries_failed = []
    individual_summaries = [] # Store successful individual file summaries

    # a. Summarize individual file diffs
    print("  Generating individual file summaries...")
    for file_data in filtered_files_to_process:
        filename = file_data['filename']
        file_diff = file_data['patch']
        if not file_diff:
             summaries_failed.append(f"{filename} (No diff content)")
             continue

        # TODO: Check token count for file_diff before sending?
        if len(file_diff) > MAX_CHARS_FILE_SUMMARY_DIFF:
            print(f"  Skipping summary for {filename}: Diff too long ({len(file_diff)} > {MAX_CHARS_FILE_SUMMARY_DIFF} chars).")
            summaries_failed.append(f"{filename} (Diff too long)")
            continue

        prompt = SUMMARIZE_FILE_DIFF_PROMPT.format(filename=filename, file_diff=file_diff)
        try:
            # Assuming get_review returns the raw text response for non-JSON prompts
            # Need to adjust gemini_client if it strictly expects/parses JSON
            file_summary_text = gemini.generate_text(prompt)
            if file_summary_text:
                 # Prepend filename for clarity when combining later
                 individual_summaries.append(f"**{filename}:**\\n{file_summary_text}")
            else:
                 summaries_failed.append(f"{filename} (Empty summary response)")
        except Exception as e:
             print(f"  Error summarizing {filename}: {e}", file=sys.stderr)
             summaries_failed.append(f"{filename} (API Error: {e})")

    print(f"  Generated {len(individual_summaries)} individual summaries.")

    # b. Combine individual summaries into raw_summary (if any were generated)
    if individual_summaries:
        combined_individual = "\n\n---\n\n".join(individual_summaries)
        # Prepend to any existing raw summary from previous runs
        raw_summary = f"{combined_individual}\n\n---\nPrevious Summary:\n{raw_summary}" if raw_summary else combined_individual

        # c. Refine raw_summary (using SUMMARIZE_CHANGESETS_PROMPT)
        print("  Refining raw summary...")
        # TODO: Check token count for raw_summary?
        summary_input = raw_summary
        if len(raw_summary) > MAX_CHARS_RAW_SUMMARY_INPUT:
            print(f"  Warning: Raw summary input too long ({len(raw_summary)} chars). Truncating for refinement prompt.")
            summary_input = raw_summary[:MAX_CHARS_RAW_SUMMARY_INPUT] + "\n... (Content truncated)"

        prompt_refine = SUMMARIZE_CHANGESETS_PROMPT.format(raw_summary=raw_summary)
        try:
            refined_summary = gemini.generate_text(prompt_refine)
            if refined_summary:
                raw_summary = refined_summary # Update raw_summary with the refined version
            else:
                print("  Warning: Got empty response when refining raw summary.")
                # Keep the combined individual summaries as raw_summary
        except Exception as e:
            print(f"  Error refining raw summary: {e}", file=sys.stderr)
            summaries_failed.append("Overall Raw Summary (Refinement API Error)")
            # Keep the combined individual summaries as raw_summary in case of error
    else:
        print("  Skipping raw summary refinement as no individual summaries were generated.")

    # d. Generate Final Summary (using SUMMARIZE_FINAL_PROMPT)
    if raw_summary:
        print("  Generating final summary...")
        summary_input = raw_summary
        if len(raw_summary) > MAX_CHARS_RAW_SUMMARY_INPUT:
            print(f"  Warning: Raw summary input too long ({len(raw_summary)} chars). Truncating for final summary prompt.")
            summary_input = raw_summary[:MAX_CHARS_RAW_SUMMARY_INPUT] + "\n... (Content truncated)"

        prompt_final = SUMMARIZE_FINAL_PROMPT.format(raw_summary=raw_summary)
        try:
            final_summary_text = gemini.generate_text(prompt_final)
            if final_summary_text:
                final_summary = final_summary_text
            else:
                summaries_failed.append("Overall Final Summary (Empty Response)")
                final_summary = "*Could not generate final summary.*" # Set fallback
        except Exception as e:
            print(f"  Error generating final summary: {e}", file=sys.stderr)
            summaries_failed.append(f"Overall Final Summary (API Error: {e})")
            final_summary = f"*Error generating final summary: {e}*" # Set fallback
    else:
         final_summary = "*No changes detected or summarizable in this update.*"

    # e. Generate Short Summary (using SUMMARIZE_SHORT_PROMPT)
    if raw_summary:
        print("  Generating short summary...")
        summary_input = raw_summary
        if len(raw_summary) > MAX_CHARS_RAW_SUMMARY_INPUT:
            # No need to truncate for short summary, it should handle long input
            pass 

        prompt_short = SUMMARIZE_SHORT_PROMPT.format(raw_summary=raw_summary)
        try:
            short_summary_text = gemini.generate_text(prompt_short)
            if short_summary_text:
                short_summary = short_summary_text
            else:
                summaries_failed.append("Overall Short Summary (Empty Response)")
                # Keep existing short_summary or clear it?
                short_summary = extract_from_tag(existing_summarize_cmt_body, SHORT_SUMMARY_START_TAG, SHORT_SUMMARY_END_TAG) or ""
        except Exception as e:
            print(f"  Error generating short summary: {e}", file=sys.stderr)
            summaries_failed.append(f"Overall Short Summary (API Error: {e})")
            short_summary = extract_from_tag(existing_summarize_cmt_body, SHORT_SUMMARY_START_TAG, SHORT_SUMMARY_END_TAG) or "" # Keep existing on error


    print("Summarization phase complete.")
    print("-------------------------")


    # 11. Review Phase
    print("\n--- Detailed Review Phase ---")
    all_review_comments = [] # List of dicts for create_review: {"path": ..., "line": ..., "body": ...}
    reviews_failed = []
    total_hunks_processed = 0

    # Process file by file from the filtered comparison list
    for file_data in filtered_files_to_process:
        file_path = file_data['filename']
        patch_content = file_data['patch']
        status = file_data.get('status', 'modified') # e.g., 'added', 'modified'
        hunks = file_data['parsed_hunks'] # Use pre-parsed hunks
        print(f"\nReviewing file: {file_path} (Status: {status})")

        # Get full file content *at the PR base* for context extraction
        # Use head_sha if file is newly added in this PR.
        content_ref = pr_base_sha if status != 'added' else current_head_sha
        full_file_content = get_cached_file_content(github_api, file_path, content_ref)

        if full_file_content is None:
             print(f"  Error fetching base content ({content_ref[:7]}) for {file_path}, skipping reviews for this file.", file=sys.stderr)
             reviews_failed.append(f"{file_path} (Content fetch failed)")
             continue # Skip to next file
        elif full_file_content == "" and status != 'added':
             print(f"  Warning: Base content for {file_path} at {content_ref[:7]} is empty (might be deleted/renamed?). Skipping reviews for this file.")
             continue

        # Process hunks within the file
        for hunk_index, hunk_info in enumerate(hunks):
            total_hunks_processed += 1
            print(f"  Processing hunk {hunk_index + 1}/{len(hunks)} for {file_path}")

            # a. Extract Context
            # TODO: Review/Update extract_context_around_hunk for direct patch/header usage
            code_context_snippet = extract_context_around_hunk(full_file_content, hunk_info['header'])

            # b. Fetch Jira context (Placeholder)
            jira_context = "N/A" # TODO: Implement Jira fetching

            # c. Build prompt
            prompt = build_review_prompt(
                pr_details=pr_metadata, # Use fetched metadata
                file_path=file_path,
                code_context=code_context_snippet,
                hunk_content=hunk_info['content'],
                custom_instructions=config['custom_instructions'],
                jira_context=jira_context
            )

            # Check prompt length before sending
            if len(prompt) > MAX_CHARS_REVIEW_PROMPT:
                print(f"  Skipping review for hunk {hunk_index + 1} in {file_path}: Prompt too long ({len(prompt)} > {MAX_CHARS_REVIEW_PROMPT} chars).")
                # Potentially try reducing context first?
                reviews_failed.append(f"{file_path} Hunk {hunk_index + 1} (Prompt too long)")
                continue # Skip this hunk

            # d. Call Gemini API
            try:
                review_result = gemini.get_review(prompt)
            except Exception as e:
                print(f"  Error calling Gemini API for hunk {hunk_index + 1} in {file_path}: {e}", file=sys.stderr)
                reviews_failed.append(f"{file_path} Hunk {hunk_index + 1} (API Error: {e})")
                continue # Skip this hunk

            # e. Collect responses
            if review_result and 'reviews' in review_result:
                for review in review_result['reviews']:
                    hunk_line_num_relative = review.get('lineNumber') # Line num relative to hunk start
                    review_comment_body = review.get('reviewComment')

                    if hunk_line_num_relative is None or not review_comment_body:
                        print(f"  Warning: Skipping review item with missing 'lineNumber' or empty 'reviewComment' in {file_path}")
                        continue

                    # Map hunk-relative line to absolute file line
                    # TODO: Ensure map_review_to_file_line uses hunk_info correctly
                    target_file_line = map_review_to_file_line(hunk_line_num_relative, hunk_info)

                    if target_file_line is None:
                         print(f"  Warning: Could not map hunk line {hunk_line_num_relative} to file line for {file_path} (hunk {hunk_index + 1}). Comment may be lost.")
                         reviews_failed.append(f"{file_path} Hunk {hunk_index + 1} (Line mapping failed)")
                         continue

                    # Find the best patch hunk boundary to attach the comment to (like example code)
                    # TODO: Ensure find_best_patch_for_line works with file_patches_map[file_path]
                    final_line, remapped = find_best_patch_for_line(target_file_line, file_patches_map[file_path])

                    comment_to_post = review_comment_body
                    if remapped:
                        print(f"    Remapped comment for original target line {target_file_line} to patch line {final_line}")
                        comment_to_post = f"> Note: This review targeted line {target_file_line}, which is outside the changed code blocks. It has been attached to the nearest change block (line {final_line}).\n\n{review_comment_body}"

                    print(f"    Adding review comment for {file_path}:{final_line}")
                    all_review_comments.append({
                        "path": file_path,
                        "line": final_line,
                        "body": comment_to_post
                    })

            elif not review_result:
                 reviews_failed.append(f"{file_path} Hunk {hunk_index + 1} (Invalid/Empty API Response)")


    print(f"--- Review Phase Complete ---")
    print(f"Total Hunks Processed: {total_hunks_processed}")
    print(f"Total Review Comments Generated: {len(all_review_comments)}")
    if reviews_failed:
        print(f"Review Errors Encountered ({len(reviews_failed)}):")
        for fail in reviews_failed:
            print(f"  - {fail}")
    print("--------------------------")


    # 12. Construct Final Summary Comment & Post Outputs
    print("\n--- Finalizing Output ---")

    # Build status message section
    status_lines = []
    status_lines.append(f"Compared `{base_for_diff[:7]}`...`{current_head_sha[:7]}`.")
    status_lines.append(f"Processed {len(filtered_files_to_process)} out of {len(comparison_files)} changed files ({excluded_files_count} excluded).")
    status_lines.append(f"Generated {len(all_review_comments)} review comments.")
    # TODO: Add counts for skipped/failed summaries/reviews based on implemented logic

    status_details = "\n".join(status_lines)
    if summaries_failed:
         failed_summaries_str = "* " + "\n* ".join(summaries_failed)
         status_details += f"\\n<details><summary>Summarization Errors ({len(summaries_failed)})</summary>\\n\\n{failed_summaries_str}\\n\\n</details>"
    if reviews_failed:
         failed_reviews_str = "* " + "\n* ".join(reviews_failed)
         status_details += f"\\n<details><summary>Review Errors ({len(reviews_failed)})</summary>\\n\\n{failed_reviews_str}\\n\\n</details>"

    # Construct the full comment body
    # Use the generated/updated summaries from step 10
    # Note: raw_summary might have been refined in step 10c

    summary_comment_body = f"""{SUMMARY_COMMENT_TAG}
 **Gemini Code Review Summary**
 
 {final_summary}
 
 {RAW_SUMMARY_START_TAG}
 {raw_summary} 
 {RAW_SUMMARY_END_TAG}
 
 {SHORT_SUMMARY_START_TAG}
 {short_summary}
 {SHORT_SUMMARY_END_TAG}
 
 ---
<details><summary>Review Status</summary>

{status_details}

</details>

{COMMIT_ID_TAG}{current_head_sha}{COMMIT_ID_END_TAG}
"""
    # Post the line comments as a review
    # Only add the summary comment body to the review if we are *not* updating an existing comment
    review_body = "" if existing_summarize_cmt_id else summary_comment_body
    if all_review_comments:
        print(f"Posting {len(all_review_comments)} review comments...")
        # Create review uses the *latest* head commit SHA
        github_api.create_review(pr_number, current_head_sha, all_review_comments, body=review_body)
    elif review_body:
        print("No line comments generated, but posting summary as review body...")
        # Post empty review just to get the summary body onto the PR if no comments were made
        # and we are creating the summary comment for the first time.
        github_api.create_review(pr_number, current_head_sha, [], body=review_body)
    else:
        print("No review comments generated.")

    # Create or Update the Summary Comment
    if existing_summarize_cmt_id:
        print(f"Updating summary comment ID {existing_summarize_cmt_id}...")
        github_api.update_comment(existing_summarize_cmt_id, summary_comment_body)
    elif not all_review_comments and not review_body:
         # If we didn't post a review (no comments and no initial summary body),
         # create the summary comment separately. Handles subsequent runs with no changes/comments.
         print("Creating new summary comment...")
         github_api.post_pr_comment(pr_number, summary_comment_body)
    else:
         print("Summary included in the review body or updated existing comment; not posting separate summary comment.")


    print("\n--- AI Review Bot Finished ---")

if __name__ == "__main__":
    main()