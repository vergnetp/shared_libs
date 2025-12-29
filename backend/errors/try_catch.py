import functools
import asyncio
from .error import TrackError, Error, UserError


def get_defining_class(instance, method_name):
    """
    Find the actual class that defines a method in an inheritance hierarchy.
    """
    if not instance or not hasattr(instance, '__class__'):
        return None
        
    calling_cls = instance.__class__
    
    for cls in calling_cls.__mro__:
        if method_name in cls.__dict__:
            return cls.__name__
    
    return None


def try_catch(func=None, description=None, action=None, critical=False, user_message=None, log_success=False):
    """
    Decorator that logs method calls and wraps exceptions.
    Works with both sync and async functions.
    """
    if func is None:
        return lambda f: try_catch(f, description, action, critical, user_message, log_success)
    
    def _get_context(args):
        if args and hasattr(args[0], '__class__'):
            instance = args[0]
            method_name = func.__name__
            defining_cls = get_defining_class(instance, method_name)
            return f"{defining_cls}.{method_name}", args[1:]
        return func.__name__, args
    
    def _handle_error(current_context, error):
        from .. import log as logger
        should_raise_custom_error = any([description, action, critical, user_message])
        
        if should_raise_custom_error:
            custom_desc = description or f"An error happened in {current_context}: {error}"
            if user_message is not None:
                raise UserError(
                    error=error,
                    description=custom_desc,
                    action=action,
                    critical=critical,
                    context=current_context,
                    user_message=user_message
                )
            else:
                raise Error(
                    error=error,
                    description=custom_desc,
                    action=action,
                    critical=critical,
                    context=current_context
                )
        
        raise TrackError(error, context=current_context)
    
    def _handle_exception(current_context, error):
        if hasattr(error, 'context') and error.context:
            _handle_error(current_context, error)
        elif hasattr(error, 'context') and not error.context:
            error.add_context(current_context)
            raise error
        else:
            _handle_error(current_context, error)

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        from .. import log as logger
        current_context, method_args = _get_context(args)
        
        try:
            result = func(*args, **kwargs)
            args_str = str(method_args)[:200]
            result_str = str(result)[:200]
            if log_success:
                logger.info(f"{current_context}({args_str}) returned {result_str}")
            else:
                logger.debug(f"{current_context}({args_str}) returned {result_str}")
            return result
        except Exception as error:
            _handle_exception(current_context, error)

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        from .. import log as logger
        current_context, method_args = _get_context(args)
        
        try:
            result = await func(*args, **kwargs)
            args_str = str(method_args)[:200]
            result_str = str(result)[:200]
            if log_success:
                logger.info(f"{current_context}({args_str}) returned {result_str}")
            else:
                logger.debug(f"{current_context}({args_str}) returned {result_str}")
            return result
        except Exception as error:
            _handle_exception(current_context, error)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper