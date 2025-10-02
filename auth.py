"""
Authentication utilities for FastAPI endpoints
"""

from fastapi import HTTPException, Header
from config import AUTH_PASSWORD


def get_current_user(authorization: str = Header(None)):
    """
    FastAPI dependency for Bearer token authentication
    Expects: Authorization: Bearer <token>
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={
                'error': 'Missing Authorization header',
                'message': 'Please provide Bearer token in Authorization header'
            }
        )
    
    # Check if it's a Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(
            status_code=401,
            detail={
                'error': 'Invalid Authorization header format', 
                'message': 'Expected format: Authorization: Bearer <token>'
            }
        )
    
    token = parts[1]
    
    # Validate token
    if token != AUTH_PASSWORD:
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'Invalid authentication token',
                'message': 'The provided token is incorrect'
            }
        )
    
    return True  # Authentication successful
