"""
Theme plugin loader for MuniStream platform.
Extends the plugin system to support loading themes from tenant repositories.
"""

import os
import sys
import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import (
    ThemeConfig,
    ThemeColors,
    ThemeTypography,
    ThemeSpacing,
    ThemeBorders,
    ThemeShadows,
    ThemeComponents,
    ThemeAssets,
    ThemeMetadata,
    Theme
)


class ThemePlugin:
    """Represents a theme plugin from a tenant repository"""

    def __init__(self, name: str, config: Dict[str, Any], base_path: str):
        self.name = name
        self.config = config
        self.base_path = Path(base_path)
        theme_path_str = config.get("path", "themes/default")
        self.theme_path = self.base_path / theme_path_str
        self.enabled = config.get("enabled", True)

    def load_theme(self) -> Optional[Theme]:
        """Load theme configuration from files"""
        if not self.enabled:
            return None

        print(f"🔍 Loading theme from: {self.theme_path}")
        print(f"   Base path: {self.base_path}")
        print(f"   Theme path exists: {self.theme_path.exists()}")
        print(f"   Theme path is directory: {self.theme_path.is_dir()}")

        if not self.theme_path.exists():
            print(f"⚠️ Theme path does not exist: {self.theme_path}")
            return None

        if not self.theme_path.is_dir():
            print(f"⚠️ Theme path is not a directory: {self.theme_path}")
            return None

        try:
            # Load main theme configuration
            theme_config = self._load_theme_config()
            if not theme_config:
                return None

            # Create theme instance
            theme = Theme(
                id=self.name,
                config=theme_config,
                is_active=self.enabled,
                is_default=self.config.get("is_default", False),
                plugin_name=self.name,
                loaded_at=datetime.utcnow().isoformat()
            )

            print(f"✅ Loaded theme: {theme.id}")
            return theme

        except Exception as e:
            print(f"❌ Error loading theme {self.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def _load_theme_config(self) -> Optional[ThemeConfig]:
        """Load and merge theme configuration from multiple files"""
        config_data = {}

        # Load main theme.yaml or theme.json
        main_config = self._load_file("theme")
        if main_config:
            config_data.update(main_config)

        # Load individual configuration files
        colors = self._load_file("colors")
        if colors:
            config_data["colors"] = colors

        typography = self._load_file("typography")
        if typography:
            config_data["typography"] = typography

        components = self._load_file("components")
        if components:
            config_data["components"] = components

        spacing = self._load_file("spacing")
        if spacing:
            config_data["spacing"] = spacing

        borders = self._load_file("borders")
        if borders:
            config_data["borders"] = borders

        shadows = self._load_file("shadows")
        if shadows:
            config_data["shadows"] = shadows

        assets = self._load_file("assets")
        if assets:
            config_data["assets"] = assets

        # Handle assets paths
        if "assets" in config_data:
            config_data["assets"] = self._resolve_asset_paths(config_data["assets"])

        # Ensure metadata exists
        if "metadata" not in config_data:
            config_data["metadata"] = {
                "name": self.name,
                "version": "1.0.0"
            }

        # Ensure colors exist
        if "colors" not in config_data:
            # Use default colors or from config
            config_data["colors"] = {
                "primary_main": self.config.get("config", {}).get("primary_color", "#1976d2")
            }

        try:
            return ThemeConfig(**config_data)
        except Exception as e:
            print(f"❌ Error creating ThemeConfig: {str(e)}")
            return None

    def _load_file(self, filename: str) -> Optional[Dict[str, Any]]:
        """Load configuration from YAML or JSON file"""
        # Try YAML first
        yaml_path = self.theme_path / f"{filename}.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            except Exception as e:
                print(f"⚠️ Error loading {yaml_path}: {str(e)}")

        # Try YML
        yml_path = self.theme_path / f"{filename}.yml"
        if yml_path.exists():
            try:
                with open(yml_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            except Exception as e:
                print(f"⚠️ Error loading {yml_path}: {str(e)}")

        # Try JSON
        json_path = self.theme_path / f"{filename}.json"
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Error loading {json_path}: {str(e)}")

        return None

    def _resolve_asset_paths(self, assets: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve asset paths relative to theme directory"""
        resolved = {}
        assets_dir = self.theme_path / "assets"

        for key, value in assets.items():
            if isinstance(value, str):
                # Check if it's a relative path
                if not value.startswith(("http://", "https://", "/")):
                    asset_path = assets_dir / value
                    if asset_path.exists():
                        # Store as relative path for serving
                        resolved[key] = f"/themes/{self.name}/assets/{value}"
                    else:
                        resolved[key] = value
                else:
                    resolved[key] = value
            else:
                resolved[key] = value

        return resolved

    def get_asset(self, asset_path: str) -> Optional[bytes]:
        """Get asset file content"""
        try:
            full_path = self.theme_path / "assets" / asset_path
            if full_path.exists() and full_path.is_file():
                with open(full_path, 'rb') as f:
                    return f.read()
        except Exception as e:
            print(f"⚠️ Error reading asset {asset_path}: {str(e)}")
        return None


class ThemePluginManager:
    """Manages theme plugins for all tenants"""

    def __init__(self):
        self.themes: Dict[str, Theme] = {}
        self.theme_plugins: Dict[str, ThemePlugin] = {}
        self.default_theme: Optional[Theme] = None

    def load_plugin_themes(self, plugins_config: Dict[str, Any], base_path: str) -> int:
        """Load themes from plugin configuration"""
        theme_configs = plugins_config.get("themes", [])
        loaded_count = 0

        print(f"📦 Loading themes from plugin")
        print(f"   Base path: {base_path}")
        print(f"   Theme configs: {len(theme_configs)}")

        for theme_config in theme_configs:
            if not theme_config.get("enabled", True):
                continue

            plugin = ThemePlugin(
                name=theme_config.get("name", "default"),
                config=theme_config,
                base_path=base_path
            )

            theme = plugin.load_theme()
            if theme:
                self.themes[theme.id] = theme
                self.theme_plugins[theme.id] = plugin

                # Set as default if specified
                if theme.is_default or len(theme_configs) == 1:
                    self.set_default_theme(theme)

                loaded_count += 1
                print(f"   ✅ Stored theme: {theme.id}")

        return loaded_count

    def get_theme(self, theme_id: Optional[str] = None) -> Optional[Theme]:
        """Get theme by ID or default"""
        if theme_id and theme_id in self.themes:
            return self.themes[theme_id]

        # Return default theme
        return self.default_theme

    def set_default_theme(self, theme: Theme):
        """Set default theme"""
        # Reset other themes
        for t in self.themes.values():
            t.is_default = False

        theme.is_default = True
        self.default_theme = theme

    def get_asset(self, theme_id: str, asset_path: str) -> Optional[bytes]:
        """Get asset from a theme"""
        if theme_id in self.theme_plugins:
            return self.theme_plugins[theme_id].get_asset(asset_path)
        return None

    def list_themes(self) -> List[Theme]:
        """List all themes"""
        return list(self.themes.values())

    def create_default_theme(self) -> Theme:
        """Create a default theme if none exists"""
        theme_config = ThemeConfig(
            metadata=ThemeMetadata(
                name="Default Theme",
                description="Default theme",
                version="1.0.0"
            ),
            colors=ThemeColors(
                primary_main="#1976d2",
                secondary_main="#dc004e",
                background_default="#ffffff",
                background_paper="#f5f5f5",
                text_primary="#000000",
                text_secondary="#666666"
            )
        )

        theme = Theme(
            id="default",
            config=theme_config,
            is_active=True,
            is_default=True,
            loaded_at=datetime.utcnow().isoformat()
        )

        self.themes[theme.id] = theme
        self.default_theme = theme

        return theme


# Global theme manager instance
theme_manager = ThemePluginManager()