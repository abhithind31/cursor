# src/utils.py - Utility functions (e.g., diff parsing, file filtering)

import re
from fnmatch import fnmatch # For gitignore-style pattern matching

# --- Constants ---
HUNK_HEADER_RE = re.compile(r'^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@')

# --- New Hunk Parsing (Replaces parse_diff) ---
def parse_hunks_from_patch(patch_text):
    """Parses patch text for a single file into detailed hunk information.

    Args:
        patch_text (str): The raw diff/patch text for one file.

    Returns:
        list[dict]: A list of dictionaries, each representing a hunk:
            {
                'header': str,         # The full @@ ... @@ line
                'content': str,        # The content of the hunk (including header)
                'new_start_line': int, # Start line number in the new file
                'new_end_line': int,   # End line number in the new file
                'new_line_count': int  # Number of lines in the new file's part of the hunk
            }
        Or None if parsing fails completely.
    """
    if not patch_text:
        return []

    hunks = []
    current_hunk_content = []
    current_hunk_info = None

    lines = patch_text.splitlines()

    for line in lines:
        match = HUNK_HEADER_RE.match(line)
        if match:
            # Finalize previous hunk if exists
            if current_hunk_info:
                current_hunk_info['content'] = '\n'.join(current_hunk_content)
                hunks.append(current_hunk_info)
            
            # Parse new hunk header
            try:
                old_start = int(match.group(1))
                old_len = int(match.group(2) or 1) # Default length is 1 if omitted
                new_start = int(match.group(3))
                new_len = int(match.group(4) or 1)
            except ValueError:
                print(f"Warning: Could not parse hunk header integers: {line}")
                current_hunk_info = None # Invalidate current hunk
                current_hunk_content = []
                continue

            current_hunk_info = {
                'header': line,
                'content': "", # Will be filled later
                'new_start_line': new_start,
                'new_end_line': new_start + new_len - 1,
                'new_line_count': new_len
            }
            current_hunk_content = [line] # Start content with header
            # print(f"Debug: Parsed Hunk Header - Start: {new_start}, Len: {new_len}, End: {new_start + new_len - 1}")
        
        elif current_hunk_info:
            # Add line to current hunk content if we are inside a valid hunk
            current_hunk_content.append(line)

    # Finalize the last hunk
    if current_hunk_info:
        current_hunk_info['content'] = '\n'.join(current_hunk_content)
        hunks.append(current_hunk_info)

    if not hunks and patch_text: # Check if parsing failed completely despite input
         print(f"Warning: Could not parse any hunks from the provided patch text.")
         # print(f"Patch text snippet: {patch_text[:200]}...")
         return None # Indicate failure to parse

    return hunks

# --- New Line Mapping Logic ---
def map_review_to_file_line(hunk_line_relative, hunk_info):
    """Maps a hunk-relative line number (from AI) to an absolute file line number.

    Args:
        hunk_line_relative (int): 1-based line number within the hunk content
                                   (excluding the @@ header line) reported by the AI.
        hunk_info (dict): The parsed information for this specific hunk from
                          parse_hunks_from_patch.

    Returns:
        int | None: The corresponding 1-based line number in the new file, or None
                    if the line is invalid, deleted, or mapping fails.
    """
    if not hunk_info or hunk_line_relative <= 0:
        return None

    hunk_content_lines = hunk_info['content'].splitlines()
    # Index relative to the content lines array (0-based), skipping header
    content_line_index = hunk_line_relative 

    if content_line_index >= len(hunk_content_lines):
        print(f"Warning: Hunk relative line {hunk_line_relative} is out of bounds for hunk content length {len(hunk_content_lines)}.")
        return None

    target_line_text = hunk_content_lines[content_line_index]
    line_type = target_line_text[0] if target_line_text else ' '

    # Cannot comment on deleted lines
    if line_type == '-':
        print(f"Debug: Hunk relative line {hunk_line_relative} corresponds to a deleted line.")
        return None

    # Count lines starting with ' ' or '+' up to and including the target relative line
    new_file_line_count = 0
    for i in range(1, content_line_index + 1): # Iterate through content lines up to target
        line_text = hunk_content_lines[i]
        type_char = line_text[0] if line_text else ' '
        if type_char in [' ', '+']:
            new_file_line_count += 1

    # Absolute line number is the hunk's start line + the count - 1
    # (because the count includes the target line itself)
    absolute_line = hunk_info['new_start_line'] + new_file_line_count - 1
    # print(f"Debug: map_review: Rel={hunk_line_relative}, Start={hunk_info['new_start_line']}, Count={new_file_line_count}, Abs={absolute_line}")
    return absolute_line

