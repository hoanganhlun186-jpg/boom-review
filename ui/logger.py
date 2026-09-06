"""Logging utilities for AutoRecapPro V2"""
import logging
from datetime import datetime
from typing import Optional, Callable


class Logger:
    """Centralized logging with callback support for UI updates"""
    
    _instance: Optional['Logger'] = None
    _callbacks: list = []
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_logging()
        return cls._instance
    
    def _init_logging(self):
        """Initialize Python logging"""
        self.logger = logging.getLogger('AutoRecapPro')
        self.logger.setLevel(logging.DEBUG)
        
        if not self.logger.handlers:
            handler = logging.FileHandler(f'auto_recap_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def register_callback(self, callback: Callable[[str], None]):
        """Register a callback for log messages (e.g., UI text widget)"""
        self._callbacks.append(callback)
    
    def _notify_callbacks(self, message: str):
        """Notify all registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(message)
            except Exception as e:
                print(f"Callback error: {e}")
    
    def info(self, message: str):
        """Log info message"""
        self.logger.info(message)
        self._notify_callbacks(f"ℹ️  {message}\n")
    
    def success(self, message: str):
        """Log success message"""
        self.logger.info(message)
        self._notify_callbacks(f"✅ {message}\n")
    
    def warning(self, message: str):
        """Log warning message"""
        self.logger.warning(message)
        self._notify_callbacks(f"⚠️  {message}\n")
    
    def error(self, message: str):
        """Log error message"""
        self.logger.error(message)
        self._notify_callbacks(f"❌ {message}\n")
    
    def debug(self, message: str):
        """Log debug message"""
        self.logger.debug(message)
    
    def step(self, step_num: int, message: str):
        """Log workflow step"""
        msg = f"📍 BƯỚC {step_num}: {message}"
        self.info(msg)
    
    def progress(self, message: str, percentage: int):
        """Log progress message"""
        msg = f"⏳ {message} ({percentage}%)"
        self.logger.info(msg)
        self._notify_callbacks(f"{msg}\n")
