# src/github_api.py - Functions for interacting with the GitHub API

import os
import requests
import json
import time # Import time for potential rate limiting
import sys # Needed for stderr prints in main


class GitHubAPI:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN environment variable not set.")
        self.repo = os.getenv("GITHUB_REPOSITORY") # e.g., "owner/repo"
        if not self.repo:
            raise ValueError("GITHUB_REPOSITORY environment variable not set.")
        self.api_base_url = os.getenv("GITHUB_API_URL", "https://api.github.com") # Default to public GitHub API

        self.headers = {
            "Authorization": f"token {self.token}",
            # Default to JSON, specific methods can override
            "Accept": "application/vnd.github.v3+json",
        }

    def _make_request(self, method, url, headers=None, params=None, data=None, expected_status=None):
        """Helper function to make requests and handle common errors."""
        if headers is None:
            headers = self.headers
        try:
            response = requests.request(method, url, headers=headers, params=params, data=data)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

            # Check if the expected status code matches, if provided
            if expected_status and response.status_code != expected_status:
                 print(f"Warning: Expected status {expected_status} but got {response.status_code} for {method} {url}")
                 # Decide if this should be an error or just a warning

            # Basic rate limit check
            if 'X-RateLimit-Remaining' in response.headers:
                remaining = int(response.headers['X-RateLimit-Remaining'])
                if remaining < 10:
                    print(f"Warning: Low GitHub API rate limit remaining: {remaining}")
            
            # Return JSON if response has content, else True for success on non-GET/HEAD
            if response.status_code == 204: # No Content
                return True
            if method.upper() in ["GET", "HEAD"] or response.content:
                return response.json()
            else:
                return True # Success for POST/PATCH/DELETE with no body

        except requests.exceptions.HTTPError as e:
            # Log specific details for common errors
            if e.response.status_code == 401:
                 print(f"GitHub API Error: 401 Unauthorized. Check your GITHUB_TOKEN permissions for {method} {url}.", file=sys.stderr)
            elif e.response.status_code == 403:
                 print(f"GitHub API Error: 403 Forbidden. Check permissions or rate limits for {method} {url}.", file=sys.stderr)
                 if 'X-RateLimit-Remaining' in e.response.headers and int(e.response.headers['X-RateLimit-Remaining']) == 0:
                      reset_time = int(e.response.headers.get('X-RateLimit-Reset', 'unknown'))
                      print(f"Rate limit likely exceeded. Resets at epoch {reset_time}.", file=sys.stderr)
            elif e.response.status_code == 404:
                 print(f"GitHub API Error: 404 Not Found for {method} {url}", file=sys.stderr)
            else:
                 print(f"GitHub API HTTP Error ({e.response.status_code}) for {method} {url}: {e}", file=sys.stderr)
            print(f"Response body: {e.response.text}", file=sys.stderr)
            return None # Indicate failure
        except requests.exceptions.RequestException as e:
            print(f"GitHub API Request Error for {method} {url}: {e}", file=sys.stderr)
            return None # Indicate failure

    def get_pr_metadata(self, pr_number):
        """Fetches essential PR details: title, description, base SHA, head SHA."""
        pr_url = f"{self.api_base_url}/repos/{self.repo}/pulls/{pr_number}"
        pr_data = self._make_request("GET", pr_url)

        if not pr_data:
            print(f"Error: Could not fetch metadata for PR #{pr_number}.")
            return None

        return {
            "title": pr_data.get("title", ""),
            "description": pr_data.get("body", ""),
            "base_sha": pr_data.get("base", {}).get("sha"),
            "head_sha": pr_data.get("head", {}).get("sha"),
        }

    def compare_commits(self, base_sha, head_sha):
        """Gets the comparison between two commits, including diff data."""
        compare_url = f"{self.api_base_url}/repos/{self.repo}/compare/{base_sha}...{head_sha}"
        
        # Request JSON response which includes files array with patch data
        comparison_data = self._make_request("GET", compare_url)

        if not comparison_data:
            print(f"Error comparing commits {base_sha}...{head_sha}")
            return None
        
        # TODO: Consider handling cases where comparison status is not 'ahead' or 'diverged'
        # e.g., 'behind', 'identical'

        return comparison_data # Contains 'files', 'commits', etc.

    def find_comment_with_tag(self, pr_number, tag):
        """Finds the first issue comment containing a specific tag."""
        comments_url = f"{self.api_base_url}/repos/{self.repo}/issues/{pr_number}/comments"
        page = 1
        per_page = 100 # Max allowed by GitHub API

        while True:
            params = {"page": page, "per_page": per_page}
            comments_page = self._make_request("GET", comments_url, params=params)

            if not comments_page: # Error occurred or no comments found at all
                print(f"Error fetching comments or no comments found for PR #{pr_number}.")
                return None

            for comment in comments_page:
                if comment.get("body") and tag in comment["body"]:
                    print(f"Found comment (ID: {comment['id']}) with tag '{tag}'")
                    return comment # Return the full comment object

            # Check if this was the last page
            if len(comments_page) < per_page:
                break # No more pages

            page += 1
            # Optional: Add a small delay to avoid hitting secondary rate limits
            # time.sleep(0.1)

        print(f"No comment found with tag '{tag}' for PR #{pr_number}.")
        return None

    def update_comment(self, comment_id, body):
        """Updates an existing issue comment."""
        comment_url = f"{self.api_base_url}/repos/{self.repo}/issues/comments/{comment_id}"
        payload = json.dumps({"body": body})
        result = self._make_request("PATCH", comment_url, data=payload)
        if result:
            print(f"Successfully updated comment ID {comment_id}")
            return result
        else:
            print(f"Error updating comment ID {comment_id}")
            return None

    def create_review(self, pr_number, commit_id, comments, body="", event="COMMENT"):
        """Creates a pull request review with multiple comments."""
        # comments should be a list of dicts: [{"path": "file.py", "line": 10, "body": "comment text"}, ...]
        reviews_url = f"{self.api_base_url}/repos/{self.repo}/pulls/{pr_number}/reviews"
        payload = {
            "commit_id": commit_id,
            "body": body, # Overall review summary
            "event": event, # e.g., COMMENT, APPROVE, REQUEST_CHANGES
            "comments": comments,
        }
        
        # Filter out any potentially empty comments just in case
        valid_comments = [c for c in comments if c.get("body")]
        if len(valid_comments) != len(comments):
             print(f"Warning: Filtered out {len(comments) - len(valid_comments)} empty comments before creating review.")
        payload["comments"] = valid_comments

        # Don't submit a review if there are no comments and no body
        if not payload["comments"] and not payload["body"]:
             print("Skipping review creation: No comments and no summary body provided.")
             return None

        result = self._make_request("POST", reviews_url, data=json.dumps(payload))

        if result:
            print(f"Successfully created review for PR #{pr_number} on commit {commit_id[:7]}")
            return result
        else:
            print(f"Error creating review for PR #{pr_number}")
            return None

    # --- Existing methods (potentially need adjustments later) ---

    def get_file_content(self, file_path, ref):
        """Fetches the raw content of a file at a specific ref (commit SHA, branch, etc.)."""
        content_url = f"{self.api_base_url}/repos/{self.repo}/contents/{file_path}"
        query_params = {"ref": ref}

        # Override default headers for raw content
        raw_headers = self.headers.copy()
        raw_headers["Accept"] = "application/vnd.github.raw"

        try:
            response = requests.get(content_url, headers=raw_headers, params=query_params)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"Warning: File not found at path '{file_path}' for ref '{ref}'. It might be a new file.")
                return "" # Return empty string for new/deleted files at this ref
            else:
                print(f"Error fetching file content for {file_path} at ref {ref}: {e}", file=sys.stderr)
                print(f"Response body: {e.response.text}", file=sys.stderr)
                return None # Indicate a fetch error
        except requests.exceptions.RequestException as e:
            print(f"Error fetching file content for {file_path} at ref {ref}: {e}", file=sys.stderr)
            return None # Indicate a fetch error

    def post_pr_comment(self, pr_number, body):
        """Posts a general comment on the Pull Request (issue comment)."""
        issue_comment_url = f"{self.api_base_url}/repos/{self.repo}/issues/{pr_number}/comments"
        payload = json.dumps({"body": body})
        result = self._make_request("POST", issue_comment_url, data=payload)

        if result:
            print(f"Successfully posted comment to PR #{pr_number}")
            return result
        else:
            print(f"Error posting comment to PR #{pr_number}")
            return None
    
    # --- Potentially deprecated methods (keep for now, review later) ---
    
    def _get_pr_full_diff_legacy(self, pr_number):
        """LEGACY: Fetches PR title, description, and FULL diff."""
        # Kept temporarily, should switch to get_pr_metadata + compare_commits
        pr_url = f"{self.api_base_url}/repos/{self.repo}/pulls/{pr_number}"
        diff_url = f"{pr_url}.diff"
        
        pr_details = None
        diff_content = None

        try:
            # Get PR metadata (title, body)
            json_headers = self.headers.copy()
            json_headers["Accept"] = "application/vnd.github.v3+json"
            response_pr = requests.get(pr_url, headers=json_headers)
            response_pr.raise_for_status()
            pr_data = response_pr.json()
            pr_details = {
                "title": pr_data.get("title", ""),
                "description": pr_data.get("body", "")
                # Base/Head SHA are available here too but fetched in get_pr_metadata
            }
            
            # Get PR diff
            diff_headers = self.headers.copy()
            diff_headers["Accept"] = "application/vnd.github.v3.diff"
            response_diff = requests.get(diff_url, headers=diff_headers)
            response_diff.raise_for_status()
            diff_content = response_diff.text

        except requests.exceptions.RequestException as e:
            print(f"Error fetching PR details (legacy) for #{pr_number}: {e}", file=sys.stderr)
            return None, None

        return pr_details, diff_content

    def post_review_comment(self, pr_number, commit_id, path, line, body):
        """LEGACY?: Posts a single review comment. Consider using create_review instead."""
        # This posts comments individually, not as part of a review.
        # Might be useful for immediate feedback but create_review is generally preferred.
        comments_url = f"{self.api_base_url}/repos/{self.repo}/pulls/{pr_number}/comments"
        payload = json.dumps({
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
        })
        result = self._make_request("POST", comments_url, data=payload)
        if result:
            print(f"Successfully posted single comment to {path}:{line}")
            return result
        else:
            print(f"Error posting single comment to {path}:{line}")
            return None

    def get_pr_commit_id(self, pr_number):
        """LEGACY?: Fetches the HEAD commit SHA. Included in get_pr_metadata."""
        # Kept temporarily as main.py uses it. Should be replaced by get_pr_metadata.
        pr_data = self._make_request("GET", f"{self.api_base_url}/repos/{self.repo}/pulls/{pr_number}")
        if pr_data:
             head_sha = pr_data.get("head", {}).get("sha")
             if head_sha:
                 return head_sha
             else:
                 print(f"Error: Could not extract head SHA from PR data for #{pr_number}.", file=sys.stderr)
                 return None
        else:
            print(f"Error fetching PR commit ID (legacy) for #{pr_number}: {e}", file=sys.stderr)
            return None