def find_best_patch_for_line(target_file_line, file_patches):
    """Finds the best patch hunk to attach a comment to for a target file line.
    
    If the line falls within a patch, returns that line.
    If the line falls outside all patches, finds the closest patch and returns
    the *last* line of that patch's range, indicating remapping occurred.

    Args:
        target_file_line (int): The absolute target line number in the new file.
        file_patches (list[dict]): List of parsed hunk info dictionaries for the file.

    Returns:
        tuple[int, bool] | tuple[None, False]: 
            (line_to_comment_on, was_remapped) or (None, False) if no patches.
    """
    if not file_patches:
        return None, False

    best_patch = None
    min_distance = float('inf')

    for patch in file_patches:
        start = patch['new_start_line']
        end = patch['new_end_line']

        # Check for exact containment first
        if start <= target_file_line <= end:
            # print(f"Debug: find_best_patch: Line {target_file_line} is within patch {start}-{end}.")
            return target_file_line, False # Exact match, no remapping

        # Calculate distance if outside
        distance = 0
        if target_file_line < start:
            distance = start - target_file_line
        else: # target_file_line > end
            distance = target_file_line - end
        
        if distance < min_distance:
            min_distance = distance
            best_patch = patch

    # If we get here, no exact match was found. Use the closest patch.
    if best_patch:
        # Attach to the last line of the closest patch range
        line_to_comment_on = best_patch['new_end_line'] 
        # print(f"Debug: find_best_patch: Line {target_file_line} is outside patches. Closest patch is {best_patch['new_start_line']}-{best_patch['new_end_line']}. Remapping to {line_to_comment_on}.")
        return line_to_comment_on, True # Remapped
    else:
        # Should not happen if file_patches is not empty, but handle defensively
        print("Warning: find_best_patch_for_line couldn't find a best patch despite having patches.")
        return None, False

# --- Legacy Diff Parsing (Commented Out) ---
# def parse_diff(diff_text):
#     """Parses a unified diff string into a dictionary structure.
#     ...
#     """
#     files = {}
#     ...
#     return files

# --- File Filtering ---
def should_exclude_file(file_path, exclude_patterns):
    """Checks if a file path matches any of the exclude patterns."""
    if not exclude_patterns:
        return False
    for pattern in exclude_patterns:
        if fnmatch(file_path, pattern):
            # print(f"Excluding file '{file_path}' due to pattern '{pattern}'") # Debugging
            return True
    return False

# --- Placeholder for Jira functions (Phase 3) ---
def extract_jira_keys(text, project_keys):
    """Finds potential Jira keys (e.g., ABC-123) in text."""
    if not text or not project_keys:
        return []
    # Simple regex: Look for project keys followed by hyphen and digits
    keys_pattern = r'\b(' + '|'.join(project_keys) + r')-\d+\b'
    found_keys = re.findall(keys_pattern, text, re.IGNORECASE)
    return list(set(found_keys)) # Return unique keys

# --- Legacy Hunk Line to File Line Mapping (Commented Out) ---
# def map_hunk_line_to_file_line(hunk_header, hunk_content, hunk_line_number):
#     """Maps a line number within the hunk_content (1-based relative to hunk)
#        to the corresponding line number in the file's new version (1-based).
#     ...
#     """
#     # Print debug info to help troubleshoot
#     print(f"Debug: Mapping hunk line {hunk_line_number} in header {hunk_header}")
#     
#     # Parse the hunk header to get the starting line in new file
#     match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', hunk_header)
#     ...
#     return file_line

# --- Context Extraction Logic (Remains largely the same) ---

def _get_indentation(line):
    """Returns the number of leading spaces."""
    return len(line) - len(line.lstrip(' '))

