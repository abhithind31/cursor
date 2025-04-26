# src/config.py - Configuration loading and management

import os
import yaml # Requires PyYAML package

# Get default config path from environment variable set by action.yml, 
# defaulting to the standard path if not set (e.g., during local testing)
DEFAULT_CONFIG_PATH_IN_REPO = os.getenv("CONFIG_PATH", ".github/gemini-reviewer.yml") 
DEFAULT_INSTRUCTIONS = "Focus on bugs, security, and performance. Do not suggest code comments."

def load_config(config_path_override=None):
    """Loads configuration from the YAML file or returns defaults.
    Uses config_path_override if provided, otherwise uses DEFAULT_CONFIG_PATH_IN_REPO.
    """
    target_config_path = config_path_override if config_path_override else DEFAULT_CONFIG_PATH_IN_REPO
    
    config = {
        "exclude": [],
        "custom_instructions": DEFAULT_INSTRUCTIONS,
        "jira": None # Placeholder for Jira config
    }

    # Important: When running as an action, the config file path is relative
    # to the root of the *consuming* repository, not the action repository.
    # The GITHUB_WORKSPACE variable points to the root of the consuming repo checkout.
    workspace_path = os.getenv("GITHUB_WORKSPACE", ".") # Default to current dir if not in GHA
    absolute_config_path = os.path.join(workspace_path, target_config_path)

    print(f"Attempting to load config from: {absolute_config_path} (relative: {target_config_path})")

    if os.path.exists(absolute_config_path):
        try:
            with open(absolute_config_path, 'r') as f:
                user_config = yaml.safe_load(f)
            
            if user_config:
                config["exclude"] = user_config.get("exclude", [])
                # Ensure instructions are treated as a single string block
                config["custom_instructions"] = user_config.get("custom_instructions", DEFAULT_INSTRUCTIONS).strip()
                config["jira"] = user_config.get("jira") # Load entire Jira block if present
                print(f"Loaded configuration from {absolute_config_path}")
            else:
                 print(f"Configuration file {absolute_config_path} is empty, using defaults.")

        except yaml.YAMLError as e:
            print(f"Error parsing YAML configuration file {absolute_config_path}: {e}")
            print("Using default configuration.")
        except Exception as e:
            print(f"Error reading configuration file {absolute_config_path}: {e}")
            print("Using default configuration.")
    else:
        print(f"Configuration file {absolute_config_path} not found, using defaults.")

    # Basic validation
    if not isinstance(config["exclude"], list):
        print(f"Warning: 'exclude' key in config is not a list. Ignoring.")
        config["exclude"] = []
    if not isinstance(config["custom_instructions"], str):
         print(f"Warning: 'custom_instructions' key in config is not a string. Using default.")
         config["custom_instructions"] = DEFAULT_INSTRUCTIONS

    return config

# Example usage (for testing)
if __name__ == "__main__":
    # Create a dummy config file for testing
    dummy_path = "./temp_test_config.yml"
    dummy_content = """
exclude:
  - "*.log"
  - "/dist/"
custom_instructions: |
  Line 1 of instructions.
  Line 2, check for XYZ.
jira:
  project_keys: ["TEST"]
"""
    with open(dummy_path, 'w') as f:
        f.write(dummy_content)
        
    print("--- Testing config loading --- ")
    loaded_cfg = load_config(dummy_path)
    print("\nLoaded Config:")
    import json
    print(json.dumps(loaded_cfg, indent=2))
    
    # Test default loading
    print("\n--- Testing default config loading --- ")
    default_cfg = load_config("non_existent_file.yml")
    print("\nDefault Config:")
    print(json.dumps(default_cfg, indent=2))

    # Clean up dummy file
    os.remove(dummy_path) 