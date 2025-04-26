# src/gemini_client.py - Wrapper for Gemini API calls

import os
import json
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import sys

# Get model name from environment variable, falling back to a default
DEFAULT_MODEL_NAME = "gemini-1.5-flash-latest"
ENV_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", DEFAULT_MODEL_NAME)

class GeminiClient:
    def __init__(self):
        # Read API Key from environment variable (set by action.yml)
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = ENV_MODEL_NAME # Use the model name determined at module load time
        
        if not self.api_key:
            # Exit if API key is missing - this is mandatory
            print("Error: GEMINI_API_KEY environment variable not set.", file=sys.stderr)
            sys.exit("Missing GEMINI_API_KEY environment variable.") # Use sys.exit with message
            # Or raise ValueError("GEMINI_API_KEY environment variable not set.") if main.py handles it

        try:
            genai.configure(api_key=self.api_key)
            # Safety settings (consider making these configurable too)
            self.safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            }
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                safety_settings=self.safety_settings
            )
            print(f"GeminiClient initialized with model: {self.model_name}")
        except Exception as e:
            print(f"Error configuring Gemini client: {e}", file=sys.stderr)
            # Exit if model initialization fails
            sys.exit(f"Failed to initialize Gemini model '{self.model_name}': {e}")

    def generate_text(self, prompt):
        """Sends a prompt to Gemini and returns the raw text response."""
        if not self.model:
            print("Error: Gemini model not initialized.", file=sys.stderr)
            return None # Return None to indicate failure

        response_text = None
        try:
            print(f"\n--- Sending Text Prompt to Gemini ({self.model_name}) ---")
            # print(prompt) # Keep prompt logging minimal unless debugging
            print("Prompt length:", len(prompt), "chars")
            print("-------------------------------------------------")

            # Make the API call
            response = self.model.generate_content(prompt)

            # --- Process Response ---
            if response.parts:
                 response_text = response.text # Access the combined text from all parts
            else:
                 # Check for prompt feedback (e.g., blocked due to safety)
                 print(f"Warning: Gemini text response missing content. Prompt Feedback: {response.prompt_feedback}", file=sys.stderr)
                 return None # Indicate blocked/empty response

            print(f"\n--- Raw Gemini Text Response ---\n{response_text}\n--------------------------------")
            return response_text.strip()

        except Exception as e:
            # Catch other potential errors (API errors, etc.)
            print(f"Error during Gemini API call for text generation: {e}", file=sys.stderr)
            print(f"Exception Type: {type(e).__name__}", file=sys.stderr)
            # Return None to indicate failure
            return None

    def get_review(self, prompt):
        """Sends the prompt to Gemini and expects a JSON response."""
        if not self.model:
            # This should ideally not be reached due to __init__ checks
            print("Error: Gemini model not initialized.", file=sys.stderr)
            return {"reviews": []}

        response_text = None
        try:
            print(f"\n--- Sending Prompt to Gemini ({self.model_name}) ---")
            # print(prompt) # Keep prompt logging minimal unless debugging
            print("Prompt length:", len(prompt), "chars")
            print("-------------------------------------------------")

            # Make the API call
            # Use generate_content for direct text prompting
            response = self.model.generate_content(prompt)

            # --- Process Response --- 
            # Access the generated text content
            # Handle potential lack of response or errors within the response object
            if response.parts:
                 response_text = response.text # Access the combined text from all parts
            else:
                 # Check for prompt feedback (e.g., blocked due to safety)
                 print(f"Warning: Gemini response missing content. Prompt Feedback: {response.prompt_feedback}", file=sys.stderr)
                 return {"reviews": []} # Return empty if blocked or no response
                 
            # Clean the response: Sometimes Gemini might add ```json ... ``` markers
            response_text = response_text.strip().removeprefix("```json").removesuffix("```").strip()

            print(f"\n--- Raw Gemini Response (cleaned) ---\n{response_text}\n------------------------------------")

            # Parse the JSON response
            review_data = json.loads(response_text)

            # Basic validation of the response structure
            if "reviews" not in review_data or not isinstance(review_data["reviews"], list):
                 print(f"Warning: Invalid JSON response format from Gemini: 'reviews' key missing or not a list. Response: {response_text}", file=sys.stderr)
                 return {"reviews": []} # Return empty on format error

            # Validate individual review items
            valid_reviews = []
            for item in review_data["reviews"]:
                if not isinstance(item, dict) or not all(k in item for k in ("lineNumber", "reviewComment")):
                    print(f"Warning: Invalid review item format: {item}. Missing keys.", file=sys.stderr)
                    continue # Skip invalid item
                if not isinstance(item["lineNumber"], int):
                    print(f"Warning: Invalid review item format: {item}. 'lineNumber' not an integer.", file=sys.stderr)
                    continue # Skip invalid item
                if not isinstance(item["reviewComment"], str) or not item["reviewComment"].strip():
                    print(f"Warning: Invalid review item format: {item}. 'reviewComment' not a non-empty string.", file=sys.stderr)
                    continue # Skip invalid item
                valid_reviews.append(item)

            return {"reviews": valid_reviews}

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response from Gemini: {e}", file=sys.stderr)
            print(f"Raw response text was: {response_text}", file=sys.stderr)
            return {"reviews": []}
        except Exception as e:
            # Catch other potential errors (API errors, validation errors, etc.)
            print(f"Error during Gemini API call or processing: {e}", file=sys.stderr)
            # Log the specific exception type for better debugging
            print(f"Exception Type: {type(e).__name__}", file=sys.stderr)
            if response_text: # If we got some text before the error
                print(f"Response text before error: {response_text}", file=sys.stderr)
            return {"reviews": []}

# Example usage (for testing)
if __name__ == "__main__":
    # Requires GEMINI_API_KEY env var set for testing
    if os.getenv("GEMINI_API_KEY"):
        try:
            client = GeminiClient()
            test_prompt = (
                "You are reviewing code.\n" 
                "File: test.py\n"
                "Diff:\n" 
                "```diff\n" 
                "+ def hello():\n" 
                "+   print(\"Hello\")\n"
                "```\n" 
                "Respond in JSON only: {\"reviews\": [{\"lineNumber\": 2, \"reviewComment\": \"Consider adding type hints.\"}]}"
            )
            review = client.get_review(test_prompt)
            print("\n--- Parsed Review Data ---")
            print(json.dumps(review, indent=2))
        except ValueError as e:
            print(f"Test setup failed: {e}")
    else:
        print("Skipping gemini_client.py example usage.")
        print("Set GEMINI_API_KEY environment variable to run.") 