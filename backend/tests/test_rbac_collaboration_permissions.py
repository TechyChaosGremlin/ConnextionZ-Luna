from app.models.user import User, UserRole
from features.auth.rbac import Permission, ROLE_PERMISSIONS, has_permission


def _make_user(role: UserRole) -> User:
    return User(
        id="user-1",
        email="user@example.com",
        username="user",
        hashed_password="hashed",
        role=role,
    )


def test_collaboration_permissions_are_defined_for_user_roles():
    user = _make_user(UserRole.USER)
    creator = _make_user(UserRole.CREATOR)
    admin = _make_user(UserRole.ADMIN)

    assert Permission.VIEW_COLLABORATION in ROLE_PERMISSIONS[UserRole.USER]
    assert Permission.CREATE_COLLABORATION in ROLE_PERMISSIONS[UserRole.USER]
    assert Permission.JOIN_COLLABORATION in ROLE_PERMISSIONS[UserRole.USER]

    assert Permission.MANAGE_COLLABORATION in ROLE_PERMISSIONS[UserRole.CREATOR]
    assert Permission.UPDATE_COLLABORATION in ROLE_PERMISSIONS[UserRole.CREATOR]
    assert Permission.DELETE_COLLABORATION in ROLE_PERMISSIONS[UserRole.CREATOR]

    assert has_permission(user, Permission.VIEW_COLLABORATION)
    assert has_permission(creator, Permission.MANAGE_COLLABORATION)
    assert has_permission(admin, Permission.DELETE_COLLABORATION)
