"""
Role-Based Access Control (RBAC) system.

Implements role hierarchy: admin > creator > user > guest
"""

from __future__ import annotations

from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from fastapi import Depends, HTTPException, status
from fastapi.routing import APIRoute

from app.models.user import User, UserRole

# Type variable for decorated functions
F = TypeVar("F", bound=Callable[..., Any])


class Permission(str, Enum):
    """Fine-grained permissions that can be assigned to roles."""

    # User management
    CREATE_USER = "create_user"
    READ_USER = "read_user"
    UPDATE_USER = "update_user"
    DELETE_USER = "delete_user"

    # Content management
    CREATE_CONTENT = "create_content"
    READ_CONTENT = "read_content"
    UPDATE_CONTENT = "update_content"
    DELETE_CONTENT = "delete_content"
    MODERATE_CONTENT = "moderate_content"

    # Collaboration
    VIEW_COLLABORATION = "view_collaboration"
    READ_COLLABORATION = "view_collaboration"  # Alias for partner apps using read semantics
    CREATE_COLLABORATION = "create_collaboration"
    JOIN_COLLABORATION = "join_collaboration"
    UPDATE_COLLABORATION = "update_collaboration"
    DELETE_COLLABORATION = "delete_collaboration"
    MANAGE_COLLABORATION = "manage_collaboration"

    # Reputation
    VIEW_REPUTATION = "view_reputation"
    MANAGE_REPUTATION = "manage_reputation"

    # Admin
    MANAGE_SYSTEM = "manage_system"
    VIEW_ANALYTICS = "view_analytics"


# Role-based permission mapping
ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.GUEST: {
        Permission.READ_CONTENT,
        Permission.READ_USER,
        Permission.VIEW_COLLABORATION,
    },
    UserRole.USER: {
        Permission.READ_CONTENT,
        Permission.READ_USER,
        Permission.CREATE_CONTENT,
        Permission.UPDATE_CONTENT,  # Own content only
        Permission.DELETE_CONTENT,  # Own content only
        Permission.VIEW_COLLABORATION,
        Permission.CREATE_COLLABORATION,
        Permission.JOIN_COLLABORATION,
        Permission.VIEW_REPUTATION,
    },
    UserRole.CREATOR: {
        Permission.READ_CONTENT,
        Permission.READ_USER,
        Permission.CREATE_CONTENT,
        Permission.UPDATE_CONTENT,
        Permission.DELETE_CONTENT,
        Permission.VIEW_COLLABORATION,
        Permission.CREATE_COLLABORATION,
        Permission.JOIN_COLLABORATION,
        Permission.UPDATE_COLLABORATION,
        Permission.DELETE_COLLABORATION,
        Permission.MANAGE_COLLABORATION,
        Permission.VIEW_REPUTATION,
        Permission.MODERATE_CONTENT,  # Own content
    },
    UserRole.ADMIN: {
        # Admins have all permissions
        *set(Permission),
    },
}


def get_user_permissions(user: User) -> set[Permission]:
    """
    Get all permissions for a user based on their role.

    Args:
        user: The user to get permissions for

    Returns:
        Set of permissions the user has
    """
    return ROLE_PERMISSIONS.get(user.role, set())


def has_permission(user: User, permission: Permission) -> bool:
    """
    Check if a user has a specific permission.

    Args:
        user: The user to check
        permission: The permission to check for

    Returns:
        True if user has the permission, False otherwise
    """
    permissions = get_user_permissions(user)
    return permission in permissions


def require_permission(permission: Permission) -> Callable[[F], F]:
    """
    Decorator to require a specific permission for a route.

    Args:
        permission: The required permission

    Returns:
        Decorated function that checks permissions
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Get current user from kwargs (injected by FastAPI dependency)
            current_user = kwargs.get("current_user")
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not has_permission(current_user, permission):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient permissions",
                )

            return await func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def require_role(required_role: UserRole) -> Callable[[F], F]:
    """
    Decorator to require a minimum role for a route.

    Args:
        required_role: The minimum role required

    Returns:
        Decorated function that checks role hierarchy
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_user = kwargs.get("current_user")
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            # Check role hierarchy (admin > creator > user > guest)
            role_hierarchy = {
                UserRole.GUEST: 0,
                UserRole.USER: 1,
                UserRole.CREATOR: 2,
                UserRole.ADMIN: 3,
            }

            user_level = role_hierarchy.get(current_user.role, 0)
            required_level = role_hierarchy.get(required_role, 0)

            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires {required_role.value} role or higher",
                )

            return await func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


class RoleChecker:
    """FastAPI dependency for role-based access control."""

    def __init__(self, required_roles: list[UserRole]):
        self.required_roles = required_roles

    def __call__(self, current_user: User = Depends("get_current_user")) -> User:
        if current_user.role not in self.required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
