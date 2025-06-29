import json
import os
from typing import Dict, Set
from dotenv import load_dotenv
from pathlib import Path
import secrets
from enums import Envs, ServiceTypes

"""
Enhanced secret key management with 4-level inheritance for both files and variables.

File inheritance: os.environ -> .env → .env.{env} → .env.{project} → .env.{project}.{env}
Variable inheritance: {TARGET} → {ENV}_{TARGET} → {PROJECT}_{TARGET} → {PROJECT}_{ENV}_{TARGET}
"""

# Configuration constants
LOCAL_DIR = Path(__file__).parent
SECRETS_DIR = '/run/secrets'  # Docker/K8s mounted secrets path
DEFAULT_TARGETS = {
    'postgres': True,   # pwd - auto-generate if missing
    'redis': True,      # pwd - auto-generate if missing
    'opensearch': True, # pwd - auto-generate if missing
    'openapi': False,   # key - must be provided manually
    'stripe': False,    # secret key - must be provided manually
    'gmail': False      # pwd - must be provided manually
}


class SecretsManager:
    """
    Enhanced secret key management with 4-level inheritance for both files and variables.
    
    Provides configurable secret management with auto-generation capabilities for 
    infrastructure services while requiring manual setup for external service credentials.
    
    Examples:
        ```python
        # Load secrets with default targets
        secrets = SecretsManager.load_secrets("ecommerce", "prod")
        
        # Create secrets JSON with custom targets
        custom_targets = {'postgres': True, 'custom_api': False}
        SecretsManager.create_secrets_json("ecommerce", "prod", targets=custom_targets)
        
        # Get specific secret
        db_password = SecretsManager.get_secret("ecommerce", "prod", "postgres")
        ```
    """
    
    @staticmethod
    def get_all_targets(targets: Dict[str, bool] = None) -> Set[str]:
        """Return the secrets targets for automatic generation of secrets.json file"""
        if targets is None:
            targets = DEFAULT_TARGETS
        return set(targets.keys())
    
    @staticmethod
    def _get_secret_key(project: str, env: Envs, target: str) -> str:
        """Generate secret key using naming convention: {project}_{env}_{target}"""
        env = Envs.to_enum(env)
        return f'{project}_{env.value}_{target}'
    
    @staticmethod
    def load_env_files(project: str, env: Envs) -> bool:
        """
        Load environment variables with 4-level inheritance mechanism.
        
        Loads .env files in inheritance order (least specific to most specific):
        1. .env (base configuration) - LOWEST PRIORITY
        2. .env.{env} (e.g., .env.prod) - Environment-specific overrides
        3. .env.{project} (e.g., .env.ecommerce) - Project-specific overrides  
        4. .env.{project}.{env} (e.g., .env.ecommerce.prod) - HIGHEST PRIORITY
        
        Each file found is loaded and can override values from previous files.
        This creates an inheritance chain where more specific configurations
        override more general ones.

        The locations searched, in order, are current directory and then the configured local_dir.
        
        Returns:
            bool: True if at least one .env file was loaded
        """
        env = Envs.to_enum(env)
        # Define file patterns in inheritance order (least specific to most specific)
        env_files = [
            '.env',                           # Base configuration - Lowest priority
            f'.env.{env.value}',             # Environment-specific - .env.dev (FIXED)
            f'.env.{project}',               # Project-specific - .env.testlocal  
            f'.env.{project}.{env.value}',   # Most specific - .env.testlocal.dev (FIXED)
        ]
        
        # Define search locations
        search_locations = [
            Path.cwd(),                    # Current working directory
            LOCAL_DIR,                     # Module directory
        ]
        
        files_loaded = 0
        
        # Load files in inheritance order
        for env_file in env_files:
            file_found = False
            
            # Try each location for this file
            for location in search_locations:
                env_path = location / env_file
                
                if env_path.exists():
                    print(f"Loading: {env_path}")
                    
                    # Load this file (will override any previous values)
                    loaded = load_dotenv(env_path, override=True)
                    if loaded:
                        print(f"  ✓ Loaded: {env_path}")
                        files_loaded += 1
                        file_found = True
                        break  # Found in this location, move to next file
                    else:
                        print(f"  ✗ Failed to load: {env_path}")
            
            if not file_found:
                print(f"  Not found: {env_file} (in any location)")
        
        if files_loaded > 0:
            print(f"✓ Loaded {files_loaded} .env files with inheritance")
            return True
        else:
            print("No .env files found in any location")
            return False

    @staticmethod
    def load_secrets_from_env(project: str, env: Envs, targets: Dict[str, bool] = None) -> Dict[str, str]:
        """
        Load secrets from environment variables with 4-level variable inheritance.
        Auto-generates missing secrets for targets marked as auto-generatable.
        
        Tries variable names in inheritance order (least specific to most specific):
        1. {TARGET} (e.g., POSTGRES) - Base/default value
        2. {ENV}_{TARGET} (e.g., PROD_POSTGRES) - Environment-specific
        3. {PROJECT}_{TARGET} (e.g., ECOMMERCE_POSTGRES) - Project-specific
        4. {PROJECT}_{ENV}_{TARGET} (e.g., ECOMMERCE_PROD_POSTGRES) - Most specific
        
        More specific variable names override less specific ones, allowing for
        a proper inheritance chain in environment variable naming.

        Loads .env files in inheritance order (least specific to most specific):
        1. .env (base configuration) - LOWEST PRIORITY
        2. .env.{env} (e.g., .env.prod) - Environment-specific overrides
        3. .env.{project} (e.g., .env.ecommerce) - Project-specific overrides  
        4. .env.{project}.{env} (e.g., .env.ecommerce.prod) - HIGHEST PRIORITY
        
        Each file found is loaded and can override values from previous files.
        This creates an inheritance chain where more specific configurations
        override more general ones.

        The locations searched, in order, are current directory and then the configured local_dir.
        
        Args:
            project: Project name (e.g., "ecommerce")
            env: Environment name (e.g., "prod")
            targets: Dict mapping secret names to auto-generation flags (defaults to DEFAULT_TARGETS)
            
        Returns:
            Dict mapping target names to secret values
        """
        if targets is None:
            targets = DEFAULT_TARGETS
            
        env = Envs.to_enum(env)
        # Load .env files first
        SecretsManager.load_env_files(project, env)
        
        result = {}
        missing_secrets = []
        
        print(f"\nLooking for secrets with 4-level inheritance for {project}/{env.value}:")
        
        for target in sorted(SecretsManager.get_all_targets(targets)):
            auto_generate = targets[target]  # Get auto-generate flag
            target_upper = target.upper()
            env_upper = env.value.upper()
            project_upper = project.upper()
            
            # Define variable name patterns in inheritance order (least to most specific)
            var_patterns = [
                target_upper,                                    # POSTGRES (base)
                f"{env_upper}_{target_upper}",                  # DEV_POSTGRES (env-specific)
                f"{project_upper}_{target_upper}",              # TESTLOCAL_POSTGRES (project-specific)
                f"{project_upper}_{env_upper}_{target_upper}",  # TESTLOCAL_DEV_POSTGRES (most specific)
            ]
            
            secret_value = None
            found_pattern = None
            
            # Try each pattern in inheritance order - later ones override earlier ones
            for pattern in var_patterns:
                value = os.environ.get(pattern)
                if value:
                    secret_value = value
                    found_pattern = pattern
                    print(f"  ✓ Found {target}: {pattern}")
                else:
                    print(f"    Tried: {pattern} (not found)")
            
            if secret_value:
                result[target] = secret_value
                print(f"  → Using: {found_pattern} for {target}")
            elif auto_generate:
                # Auto-generate missing secret
                password = secrets.token_urlsafe(18)
                most_specific = f"{project_upper}_{env_upper}_{target_upper}"
                
                # Save to environment for current session
                os.environ[most_specific] = password
                
                # ALSO save to .env file for persistence (in LOCAL_DIR)
                env_file = LOCAL_DIR / f".env.{project}.{env.value}"
                try:
                    with open(env_file, "a") as f:
                        f.write(f"\n{most_specific}={password}\n")
                    print(f"  ✓ {target}: auto-generated and saved to {env_file}")
                except Exception as e:
                    print(f"  ✓ {target}: auto-generated (session only - failed to save to {env_file}: {e})")
                
                result[target] = password
            else:
                missing_secrets.append(f"{project_upper}_{env_upper}_{target_upper}")
                print(f"  ✗ {target}: not found and cannot auto-generate")
        
        if missing_secrets:
            print(f"\nMissing {len(missing_secrets)} secrets")
            print("Most specific patterns that were missing:")
            for missing in missing_secrets:
                print(f"  {missing}")
        
        print(f"\nLoaded {len(result)} secrets successfully")
        return result

    @staticmethod
    def create_secrets_json(project: str, env: Envs, targets: Dict[str, bool] = None) -> str:
        """
        Create secrets.json from environment variables with 4-level inheritance.
        
        Uses both file inheritance (.env → .env.{env} → .env.{project} → .env.{project}.{env})
        and variable inheritance ({TARGET} → {ENV}_{TARGET} → {PROJECT}_{TARGET} → {PROJECT}_{ENV}_{TARGET})
        to resolve secrets and write them to a JSON file.
        
        Args:
            project: Project name (used in file and variable naming)
            env: Environment name (used in file and variable naming)
            targets: Dict mapping secret names to auto-generation flags (defaults to DEFAULT_TARGETS)
                       
        Returns:
            str: The file path to the json file (LOCAL_DIR/{project}_{env}_secrets.json) or None in case of error
            
        Examples:
            ```python
            # Create secrets for ecommerce production (saves to LOCAL_DIR/ecommerce_prod_secrets.json)
            success = SecretsManager.create_secrets_json("ecommerce", "prod")
            
            # Create with custom targets
            custom_targets = {'postgres': True, 'custom_api': False}
            success = SecretsManager.create_secrets_json("ecommerce", "prod", targets=custom_targets)
            ```
            
        Notes:
            - Searches for .env files in current directory and LOCAL_DIR
            - Uses 4-level inheritance for both files and variables
            - Only writes JSON if at least one secret is found
            - Includes helpful logging of found/missing secrets
        """
        if targets is None:
            targets = DEFAULT_TARGETS
            
        env = Envs.to_enum(env)
        output_path = SecretsManager.get_json_path(project, env)

        try:
            secrets_dict = SecretsManager.load_secrets_from_env(project, env, targets)
            
            if not secrets_dict:
                print("No secrets found to write to JSON file")
                return None
            
            with open(output_path, 'w') as f:
                json.dump(secrets_dict, f, indent=2)
            
            print(f"✓ Created secrets file: {output_path}")
            print(f"  Secrets included: {list(secrets_dict.keys())}")
            return output_path
            
        except Exception as e:
            print(f"✗ Error creating secrets file: {e}")
            return None

    @staticmethod
    def get_local_dir() -> str:
        """Return the path to local directory containing the env and JSON secrets files"""
        return LOCAL_DIR
    
    @staticmethod
    def get_json_path(project: str, env: Envs) -> str:
        """Return the path to the JSON secrets file"""
        env = Envs.to_enum(env)
        return LOCAL_DIR / f'{project}_{env.value}_secrets.json' 
    
    @staticmethod
    def get_secrets_dir() -> str:
        """Return the path to directory containing the JSON secrets file in the container"""
        return SECRETS_DIR
        
    @staticmethod
    def get_secrets_file() -> str:
        """Return the path to the JSON secrets file in the container"""
        return f'{SECRETS_DIR}/secrets.json'

    @staticmethod
    def load_secrets(project: str, env: Envs, secret_file: str = None, targets: Dict[str, bool] = None) -> Dict[str, str]:
        """
        Load secrets from JSON file. Raises error if file doesn't exist.
        
        Args:
            project: Project name (used only for error messages)
            env: Environment name (used only for error messages)
            secret_file: Path to JSON secrets file (default: SECRETS_DIR/secrets.json)
            targets: Dict mapping secret names to auto-generation flags (defaults to DEFAULT_TARGETS, used only for validation)
            
        Returns:
            Dict mapping target names (postgres, redis, etc.) to secret values
            
        Raises:
            FileNotFoundError: If secrets directory or file doesn't exist
            OSError: If secrets file cannot be read
            json.JSONDecodeError: If secrets file is not valid JSON
            
        Examples:
            ```python
            # Load secrets from mounted JSON file (production)
            secrets = SecretsManager.load_secrets("ecommerce", "prod")
            db_password = secrets["postgres"]
            
            # Load from custom JSON file
            secrets = SecretsManager.load_secrets("ecommerce", "prod", "/custom/path/secrets.json")
            ```
            
        Notes:
            - Expects secrets to be properly mounted/available
            - Fails fast if secrets are not accessible
            - For development, use load_secrets_from_env() directly
        """
        if targets is None:
            targets = DEFAULT_TARGETS
            
        env = Envs.to_enum(env)
        if secret_file is None:
            secret_file = SecretsManager.get_secrets_file()
            
        # Check if secrets directory exists first
        if not os.path.exists(SECRETS_DIR):
            raise FileNotFoundError(f"Secrets directory {SECRETS_DIR} not found. Ensure secrets are properly mounted in production.")
            
        # Load from JSON file - let exceptions bubble up
        with open(secret_file, 'r') as f:
            secrets_dict = json.load(f)
        
        print(f"✓ Loaded secrets from: {secret_file}")
        return secrets_dict

    @staticmethod
    def get_secret(project_name: str, env: Envs, secret_name: str, targets: Dict[str, bool] = None) -> str:
        """
        Get a specific secret value by name from mounted JSON file. Raises error if not found.
        
        Args:
            project_name: Name of the project (used only for validation)
            env: Environment name (used only for validation) 
            secret_name: Name of the secret target (must be one of the configured targets)
            targets: Dict mapping secret names to auto-generation flags (defaults to DEFAULT_TARGETS)
            
        Returns:
            str: The secret value from the JSON file
            
        Raises:
            ValueError: If the secret name is not in the configured targets
            FileNotFoundError: If secrets directory or file doesn't exist
            KeyError: If the secret name is not found in the JSON file
            
        Examples:
            ```python
            # Get PostgreSQL password from mounted secrets
            postgres_pwd = SecretsManager.get_secret("ecommerce", "prod", "postgres")
            
            # Get Redis password  
            redis_pwd = SecretsManager.get_secret("ecommerce", "dev", "redis")
            
            # Get secret with custom targets
            custom_targets = {'postgres': True, 'custom_api': False}
            api_key = SecretsManager.get_secret("ecommerce", "prod", "custom_api", targets=custom_targets)
            ```
            
        Notes:
            - Expects secrets to be properly mounted in production
            - Fails fast if secrets are not accessible
            - For development, use load_secrets_from_env() directly
        """
        if targets is None:
            targets = DEFAULT_TARGETS
            
        env = Envs.to_enum(env)
        # Validate secret name is in configured targets
        if secret_name not in SecretsManager.get_all_targets(targets):
            available_targets = sorted(SecretsManager.get_all_targets(targets))
            raise ValueError(f"Secret name '{secret_name}' not found in configured targets. Available: {available_targets}")
        
        # Load from JSON file (production mode) - fail fast if not available
        secrets_file = SecretsManager.get_secrets_file()
        
        # Check if secrets directory exists first
        if not os.path.exists(SECRETS_DIR):
            raise FileNotFoundError(f"Secrets directory {SECRETS_DIR} not found. Ensure secrets are properly mounted in production.")
            
        with open(secrets_file, 'r') as f:
            secrets_dict = json.load(f)
        
        if secret_name in secrets_dict:
            print(f"✓ Loaded {secret_name} from: {secrets_file}")
            return secrets_dict[secret_name]
        else:
            raise KeyError(f"Secret '{secret_name}' not found in {secrets_file}. Available secrets: {list(secrets_dict.keys())}")



