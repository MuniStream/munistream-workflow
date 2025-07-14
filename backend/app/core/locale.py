"""
Locale detection and handling for CivicStream.
Provides utilities for extracting user locale from requests.
"""

from typing import Optional
from fastapi import Request, Header, Depends
from .i18n import get_locale_from_accept_language, translator


def get_locale_from_request(
    request: Request,
    accept_language: Optional[str] = Header(None, alias="Accept-Language")
) -> str:
    """
    Extract user locale from request with the following priority:
    1. Query parameter ?locale=es or ?lang=es
    2. User preference from database (if authenticated)
    3. Accept-Language header
    4. Default locale (en)
    
    Args:
        request: FastAPI request object
        accept_language: Accept-Language header
        
    Returns:
        Validated locale string
    """
    # 1. Check query parameters
    locale_param = (
        request.query_params.get("locale") or 
        request.query_params.get("lang")
    )
    if locale_param:
        validated_locale = translator.validate_locale(locale_param)
        if validated_locale in translator.supported_locales:
            return validated_locale
    
    # 2. TODO: Check user preference from database
    # This would require authentication and user model updates
    # user_locale = get_user_preferred_locale(request)
    # if user_locale:
    #     return translator.validate_locale(user_locale)
    
    # 3. Check Accept-Language header
    if accept_language:
        return get_locale_from_accept_language(accept_language)
    
    # 4. Return default locale
    return translator.default_locale


def get_locale_dependency():
    """
    FastAPI dependency for getting locale from request.
    Use this in API endpoints that need localization.
    
    Usage:
        @app.get("/api/example")
        async def example(locale: str = Depends(get_locale_dependency)):
            return {"message": t("example.message", locale)}
    """
    return Depends(get_locale_from_request)


class LocaleContext:
    """Context manager for setting locale in a scope."""
    
    def __init__(self, locale: str):
        self.locale = translator.validate_locale(locale)
        self.original_locale = None
    
    def __enter__(self):
        # Store original default if we want to temporarily change it
        self.original_locale = translator.default_locale
        return self.locale
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Could restore original locale if needed
        pass