def _find_block_boundaries(lines, start_index):
    """Attempts to find function/class boundaries around a start index based on indentation (Python focus)."""
    if start_index >= len(lines) or start_index < 0:
        return None, None

    start_line_indent = _get_indentation(lines[start_index])

    # Find the start of the block (e.g., 'def' or 'class' line)
    block_start_index = start_index
    for i in range(start_index, -1, -1):
        line = lines[i]
        indent = _get_indentation(line)
        # If we find a line with less indentation, the line *after* it is likely the start
        # Or if we hit the top-level 'def' or 'class'
        if indent < start_line_indent or (indent == 0 and (line.strip().startswith("def ") or line.strip().startswith("class "))):
            block_start_index = i
            # If the found line is 'def' or 'class', keep it. Otherwise, the block starts *after* this less indented line.
            if not (line.strip().startswith("def ") or line.strip().startswith("class ")):
                 block_start_index = i + 1
            break
        # If we are already at indent 0 and haven't found def/class, assume the start is the first line
        if indent == 0 and i < start_index:
            block_start_index = i
            break
    else: # Loop completed without break (reached file start)
        block_start_index = 0


    # Find the end of the block
    block_end_index = start_index
    block_start_line_indent = _get_indentation(lines[block_start_index]) # Indent of the 'def' or 'class' line itself

    for i in range(start_index + 1, len(lines)):
        line = lines[i]
        # Skip empty or comment lines for boundary detection
        if not line.strip() or line.strip().startswith('#'):
            block_end_index = i
            continue

        indent = _get_indentation(line)
        # Block ends when we find a line with indentation <= the block's starting line's indentation
        # Need to be careful if the block starts at indent 0
        if block_start_line_indent == 0:
             if indent == 0 and i > block_start_index: # Found another top-level definition or end of file
                 block_end_index = i -1 # The previous line was the end
                 break
        elif indent <= block_start_line_indent:
             block_end_index = i - 1 # The previous line was the end
             break
        block_end_index = i # Otherwise, this line is still part of the block
    else: # Loop completed without break (reached file end)
        block_end_index = len(lines) - 1

    # Ensure start <= end
    if block_start_index > block_end_index:
        # This might happen if the change is on the very last line and logic gets confused
        # Fallback to just the line itself or a small window? For now, let's return the original index.
        return start_index, start_index

    # print(f"Debug: Found block from {block_start_index + 1} to {block_end_index + 1}")
    return block_start_index, block_end_index