# Example usage and testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) >= 3:
        project = sys.argv[1]
        env = sys.argv[2]
    else:
        project = "ecommerce"
        env = "prod"
    env = Envs(env)
    
    print(f"=== Testing 4-Level Inheritance for {project}/{env} ===")
    
    # Test with default targets
    print(f"Available targets: {sorted(SecretsManager.get_all_targets())}")
    print()
    
    # Test loading from environment
    secrets_dict = SecretsManager.load_secrets_from_env(project, env)
    
    if secrets_dict:
        print(f"\n=== Successfully Loaded Secrets ===")
        for target, value in secrets_dict.items():
            # Mask the value for security
            masked_value = value[:4] + '*' * (len(value) - 4) if len(value) > 4 else '***'
            print(f"  {target}: {masked_value}")
        
        # Test creating JSON file
        print(f"\n=== Creating secrets.json ===")
        SecretsManager.create_secrets_json(project, env)
        
    else:
        print("No secrets loaded. Check your .env files and environment variables.")
        
        print(f"\n4-Level inheritance patterns to try:")
        for target in sorted(list(SecretsManager.get_all_targets())[:3]):  # Show first 3 as examples
            target_upper = target.upper()
            env_upper = env.value.upper()
            project_upper = project.upper()
            
            print(f"\nFor {target}:")
            print(f"  1. {target_upper} (base)")
            print(f"  2. {env_upper}_{target_upper} (env-specific)")
            print(f"  3. {project_upper}_{target_upper} (project-specific)")
            print(f"  4. {project_upper}_{env_upper}_{target_upper} (most specific)")