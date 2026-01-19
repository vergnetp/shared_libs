"""
Ollama helper - auto-install models cross-platform.

Recommended models for RAG:
- qwen2.5:3b  (1.9GB) - Best quality/size ratio, multilingual
- llama3.2:3b (2.0GB) - Good English, faster

Usage:
    from ai.ollama import ensure_model, is_available
    
    if is_available():
        await ensure_model("qwen2.5:3b")  # Auto-pulls if missing

Note: This module wraps cloud.llm.OllamaClient for model management.
      For full LLM functionality (chat, streaming), use cloud.llm directly:
      
    from cloud.llm import AsyncOllamaClient
    
    async with AsyncOllamaClient(model="qwen2.5:3b") as client:
        response = await client.chat([{"role": "user", "content": "Hello!"}])
"""

from typing import Optional, Callable

# Re-export from cloud.llm for backwards compatibility
from ..cloud.llm import (
    OllamaClient,
    AsyncOllamaClient,
    OLLAMA_DEFAULT_MODEL as DEFAULT_MODEL,
    OLLAMA_RECOMMENDED_MODELS as RECOMMENDED_MODELS,
    get_recommended_models,
    get_default_model,
)

# Default Ollama endpoint
DEFAULT_BASE_URL = "http://localhost:11434"


def is_available(base_url: str = None) -> bool:
    """Check if Ollama is running."""
    client = OllamaClient(base_url=base_url or DEFAULT_BASE_URL)
    return client.is_available()


async def is_available_async(base_url: str = None) -> bool:
    """Check if Ollama is running (async)."""
    client = AsyncOllamaClient(base_url=base_url or DEFAULT_BASE_URL)
    return await client.is_available()


def list_models(base_url: str = None) -> list[str]:
    """List installed models."""
    client = OllamaClient(base_url=base_url or DEFAULT_BASE_URL)
    return client.list_models()


async def list_models_async(base_url: str = None) -> list[str]:
    """List installed models (async)."""
    client = AsyncOllamaClient(base_url=base_url or DEFAULT_BASE_URL)
    return await client.list_models()


def has_model(model: str, base_url: str = None) -> bool:
    """Check if a model is installed."""
    client = OllamaClient(model=model, base_url=base_url or DEFAULT_BASE_URL)
    return client.has_model(model)


async def has_model_async(model: str, base_url: str = None) -> bool:
    """Check if a model is installed (async)."""
    client = AsyncOllamaClient(model=model, base_url=base_url or DEFAULT_BASE_URL)
    return await client.has_model(model)


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
    print(f"[ollama] Pulling {model}...")
    if progress_callback:
        progress_callback(f"Starting download of {model}", 0)
    
    try:
        client = OllamaClient(model=model, base_url=base_url or DEFAULT_BASE_URL)
        
        for data in client.pull_model(model):
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
    print(f"[ollama] Pulling {model}...")
    if progress_callback:
        progress_callback(f"Starting download of {model}", 0)
    
    try:
        client = AsyncOllamaClient(model=model, base_url=base_url or DEFAULT_BASE_URL)
        
        async for data in client.pull_model(model):
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
    return get_recommended_models()


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