def extract_context_around_hunk(full_file_content, hunk_header, fallback_lines=20):
    """Extracts relevant context (imports + function/class or fallback lines) for a hunk."""

    if full_file_content is None: # Handle API error case from get_file_content
        print("Warning: Cannot extract context, full_file_content is None.")
        return ""
    if not full_file_content.strip(): # Handle case where file was empty or deleted/not found
        # print("Debug: Full file content is empty, likely a new or deleted file. No context extracted.")
        return "" # No context to extract

    lines = full_file_content.splitlines()

    # 1. Extract Imports (Python specific for now)
    imports_section = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("import ") or stripped_line.startswith("from "):
            imports_section.append(line)
        elif imports_section and stripped_line: # Stop after first non-import, non-empty line
            break
    imports_context = "\n".join(imports_section)

    # 2. Find Hunk's position in the original file
    #    We need the *starting line number* in the *new* file from the hunk header
    #    to find the approximate location for context.
    match = HUNK_HEADER_RE.match(hunk_header)
    if not match:
        print(f"Warning: Could not parse hunk header '{hunk_header}' for context extraction.")
        # Fallback: Provide imports and maybe first/last N lines?
        context_lines = lines[:fallback_lines] + ["..."] + lines[-fallback_lines:]
        return imports_context + "\n\n... (Context fallback due to header parse error) ...\n\n" + "\n".join(context_lines)

    try:
        # Use the OLD start line number (group 1) as it relates to the base file content
        hunk_start_line_old = int(match.group(1))
    except ValueError:
        print(f"Warning: Could not parse hunk old start line number from header: {hunk_header}")
        # Fallback as above
        context_lines = lines[:fallback_lines] + ["..."] + lines[-fallback_lines:]
        return imports_context + "\n\n... (Context fallback due to header number parse error) ...\n\n" + "\n".join(context_lines)

    # Find the corresponding line index (0-based) in the *original* full file content
    # Use the old start line number directly, adjusted for 0-based index.
    # Clamp the index to valid bounds.
    approx_line_index = max(0, min(hunk_start_line_old - 1, len(lines) - 1))

    # 3. Find Function/Class Boundaries
    #    Use indentation-based logic (can be improved for other languages)
    block_start_index, block_end_index = _find_block_boundaries(lines, approx_line_index)

    # 4. Construct Context String
    block_context = ""
    if block_start_index is not None and block_end_index is not None:
        # Ensure indices are within bounds
        block_start_index = max(0, block_start_index)
        block_end_index = min(len(lines) - 1, block_end_index)
        # Extract lines, handling potential empty ranges
        if block_start_index <= block_end_index:
             block_lines = lines[block_start_index : block_end_index + 1]
             block_context = "\n".join(block_lines)
             # Optional: Add ellipsis if block is very large?
    
    # Fallback if block finding failed or produced empty result
    if not block_context:
         print(f"Debug: Block context finding failed or was empty for hunk starting near line {approx_line_index + 1}. Using fallback window.")
         start_fallback = max(0, approx_line_index - fallback_lines // 2)
         end_fallback = min(len(lines), approx_line_index + fallback_lines // 2 + 1)
         fallback_context_lines = lines[start_fallback:end_fallback]
         block_context = "\n".join(fallback_context_lines)
         if start_fallback > 0:
             block_context = "...\n" + block_context
         if end_fallback < len(lines):
             block_context = block_context + "\n..."

    # Combine imports and block context
    final_context = imports_context
    if imports_context and block_context:
        final_context += "\n\n---
\n" # Separator
    final_context += block_context

    # Limit total context size? (Could do this here or in the calling function)
    # MAX_CONTEXT_CHARS = 3000
    # if len(final_context) > MAX_CONTEXT_CHARS:
    #     final_context = final_context[:MAX_CONTEXT_CHARS] + "\n... (Context truncated)"

    return final_context.strip()

# Example usage (for testing) - Commented out line-by-line
# if __name__ == "__main__":
#     test_diff = """
# diff --git a/README.md b/README.md
# index abc..def 100644
# --- a/README.md
# +++ b/README.md
# @@ -1,3 +1,4 @@
#  # Test Project
#  
#  This is a test.
# +Adding a new line.
# diff --git a/src/main.py b/src/main.py
# index ghi..jkl 100644
# --- a/src/main.py
# +++ b/src/main.py
# @@ -5,5 +5,6 @@
#  
#  def main():
#      print("Hello")
# +    print("World")
#  
#  if __name__ == "__main__":
#      main()
# diff --git a/docs/guide.txt b/docs/guide.txt
# new file mode 100644
# index 000..mno
# --- /dev/null
# +++ b/docs/guide.txt
# @@ -0,0 +1 @@
# +New guide.
# """
    
#     print("--- Testing diff parsing ---")
#     parsed_files = parse_diff(test_diff)
#     import json
#     print(json.dumps(parsed_files, indent=2))

#     print("\n--- Testing file exclusion ---")
#     exclude_list = ["*.md", "docs/*", "*.log"]
#     print(f"Exclude patterns: {exclude_list}")
#     print(f"Should exclude 'README.md': {should_exclude_file('README.md', exclude_list)}")
#     print(f"Should exclude 'src/main.py': {should_exclude_file('src/main.py', exclude_list)}")
#     print(f"Should exclude 'docs/guide.txt': {should_exclude_file('docs/guide.txt', exclude_list)}")
#     print(f"Should exclude 'app.log': {should_exclude_file('app.log', exclude_list)}")
#     print(f"Should exclude 'src/utils.py': {should_exclude_file('src/utils.py', [])}") # No patterns
    
#     print("\n--- Testing Jira key extraction ---")
#     test_text = "Fixes ABC-123, relates to CORE-456. Also mentions xyz-789 but that's not a key."
#     keys = extract_jira_keys(test_text, ["ABC", "CORE"])
#     print(f"Found keys in '{test_text}': {keys}") 

#     print("\n--- Testing Hunk Line Mapping --- ")
#     header1 = "@@ -5,5 +5,6 @@"
#     content1 = """@@ -5,5 +5,6 @@
#  
#  def main():
#      print("Hello")
# -    # Old comment
# +    print("World") # AI comments on this line (hunk line 4)
#  
#  if __name__ == "__main__":
#      main()"""
#     map_result1 = map_hunk_line_to_file_line(header1, content1, 4)
#     print(f"Mapping hunk line 4 in Hunk 1: {map_result1} (Expected: 8)") # 5(start)+0(space)+0(space)+1(+)=8

#     header2 = "@@ -1,3 +1,4 @@"
#     content2 = """@@ -1,3 +1,4 @@
#  # Test Project
#  
#  This is a test.
# +Adding a new line.""" # AI comments on this line (hunk line 4)
#     map_result2 = map_hunk_line_to_file_line(header2, content2, 4)
#     print(f"Mapping hunk line 4 in Hunk 2: {map_result2} (Expected: 4)") # 1(start)+0(space)+0(space)+1(+)=4

#     map_result3 = map_hunk_line_to_file_line(header1, content1, 3) # AI comment on line 3 (deleted line)
#     print(f"Mapping hunk line 3 in Hunk 1: {map_result3} (Expected: None)")

#     map_result4 = map_hunk_line_to_file_line(header1, content1, 6) # AI comment on line 6 (context line)
#     print(f"Mapping hunk line 6 in Hunk 1: {map_result4} (Expected: 10)") # 5(start)+0+0+1+1+1=10

#     header3 = "@@ -0,0 +1 @@"
#     content3 = """@@ -0,0 +1 @@
# +New guide.""" # AI comment on line 1 (only line)
#     map_result5 = map_hunk_line_to_file_line(header3, content3, 1)
#     print(f"Mapping hunk line 1 in Hunk 3: {map_result5} (Expected: 1)") # 1(start)+0=1

#     print("\n--- Testing Context Extraction ---")
#     test_py_content = """
# import os
# import sys
# from collections import defaultdict
# 
# # A comment
# class MyClass:
#     def __init__(self, name):
#         self.name = name
# 
#     def greet(self, message):
#         """Greets the user."""
#         print(f"Hello {self.name}, {message}!")
#         if len(message) > 10:
#              print("That's a long message.")
#         # Some more code
# 
# def helper_function(data):
#      counts = defaultdict(int)
#      for item in data:
#          counts[item] += 1
#      return counts
# 
# # Top level code
# x = 10
# y = helper_function([1, 2, 2, 3])
# print(f"Result: {y}")
# 
# # Another function
# def process_list(items):
#     processed = []
#     for i in items:
#         if i % 2 == 0:
#             processed.append(i * 2) # Change here
#     return processed
# 
# z = process_list([1,2,3,4,5])
# """

#     # Test case 1: Change inside MyClass.greet
#     hunk_header1 = "@@ -10,5 +10,6 @@" # Assume change is around line 12 ("That's a long message.")
#     context1 = extract_context_around_hunk(test_py_content, hunk_header1)
#     print("\nContext for Hunk 1 (inside greet):")
#     print(context1)

#     # Test case 2: Change inside helper_function
#     hunk_header2 = "@@ -16,4 +17,5 @@" # Assume change is around line 19 (counts[item] += 1)
#     context2 = extract_context_around_hunk(test_py_content, hunk_header2)
#     print("\nContext for Hunk 2 (inside helper_function):")
#     print(context2)

#     # Test case 3: Change in top-level code
#     hunk_header3 = "@@ -22,3 +23,4 @@" # Assume change is around line 24 (print(f"Result: {y}"))
#     context3 = extract_context_around_hunk(test_py_content, hunk_header3)
#     print("\nContext for Hunk 3 (top-level code):")
#     print(context3) # Should likely fallback to N lines

#     # Test case 4: Change inside process_list
#     hunk_header4 = "@@ -29,4 +30,5 @@" # Assume change is around line 31 (processed.append...)
#     context4 = extract_context_around_hunk(test_py_content, hunk_header4)
#     print("\nContext for Hunk 4 (inside process_list):")
#     print(context4)

#     # Test case 5: Empty file content
#     context5 = extract_context_around_hunk("", "@@ -0,0 +1,1 @@")
#     print("\nContext for Hunk 5 (empty file):")
#     print(context5)

#     # Test case 6: Change at the very beginning (imports)
#     hunk_header6 = "@@ -1,3 +1,4 @@" # Assume change is around line 2 (import sys)
#     context6 = extract_context_around_hunk(test_py_content, hunk_header6)
#     print("\nContext for Hunk 6 (imports):")
#     print(context6) # Should fallback, block finder might return 0,0