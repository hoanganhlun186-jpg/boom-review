"""Threading utilities for AutoRecapPro V2"""
import threading
from typing import Callable, Any
from functools import wraps


class ThreadWorker:
    """Safely execute long-running operations on worker threads"""
    
    @staticmethod
    def run(target: Callable, daemon: bool = True, args=None, kwargs=None) -> threading.Thread:
        """Execute target function in a background thread"""
        args = args or ()
        kwargs = kwargs or {}
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=daemon)
        thread.start()
        return thread
    
    @staticmethod
    def run_safe(target: Callable, on_error: Callable = None):
        """Execute target with error handling"""
        @wraps(target)
        def wrapper(*args, **kwargs):
            try:
                return target(*args, **kwargs)
            except Exception as e:
                print(f"Error in thread: {e}")
                if on_error:
                    on_error(e)
        
        return ThreadWorker.run(wrapper)
    
    @staticmethod
    def run_with_callback(target: Callable, callback: Callable, daemon: bool = True):
        """Execute target and call callback when done"""
        def wrapper():
            result = target()
            if callback:
                callback(result)
        
        return ThreadWorker.run(wrapper, daemon=daemon)
