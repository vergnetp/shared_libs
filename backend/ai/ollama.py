"""
Ollama helper - auto-install models cross-platform.

Recommended models for RAG:
- qwen2.5:3b  (1.9GB) - Best quality/size ratio, multilingual
- llama3.2:3b (2.0GB) - Good English, faster

Usage:
    from ai.ollama import ensure_model, is_available
    
    if is_available():
        await ensure_model("qwen2.5:3b")  # Auto-pulls if missing
"""

import asyncio
import httpx
from typing import Optional, Callable

# Default Ollama endpoint
DEFAULT_BASE_URL = "http://localhost:11434"

# Recommended models (small, fast, good for RAG)
RECOMMENDED_MODELS = {
    "qwen2.5:3b": {
        "size": "1.9GB",
        "languages": "multilingual",
        "description": "Best quality/size ratio",
    },
    "llama3.2:3b": {
        "size": "2.0GB",
        "languages": "English",
        "description": "Fast, good for English",
    },
}

DEFAULT_MODEL = "qwen2.5:3b"


def is_available(base_url: str = None) -> bool:
    """Check if Ollama is running."""
    base_url = base_url or DEFAULT_BASE_URL
    try:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{base_url}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def is_available_async(base_url: str = None) -> bool:
    """Check if Ollama is running (async)."""
    base_url = base_url or DEFAULT_BASE_URL
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{base_url}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def list_models(base_url: str = None) -> list[str]:
    """List installed models."""
    base_url = base_url or DEFAULT_BASE_URL
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{base_url}/api/tags")
            if r.status_code == 200:
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


async def list_models_async(base_url: str = None) -> list[str]:
    """List installed models (async)."""
    base_url = base_url or DEFAULT_BASE_URL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base_url}/api/tags")
            if r.status_code == 200:
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


def has_model(model: str, base_url: str = None) -> bool:
    """Check if a model is installed."""
    models = list_models(base_url)
    # Check exact match or base name match (qwen2.5:3b matches qwen2.5:3b-instruct)
    base_name = model.split(":")[0]
    return any(model == m or m.startswith(f"{base_name}:") for m in models)


async def has_model_async(model: str, base_url: str = None) -> bool:
    """Check if a model is installed (async)."""
    models = await list_models_async(base_url)
    base_name = model.split(":")[0]
    return any(model == m or m.startswith(f"{base_name}:") for m in models)


def pull_model(
    model: str,
    base_url: str = None,
    progress_callback: Callable[[str, float], None] = None,
) -> bool:
    """
    Pull (download) a model.
    
    Args:
        model: Model name (e.g., "qwen2.5:3b")
        base_url: Ollama URL
        progress_callback: Optional callback(status, percent)
        
    Returns:
        True if successful
    """
    base_url = base_url or DEFAULT_BASE_URL
    
    print(f"[ollama] Pulling {model}...")
    if progress_callback:
        progress_callback(f"Starting download of {model}", 0)
    
    try:
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{base_url}/api/pull",
                json={"name": model},
            ) as response:
                if response.status_code != 200:
                    print(f"[ollama] Failed to pull {model}: {response.status_code}")
                    return False
                
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        import json
                        data = json.loads(line)
                        status = data.get("status", "")
                        
                        # Calculate progress
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)
                        if total > 0:
                            pct = (completed / total) * 100
                            if progress_callback:
                                progress_callback(status, pct)
                            print(f"\r[ollama] {status}: {pct:.1f}%", end="", flush=True)
                        else:
                            print(f"\r[ollama] {status}", end="", flush=True)
                            
                    except Exception:
                        pass
                        
        print(f"\n[ollama] Successfully pulled {model}")
        return True
        
    except Exception as e:
        print(f"[ollama] Error pulling {model}: {e}")
        return False


async def pull_model_async(
    model: str,
    base_url: str = None,
    progress_callback: Callable[[str, float], None] = None,
) -> bool:
    """Pull (download) a model (async)."""
    base_url = base_url or DEFAULT_BASE_URL
    
    print(f"[ollama] Pulling {model}...")
    if progress_callback:
        progress_callback(f"Starting download of {model}", 0)
    
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{base_url}/api/pull",
                json={"name": model},
            ) as response:
                if response.status_code != 200:
                    print(f"[ollama] Failed to pull {model}: {response.status_code}")
                    return False
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        import json
                        data = json.loads(line)
                        status = data.get("status", "")
                        
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)
                        if total > 0:
                            pct = (completed / total) * 100
                            if progress_callback:
                                progress_callback(status, pct)
                            print(f"\r[ollama] {status}: {pct:.1f}%", end="", flush=True)
                        else:
                            print(f"\r[ollama] {status}", end="", flush=True)
                            
                    except Exception:
                        pass
                        
        print(f"\n[ollama] Successfully pulled {model}")
        return True
        
    except Exception as e:
        print(f"[ollama] Error pulling {model}: {e}")
        return False


def ensure_model(model: str = None, base_url: str = None) -> bool:
    """
    Ensure a model is available, pulling if needed.
    
    Args:
        model: Model name (default: qwen2.5:3b)
        base_url: Ollama URL
        
    Returns:
        True if model is available
    """
    model = model or DEFAULT_MODEL
    base_url = base_url or DEFAULT_BASE_URL
    
    if not is_available(base_url):
        print("[ollama] Ollama is not running. Please start it first.")
        print("  Install: https://ollama.ai")
        print("  Then run: ollama serve")
        return False
    
    if has_model(model, base_url):
        return True
    
    return pull_model(model, base_url)


async def ensure_model_async(model: str = None, base_url: str = None) -> bool:
    """Ensure a model is available (async)."""
    model = model or DEFAULT_MODEL
    base_url = base_url or DEFAULT_BASE_URL
    
    if not await is_available_async(base_url):
        print("[ollama] Ollama is not running. Please start it first.")
        return False
    
    if await has_model_async(model, base_url):
        return True
    
    return await pull_model_async(model, base_url)


def get_recommended() -> dict:
    """Get recommended models for RAG."""
    return RECOMMENDED_MODELS


# Quick test
if __name__ == "__main__":
    print("Checking Ollama...")
    if is_available():
        print("✓ Ollama is running")
        models = list_models()
        print(f"  Installed: {models}")
        
        print(f"\nEnsuring {DEFAULT_MODEL}...")
        if ensure_model():
            print(f"✓ {DEFAULT_MODEL} is ready")
    else:
        print("✗ Ollama is not running")
        print("  Install from: https://ollama.ai")