# Example usage (for testing)
if __name__ == "__main__":
    # Requires GITHUB_TOKEN, GITHUB_REPOSITORY, and PR_NUMBER env vars set for testing
    pr_num_test = os.getenv("PR_NUMBER_TEST")
    if pr_num_test and os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPOSITORY"):
        api = GitHubAPI()
        pr_num_test = int(pr_num_test) # Ensure it's an int
        print(f"--- Testing GitHubAPI with PR #{pr_num_test} in repo {api.repo} ---")
        
        print("\n--- Testing get_pr_metadata ---")
        metadata = api.get_pr_metadata(pr_num_test)
        if metadata:
            print(f"Title: {metadata['title']}")
            print(f"Base SHA: {metadata['base_sha']}")
            print(f"Head SHA: {metadata['head_sha']}")
            base_sha = metadata['base_sha']
            head_sha = metadata['head_sha']

            print("\n--- Testing compare_commits (Base vs Head) ---")
            comparison = api.compare_commits(base_sha, head_sha)
            if comparison:
                print(f"Status: {comparison.get('status')}")
                print(f"Commits: {len(comparison.get('commits', []))}")
                print(f"Files changed: {len(comparison.get('files', []))}")
                if comparison.get('files'):
                    print(f" Example file 1: {comparison['files'][0].get('filename')}")
                    print(f"   Patch snippet: {comparison['files'][0].get('patch', '')[:100]}...")
            else:
                 print("Commit comparison failed.")

            # Find/Update/Create Comment test requires a tag and potentially existing comment
            test_tag = "<!-- Gemini Review Summary Test -->"
            print(f"\n--- Testing find_comment_with_tag ('{test_tag}') ---")
            existing_comment = api.find_comment_with_tag(pr_num_test, test_tag)

            if existing_comment:
                print(f"Found existing comment ID: {existing_comment['id']}")
                print("\n--- Testing update_comment ---")
                updated_body = f"{test_tag}\nUpdated at {time.time()}"
                api.update_comment(existing_comment['id'], updated_body)
            else:
                print("No existing comment found with the tag.")
                print("\n--- Testing post_pr_comment (to create one) ---")
                initial_body = f"{test_tag}\nCreated at {time.time()}"
                api.post_pr_comment(pr_num_test, initial_body)

            # Test create_review (posts an empty review comment if no comments provided)
            # print("\n--- Testing create_review (dummy comment) ---")
            # dummy_comments = [{"path": "README.md", "line": 1, "body": "Test review comment from script."}]
            # api.create_review(pr_num_test, head_sha, dummy_comments, body="Overall test review summary.")

        else:
            print("\nCould not fetch PR metadata.")
    else:
        print("\nSkipping github_api.py example usage.")
        print("Set GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER_TEST env vars to run.") 