__all__ = ["is_staff", "is_admin_user"]


async def is_staff(user_id: int) -> bool:
    from .config_manager import load_config
    cfg = await load_config()
    return user_id == cfg.settings.super_admin_id or user_id in cfg.settings.manager_ids


async def is_admin_user(user_id: int, member_roles: list[int] = None) -> bool:
    from .config_manager import load_config
    cfg = await load_config()
    if user_id == cfg.settings.super_admin_id:
        return True
    if user_id in cfg.settings.manager_ids:
        return True
    if member_roles:
        for rid in member_roles:
            if rid in cfg.settings.admin_role_ids:
                return True
    return False
