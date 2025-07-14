"""
Internationalization (i18n) service for CivicStream.
Provides translation functionality for Spanish and English support.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)


class TranslationService:
    """Service for handling translations across the application."""
    
    def __init__(self):
        self.translations: Dict[str, Dict[str, str]] = {}
        self.default_locale = "en"
        self.supported_locales = ["en", "es"]
        self.load_translations()
    
    def load_translations(self):
        """Load translation files from the translations directory."""
        translations_dir = Path(__file__).parent.parent / "translations"
        
        if not translations_dir.exists():
            logger.warning(f"Translations directory not found: {translations_dir}")
            return
        
        for locale in self.supported_locales:
            locale_file = translations_dir / f"{locale}.json"
            if locale_file.exists():
                try:
                    with open(locale_file, 'r', encoding='utf-8') as f:
                        self.translations[locale] = json.load(f)
                    logger.info(f"Loaded translations for locale: {locale}")
                except json.JSONDecodeError as e:
                    logger.error(f"Error loading translations for {locale}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error loading {locale}: {e}")
            else:
                logger.warning(f"Translation file not found: {locale_file}")
    
    def reload_translations(self):
        """Reload all translation files (useful for development)."""
        self.translations.clear()
        self.load_translations()
    
    @lru_cache(maxsize=1000)
    def t(self, key: str, locale: Optional[str] = None, **kwargs) -> str:
        """
        Translate a key to the specified locale.
        
        Args:
            key: Translation key (e.g., "auth.invalid_credentials")
            locale: Target locale (defaults to default_locale)
            **kwargs: Variables for string formatting
            
        Returns:
            Translated string or original key if translation not found
        """
        locale = self.validate_locale(locale)
        
        # Try to get translation from the specified locale
        translation = self._get_nested_value(self.translations.get(locale, {}), key)
        
        # Fallback to default locale if not found
        if translation is None and locale != self.default_locale:
            translation = self._get_nested_value(
                self.translations.get(self.default_locale, {}), key
            )
        
        # Return key if no translation found
        if translation is None:
            logger.warning(f"Translation not found for key '{key}' in locale '{locale}'")
            translation = key
        
        # Apply string formatting if variables provided
        try:
            return translation.format(**kwargs) if kwargs else translation
        except (KeyError, ValueError) as e:
            logger.error(f"Error formatting translation '{key}': {e}")
            return translation
    
    def _get_nested_value(self, data: Dict[str, Any], key: str) -> Optional[str]:
        """
        Get value from nested dictionary using dot notation.
        
        Args:
            data: Dictionary to search in
            key: Dot-separated key (e.g., "auth.invalid_credentials")
            
        Returns:
            Value if found, None otherwise
        """
        keys = key.split('.')
        current = data
        
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return None
        
        return current if isinstance(current, str) else None
    
    def validate_locale(self, locale: Optional[str]) -> str:
        """
        Validate and normalize locale.
        
        Args:
            locale: Locale to validate
            
        Returns:
            Valid locale string
        """
        if locale is None:
            return self.default_locale
        
        # Handle common locale variations
        locale = locale.lower()
        if locale.startswith('es'):
            return 'es'
        elif locale.startswith('en'):
            return 'en'
        
        return self.default_locale if locale not in self.supported_locales else locale
    
    def get_available_locales(self) -> list[str]:
        """Get list of available locales."""
        return self.supported_locales.copy()
    
    def has_translation(self, key: str, locale: Optional[str] = None) -> bool:
        """Check if a translation exists for the given key and locale."""
        locale = self.validate_locale(locale)
        return self._get_nested_value(self.translations.get(locale, {}), key) is not None


# Global translation service instance
translator = TranslationService()


def t(key: str, locale: Optional[str] = None, **kwargs) -> str:
    """
    Convenience function for translations.
    
    Args:
        key: Translation key
        locale: Target locale
        **kwargs: Variables for string formatting
        
    Returns:
        Translated string
    """
    return translator.t(key, locale, **kwargs)


def get_locale_from_accept_language(accept_language: Optional[str]) -> str:
    """
    Extract preferred locale from Accept-Language header.
    
    Args:
        accept_language: Accept-Language header value
        
    Returns:
        Preferred locale or default locale
    """
    if not accept_language:
        return translator.default_locale
    
    # Convert Header object to string if needed
    accept_language_str = str(accept_language) if accept_language else ""
    if not accept_language_str:
        return translator.default_locale
    
    # Parse Accept-Language header (simplified)
    languages = []
    for lang_with_q in accept_language_str.split(','):
        lang = lang_with_q.split(';')[0].strip().lower()
        languages.append(lang)
    
    # Check for exact matches first
    for lang in languages:
        if lang in translator.supported_locales:
            return lang
    
    # Check for partial matches (e.g., "es-ES" -> "es")
    for lang in languages:
        for supported in translator.supported_locales:
            if lang.startswith(supported):
                return supported
    
    return translator.default_locale