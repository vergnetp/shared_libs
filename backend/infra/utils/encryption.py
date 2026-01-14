import os
import hashlib
import base64
import string
import secrets
import json
from typing import Optional, Dict, Any


class Encryption:
    """
    Encrypt/decrypt sensitive data using DO token as key.
    
    Security model:
    - User's DO token is used as encryption key
    - DO token is NEVER stored server-side
    - Only users with the token can decrypt their secrets
    - Operator cannot access secrets without user's DO token
    
    Key derivation:
    - key = SHA256(user_id + do_token) for user-specific encryption
    - key = SHA256(do_token) for workspace-wide encryption
    """
    
    # Marker prefix to identify encrypted values
    ENCRYPTED_PREFIX = "enc:v1:"
    
    @staticmethod
    def derive_key(do_token: str, user_id: Optional[str] = None) -> bytes:
        """
        Derive a Fernet-compatible encryption key from DO token.
        
        Args:
            do_token: DigitalOcean API token
            user_id: Optional user ID for user-specific encryption
            
        Returns:
            32-byte base64-encoded key for Fernet
        """
        if not do_token:
            raise ValueError("DO token required for encryption")
        
        # Combine user_id + do_token for unique per-user keys
        if user_id:
            key_material = f"{user_id}:{do_token}"
        else:
            key_material = do_token
        
        # SHA256 → 32 bytes → base64 for Fernet compatibility
        key_hash = hashlib.sha256(key_material.encode()).digest()
        return base64.urlsafe_b64encode(key_hash)
    
    @staticmethod
    def encrypt(plaintext: str, do_token: str, user_id: Optional[str] = None) -> str:
        """
        Encrypt a string using DO token as key.
        
        Args:
            plaintext: String to encrypt
            do_token: DigitalOcean API token (used as encryption key)
            user_id: Optional user ID for user-specific encryption
            
        Returns:
            Encrypted string with prefix marker
        """
        if not plaintext:
            return plaintext
        
        try:
            from cryptography.fernet import Fernet
            
            key = Encryption.derive_key(do_token, user_id)
            cipher = Fernet(key)
            encrypted = cipher.encrypt(plaintext.encode()).decode()
            
            # Add prefix to identify encrypted values
            return f"{Encryption.ENCRYPTED_PREFIX}{encrypted}"
            
        except ImportError:
            # cryptography not installed - fall back to base64 obfuscation
            # NOT SECURE - just prevents casual viewing
            encoded = base64.b64encode(plaintext.encode()).decode()
            return f"b64:{encoded}"
        except Exception as e:
            raise ValueError(f"Encryption failed: {e}")
    
    @staticmethod
    def decrypt(encrypted: str, do_token: str, user_id: Optional[str] = None) -> str:
        """
        Decrypt a string using DO token as key.
        
        Args:
            encrypted: Encrypted string (with prefix marker)
            do_token: DigitalOcean API token (used as decryption key)
            user_id: Optional user ID (must match what was used for encryption)
            
        Returns:
            Decrypted plaintext
        """
        if not encrypted:
            return encrypted
        
        # Handle Fernet-encrypted values
        if encrypted.startswith(Encryption.ENCRYPTED_PREFIX):
            try:
                from cryptography.fernet import Fernet
                
                ciphertext = encrypted[len(Encryption.ENCRYPTED_PREFIX):]
                key = Encryption.derive_key(do_token, user_id)
                cipher = Fernet(key)
                return cipher.decrypt(ciphertext.encode()).decode()
                
            except ImportError:
                raise ValueError("cryptography package required for decryption")
            except Exception as e:
                raise ValueError(f"Decryption failed (wrong key?): {e}")
        
        # Handle base64 fallback
        if encrypted.startswith("b64:"):
            encoded = encrypted[4:]
            return base64.b64decode(encoded).decode()
        
        # Not encrypted - return as-is
        return encrypted
    
    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Check if a value is encrypted."""
        if not value:
            return False
        return value.startswith(Encryption.ENCRYPTED_PREFIX) or value.startswith("b64:")
    
    # =========================================================================
    # Environment Variables Encryption
    # =========================================================================
    
    @staticmethod
    def encrypt_env_vars(
        env_vars: Dict[str, str],
        do_token: str,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Encrypt environment variables dictionary.
        
        Args:
            env_vars: Dict of env var name → value
            do_token: DO token for encryption
            user_id: Optional user ID
            
        Returns:
            Encrypted JSON string
        """
        if not env_vars:
            return ""
        
        # Convert to JSON and encrypt the whole thing
        json_str = json.dumps(env_vars)
        return Encryption.encrypt(json_str, do_token, user_id)
    
    @staticmethod
    def decrypt_env_vars(
        encrypted_env_vars: str,
        do_token: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Decrypt environment variables.
        
        Args:
            encrypted_env_vars: Encrypted JSON string
            do_token: DO token for decryption
            user_id: Optional user ID (must match encryption)
            
        Returns:
            Dict of env var name → value
        """
        if not encrypted_env_vars:
            return {}
        
        # If not encrypted, try parsing as plain JSON
        if not Encryption.is_encrypted(encrypted_env_vars):
            try:
                return json.loads(encrypted_env_vars)
            except json.JSONDecodeError:
                # Maybe it's KEY=value format
                return Encryption._parse_env_string(encrypted_env_vars)
        
        # Decrypt and parse JSON
        decrypted = Encryption.decrypt(encrypted_env_vars, do_token, user_id)
        return json.loads(decrypted)
    
    @staticmethod
    def _parse_env_string(env_str: str) -> Dict[str, str]:
        """Parse KEY=value format to dict."""
        result = {}
        for line in env_str.strip().split('\n'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result
    
    # =========================================================================
    # Legacy methods (kept for compatibility)
    # =========================================================================
    
    @staticmethod
    def get_key() -> bytes:
        """Legacy: Get key from environment variable."""
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        if not do_token:
            raise ValueError("DIGITALOCEAN_API_TOKEN not set")
        return Encryption.derive_key(do_token)
    
    @staticmethod
    def encode(token: str) -> str:
        """Legacy: Encrypt using env var DO token."""
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        if not do_token:
            return token  # Can't encrypt without key
        return Encryption.encrypt(token, do_token)
    
    # Character set for passwords
    SAFE_CHARS = string.ascii_letters + string.digits
    
    @staticmethod
    def generate_password(length: int = 32, special_chars: bool = False) -> str:
        """Generate cryptographically secure password."""
        if special_chars:
            chars = Encryption.SAFE_CHARS + "!@#$%^&*()-_=+[]{}:,.<>?"
        else:
            chars = Encryption.SAFE_CHARS
        return ''.join(secrets.choice(chars) for _ in range(length))