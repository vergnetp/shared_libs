from cryptography.fernet import Fernet
import os
import hashlib
import base64
import string
import secrets

class Encryption:
    """Encrypt/decrypt Git tokens and other sensitive data using DO token as key"""
    
    @staticmethod
    def get_key() -> bytes:
        """
        Derive encryption key from DigitalOcean API token.
        
        Uses DIGITALOCEAN_API_TOKEN as the base for encryption key.
        This way, each user's tokens are encrypted with their own unique key.
        
        Returns:
            Encryption key as bytes (Fernet-compatible)
            
        Raises:
            ValueError: If DIGITALOCEAN_API_TOKEN not set
        """
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        if not do_token:
            raise ValueError(
                "DIGITALOCEAN_API_TOKEN environment variable not set. "
                "This is required for both deployment and token encryption."
            )
        
        # Derive a Fernet-compatible key from DO token
        # Use SHA256 hash and encode as base64 (Fernet requires 32-byte base64 key)
        key_hash = hashlib.sha256(do_token.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_hash)
        
        return fernet_key
    
    @staticmethod
    def encode(token: str) -> str:
        """
        Encrypt a token using DO token as encryption key.
        
        Args:
            token: Plain text token to encrypt (e.g., GitHub PAT)
            
        Returns:
            Encrypted token as string
            
        Example:
            encrypted = Encryption.encode("ghp_xxxxxxxxxxxx")
        """
        if not token:
            return token
        
        try:
            cipher = Fernet(Encryption.get_key())
            return cipher.encrypt(token.encode()).decode()
        except Exception as e:
            raise ValueError(f"Failed to encrypt token: {e}")
    
    @staticmethod
    def decrypt(encrypted_token: str) -> str:
        """
        Decrypt a token using DO token as encryption key.
        
        Args:
            encrypted_token: Encrypted token string
            
        Returns:
            Decrypted token as plain text
            
        Example:
            plain = Encryption.decrypt(encrypted)
        """
        if not encrypted_token:
            return encrypted_token
        
        try:
            cipher = Fernet(Encryption.get_key())
            return cipher.decrypt(encrypted_token.encode()).decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt token: {e}")
    
    @staticmethod
    def is_encrypted(token: str) -> bool:
        """
        Check if a token appears to be encrypted.
        
        Fernet tokens are always base64 and start with 'gAAAAA'
        
        Args:
            token: Token to check
            
        Returns:
            True if token appears encrypted, False otherwise
            
        Example:
            if Encryption.is_encrypted(token):
                token = Encryption.decrypt(token)
        """
        if not token:
            return False
        
        # Fernet tokens always start with this pattern
        return token.startswith('gAAAAA')
    

    # Character set for passwords (alphanumeric only for compatibility)
    SAFE_CHARS = string.ascii_letters + string.digits
    
    @staticmethod
    def generate_password(length: int = 32, special_chars: bool = False) -> str:
        """
        Generate cryptographically secure password.
        
        Args:
            length: Password length (default: 32)
            special_chars: Include special characters (default: False for compatibility)
            
        Returns:
            Secure random password
        """
        if special_chars:
            chars = Encryption.SAFE_CHARS + "!@#$%^&*()-_=+[]{}:,.<>?"
        else:
            chars = Encryption.SAFE_CHARS
        
        return ''.join(secrets.choice(chars) for _ in range(length))