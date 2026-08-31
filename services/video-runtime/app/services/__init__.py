"""
Service factory module providing singleton instances of all services.
This module uses lru_cache to implement the singleton pattern.
"""

from functools import lru_cache

# Remove direct imports of service classes to avoid circular imports


@lru_cache()
def get_auth_service():
    """Returns a singleton instance of AuthService."""
    from app.services.auth_service import AuthService
    return AuthService()
