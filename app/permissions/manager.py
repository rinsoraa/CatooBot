"""Permission model: superuser > admin > user > guest.

v0.1 keeps this deliberately flat: superusers/admins come from config, and a
member with an OneBot group role of owner/admin counts as admin. No ACLs.
"""

from __future__ import annotations

from app.config.settings import PermissionsConfig

ROLE_LEVELS: dict[str, int] = {"guest": 0, "user": 1, "admin": 2, "superuser": 3}

# OneBot sender.role values that imply group admin rights
_GROUP_ADMIN_ROLES = {"admin", "owner"}


class PermissionManager:
    def __init__(self, config: PermissionsConfig) -> None:
        self._superusers = {str(s) for s in config.superusers}
        self._admins = {str(s) for s in config.admins}

    @staticmethod
    def level(role: str) -> int:
        return ROLE_LEVELS.get(role, 0)

    def role(self, user_id: int | str, *, group_role: str | None = None) -> str:
        """Resolve the effective role for a user (optionally in a group context)."""
        uid = str(user_id)
        if uid in self._superusers:
            return "superuser"
        if uid in self._admins:
            return "admin"
        if group_role and group_role.lower() in _GROUP_ADMIN_ROLES:
            return "admin"
        return "user"

    def has(self, user_id: int | str, required: str, *, group_role: str | None = None) -> bool:
        return self.level(self.role(user_id, group_role=group_role)) >= self.level(required)

    def is_superuser(self, user_id: int | str) -> bool:
        return str(user_id) in self._superusers

    def is_admin(self, user_id: int | str, *, group_role: str | None = None) -> bool:
        return self.level(self.role(user_id, group_role=group_role)) >= ROLE_LEVELS["admin"]
