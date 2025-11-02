"""
Authentication middleware for FastAPI endpoints

Uses Bearer token authentication with API key validation.
"""

from fastapi import Header, HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .settings import settings


# HTTP Bearer token scheme
security = HTTPBearer()


async def require_auth(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Verify Bearer token authentication.
    
    This is called as a dependency on protected endpoints.
    
    Args:
        credentials: HTTP Bearer token from Authorization header
    
    Raises:
        HTTPException: If token is missing or invalid
    """
    token = credentials.credentials
    
    if not token or token != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing authentication token"
        )
    
    return token
