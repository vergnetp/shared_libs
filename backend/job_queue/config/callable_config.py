import importlib
import inspect
from typing import Any, Dict, Optional, Callable, Union


class QueueCallableConfig:
    """
    Configuration and registry for callable functions.
    
    Manages registration, lookup, and execution of job handler functions
    with support for both sync and async callables.
    """
    def __init__(
        self,
        logger: Optional[Any] = None,
        allow_dynamic_import: bool = True
    ):
        """
        Initialize callable configuration.
        
        Args:
            logger: Logger instance for logging
            allow_dynamic_import: Whether to allow dynamic imports for callable paths
        """
        self._registry: Dict[str, Callable] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._logger = logger
        self._allow_dynamic_import = allow_dynamic_import
    
    @property
    def allow_dynamic_import(self) -> bool:
        return self._allow_dynamic_import
    
    def register(
        self,
        callable_or_name: Union[Callable, str],
        callable_fn: Optional[Callable] = None,
        name: Optional[str] = None,
        **metadata
    ):
        """
        Register a callable function.
        
        Can be used as:
            config.register(my_function)
            config.register("my_name", my_function)
            config.register(my_function, name="custom_name")
        
        Args:
            callable_or_name: Either the callable or a name string
            callable_fn: The callable (if first arg is name)
            name: Optional name override
            **metadata: Additional metadata to store
        """
        # Handle different calling conventions
        if callable(callable_or_name):
            fn = callable_or_name
            fn_name = name or fn.__name__
        else:
            fn_name = callable_or_name
            fn = callable_fn
            if fn is None:
                raise ValueError(f"Callable required when name is provided: {fn_name}")
        
        # Store in registry
        self._registry[fn_name] = fn
        self._metadata[fn_name] = {
            "is_async": inspect.iscoroutinefunction(fn),
            "module": fn.__module__,
            "qualname": fn.__qualname__,
            **metadata
        }
        
        if self._logger:
            self._logger.debug(f"Registered callable: {fn_name}")
    
    def unregister(self, name: str) -> bool:
        """
        Unregister a callable.
        
        Args:
            name: Name of the callable to remove
            
        Returns:
            True if found and removed, False otherwise
        """
        if name in self._registry:
            del self._registry[name]
            del self._metadata[name]
            return True
        return False
    
    def get(self, name: str) -> Optional[Callable]:
        """
        Get a registered callable by name.
        
        Args:
            name: Name of the callable
            
        Returns:
            The callable or None if not found
        """
        # Check registry first
        if name in self._registry:
            return self._registry[name]
        
        # Try dynamic import if allowed
        if self._allow_dynamic_import and "." in name:
            return self._import_callable(name)
        
        return None
    
    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a registered callable."""
        return self._metadata.get(name)
    
    def is_async(self, name: str) -> bool:
        """Check if a callable is async."""
        meta = self._metadata.get(name)
        if meta:
            return meta.get("is_async", False)
        
        fn = self.get(name)
        if fn:
            return inspect.iscoroutinefunction(fn)
        return False
    
    def list_callables(self) -> list:
        """List all registered callable names."""
        return list(self._registry.keys())
    
    def _import_callable(self, path: str) -> Optional[Callable]:
        """
        Dynamically import a callable from a module path.
        
        Args:
            path: Full path like "module.submodule.function"
            
        Returns:
            The callable or None if import fails
        """
        try:
            module_path, attr_name = path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            fn = getattr(module, attr_name)
            
            # Cache it for future lookups
            self._registry[path] = fn
            self._metadata[path] = {
                "is_async": inspect.iscoroutinefunction(fn),
                "module": module_path,
                "qualname": attr_name,
                "imported": True
            }
            
            return fn
        except (ImportError, AttributeError) as e:
            if self._logger:
                self._logger.warning(f"Failed to import callable {path}: {e}")
            return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "registered_callables": list(self._registry.keys()),
            "allow_dynamic_import": self._allow_dynamic_import,
            "metadata": self._metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueueCallableConfig':
        """Create instance from dictionary."""
        return cls(
            allow_dynamic_import=data.get('allow_dynamic_import', True)
        )
