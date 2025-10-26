"""
Theme configuration models for MuniStream platform.
"""

from typing import Dict, Optional, List, Any
from pydantic import BaseModel, Field, HttpUrl


class ThemeColors(BaseModel):
    """Color palette configuration for themes"""

    # Primary colors
    primary_main: str = Field(..., description="Primary color")
    primary_light: Optional[str] = Field(None, description="Primary light variant")
    primary_dark: Optional[str] = Field(None, description="Primary dark variant")
    primary_contrast_text: Optional[str] = Field("#ffffff", description="Text color on primary")

    # Secondary colors
    secondary_main: Optional[str] = Field(None, description="Secondary color")
    secondary_light: Optional[str] = Field(None, description="Secondary light variant")
    secondary_dark: Optional[str] = Field(None, description="Secondary dark variant")
    secondary_contrast_text: Optional[str] = Field("#ffffff", description="Text color on secondary")

    # Background colors
    background_default: Optional[str] = Field("#ffffff", description="Default background")
    background_paper: Optional[str] = Field("#f5f5f5", description="Paper background")

    # Text colors
    text_primary: Optional[str] = Field("#000000", description="Primary text color")
    text_secondary: Optional[str] = Field("#666666", description="Secondary text color")
    text_disabled: Optional[str] = Field("#999999", description="Disabled text color")

    # Status colors
    error: Optional[str] = Field("#f44336", description="Error color")
    warning: Optional[str] = Field("#ff9800", description="Warning color")
    info: Optional[str] = Field("#2196f3", description="Info color")
    success: Optional[str] = Field("#4caf50", description="Success color")

    # Additional custom colors
    custom: Optional[Dict[str, str]] = Field(default_factory=dict, description="Custom colors")


class ThemeTypography(BaseModel):
    """Typography configuration for themes"""

    font_family: Optional[str] = Field(
        '"Roboto", "Helvetica", "Arial", sans-serif',
        description="Default font family"
    )
    font_family_headings: Optional[str] = Field(None, description="Headings font family")

    # Font sizes
    font_size_base: Optional[int] = Field(14, description="Base font size in px")
    font_size_small: Optional[int] = Field(12, description="Small font size in px")
    font_size_large: Optional[int] = Field(16, description="Large font size in px")

    # Font weights
    font_weight_light: Optional[int] = Field(300, description="Light font weight")
    font_weight_regular: Optional[int] = Field(400, description="Regular font weight")
    font_weight_medium: Optional[int] = Field(500, description="Medium font weight")
    font_weight_bold: Optional[int] = Field(700, description="Bold font weight")

    # Line heights
    line_height_base: Optional[float] = Field(1.5, description="Base line height")
    line_height_heading: Optional[float] = Field(1.2, description="Heading line height")

    # Custom typography variants
    custom: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Custom typography")


class ThemeSpacing(BaseModel):
    """Spacing configuration for themes"""

    unit: Optional[int] = Field(8, description="Base spacing unit in px")
    xs: Optional[int] = Field(4, description="Extra small spacing")
    sm: Optional[int] = Field(8, description="Small spacing")
    md: Optional[int] = Field(16, description="Medium spacing")
    lg: Optional[int] = Field(24, description="Large spacing")
    xl: Optional[int] = Field(32, description="Extra large spacing")


class ThemeBorders(BaseModel):
    """Border configuration for themes"""

    radius_sm: Optional[int] = Field(4, description="Small border radius")
    radius_md: Optional[int] = Field(8, description="Medium border radius")
    radius_lg: Optional[int] = Field(12, description="Large border radius")
    width: Optional[int] = Field(1, description="Default border width")
    color: Optional[str] = Field("#e0e0e0", description="Default border color")


class ThemeShadows(BaseModel):
    """Shadow configuration for themes"""

    sm: Optional[str] = Field(
        "0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24)",
        description="Small shadow"
    )
    md: Optional[str] = Field(
        "0 3px 6px rgba(0,0,0,0.16), 0 3px 6px rgba(0,0,0,0.23)",
        description="Medium shadow"
    )
    lg: Optional[str] = Field(
        "0 10px 20px rgba(0,0,0,0.19), 0 6px 6px rgba(0,0,0,0.23)",
        description="Large shadow"
    )


class ThemeComponents(BaseModel):
    """Component-specific style overrides"""

    button: Optional[Dict[str, Any]] = Field(default_factory=dict)
    card: Optional[Dict[str, Any]] = Field(default_factory=dict)
    input: Optional[Dict[str, Any]] = Field(default_factory=dict)
    navbar: Optional[Dict[str, Any]] = Field(default_factory=dict)
    sidebar: Optional[Dict[str, Any]] = Field(default_factory=dict)
    table: Optional[Dict[str, Any]] = Field(default_factory=dict)
    dialog: Optional[Dict[str, Any]] = Field(default_factory=dict)
    custom: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ThemeAssets(BaseModel):
    """Theme assets configuration"""

    logo: Optional[str] = Field(None, description="Logo path or URL")
    logo_dark: Optional[str] = Field(None, description="Dark mode logo")
    favicon: Optional[str] = Field(None, description="Favicon path")
    background_image: Optional[str] = Field(None, description="Background image")
    loading_animation: Optional[str] = Field(None, description="Loading animation")
    custom_assets: Optional[Dict[str, str]] = Field(default_factory=dict)


class ThemeMetadata(BaseModel):
    """Theme metadata information"""

    name: str = Field(..., description="Theme name")
    version: str = Field("1.0.0", description="Theme version")
    description: Optional[str] = Field(None, description="Theme description")
    author: Optional[str] = Field(None, description="Theme author")
    organization: Optional[str] = Field(None, description="Organization name")
    tenant_id: Optional[str] = Field(None, description="Associated tenant ID")
    created_at: Optional[str] = Field(None, description="Creation date")
    updated_at: Optional[str] = Field(None, description="Last update date")


class ThemeConfig(BaseModel):
    """Complete theme configuration"""

    metadata: ThemeMetadata
    colors: ThemeColors
    typography: Optional[ThemeTypography] = Field(default_factory=ThemeTypography)
    spacing: Optional[ThemeSpacing] = Field(default_factory=ThemeSpacing)
    borders: Optional[ThemeBorders] = Field(default_factory=ThemeBorders)
    shadows: Optional[ThemeShadows] = Field(default_factory=ThemeShadows)
    components: Optional[ThemeComponents] = Field(default_factory=ThemeComponents)
    assets: Optional[ThemeAssets] = Field(default_factory=ThemeAssets)

    # Additional customization
    custom_css: Optional[str] = Field(None, description="Custom CSS rules")
    custom_variables: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Theme mode support
    dark_mode: Optional[bool] = Field(False, description="Support dark mode")
    dark_colors: Optional[ThemeColors] = Field(None, description="Dark mode colors")


class Theme(BaseModel):
    """Theme instance with runtime information"""

    id: str = Field(..., description="Theme unique identifier")
    config: ThemeConfig = Field(..., description="Theme configuration")
    is_active: bool = Field(True, description="Whether theme is active")
    is_default: bool = Field(False, description="Whether this is the default theme")
    tenant_id: Optional[str] = Field(None, description="Associated tenant")
    plugin_name: Optional[str] = Field(None, description="Source plugin name")
    loaded_at: Optional[str] = Field(None, description="Loading timestamp")