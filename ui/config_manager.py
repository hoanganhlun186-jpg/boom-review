"""Configuration management for AutoRecapPro V2"""
import json
import os
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """Handles all configuration loading and saving"""
    
    @staticmethod
    def get_default_config_path() -> str:
        base_dir = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.path.join(Path.home(), "AppData", "Local")
        )
        return os.path.join(base_dir, "AutoRecapPro_V2", "config.json")

    def __init__(self, config_path: str = None):
        self.config_path = config_path or self.get_default_config_path()
        self._cache = None
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from file"""
        if self._cache is not None:
            return self._cache
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8-sig') as f:
                    self._cache = json.load(f)
                    return self._cache
            except Exception as e:
                print(f"Error loading config: {e}")
        
        self._cache = {}
        return self._cache
    
    def save(self, data: Dict[str, Any]) -> bool:
        """Save configuration to file"""
        try:
            cfg = self.load()
            cfg.update(data)
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._cache = cfg
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value"""
        cfg = self.load()
        return cfg.get(key, default)
    
    def set(self, key: str, value: Any) -> bool:
        """Set a config value"""
        return self.save({key: value})
    
    def clear_cache(self):
        """Clear the cache to force reload"""
        self._cache = None
