"""Server-owned feature permissions; ownership and administrator-only rules still apply."""

from uuid import UUID

from app.errors import fail

PERMISSIONS = {
    "evaluation.read": "查看本人Agent评测",
    "evaluation.manage": "管理本人Agent评测集",
    "evaluation.run": "执行本人Agent评测",
    "monitoring.read": "查看系统匿名监测汇总",
    "monitoring.manage": "配置系统监测告警",
    "repositories.read": "查看本人仓库与差异",
    "repositories.manage": "配置本人仓库",
    "repositories.use": "在任务中检出仓库",
    "repositories.publish": "批准本人仓库分支和PR",
    "chat.read": "查看对话",
    "chat.manage": "管理对话与附件",
    "chat.run": "发起对话与联网",
    "work.read": "查看任务",
    "work.manage": "管理任务与文件",
    "work.execute": "执行任务",
    "agents.read": "查看智能体",
    "agents.manage": "配置和发布智能体",
    "knowledge.read": "查看知识库",
    "knowledge.manage": "管理知识与索引",
    "knowledge.search": "检索知识",
    "skills.read": "读取技能",
    "skills.manage": "导入和管理技能",
    "tools.read": "查看工具",
    "tools.use": "调用工具",
    "workflows.read": "查看工作流",
    "workflows.manage": "编辑和发布工作流",
    "workflows.execute": "执行工作流及 API",
    "memory.read": "读取长期记忆",
    "memory.manage": "管理长期记忆",
    "media.read": "查看媒体",
    "media.use": "运行多模态模型",
    "organizations.read": "查看组织",
    "organizations.manage": "管理组织与共享",
    "operations.read": "查看本人用量和审计",
    "automation.read": "查看主动任务与批次",
    "automation.manage": "配置主动任务与批次",
    "automation.execute": "执行主动任务与批次",
    "notifications.read": "读取本人通知",
    "notifications.manage": "配置通知通道与投递",
}


def route_permission(path: str, method: str) -> str | None:
    """Map authenticated product routes; admin-only and private-resource checks are additional."""
    path = path.removeprefix("/api/v1/")
    reading = method in {"GET", "HEAD", "OPTIONS"}
    if path.startswith(("auth/", "admin/", "internal/")):
        return None
    if path.startswith("agent-evaluations"):
        return "evaluation.read" if reading else "evaluation.manage"
    if path.startswith("operations/monitoring"):
        return None if "/alerts/" in path else "monitoring.read"
    if path.startswith("tool-oauth/"):
        return "tools.use"
    if path.startswith("repositories"):
        if path == "repositories/options":
            return "repositories.use"
        return "repositories.read" if reading else "repositories.manage"
    if path.startswith("repository-actions"):
        return "repositories.read" if reading else "repositories.publish"
    if path.startswith("work-runs/") and "/repository" in path:
        if path.endswith("/repository/evidence"):
            return "repositories.read"
        return "repositories.read" if reading else "repositories.publish"
    if path.startswith(("automations", "batches")):
        return "automation.read" if reading else "automation.manage"
    if path.startswith("notification-channels"):
        return "notifications.read" if reading else "notifications.manage"
    if path.startswith("notifications"):
        return "notifications.manage" if path.endswith("/retry") else "notifications.read"
    if path.startswith(("conversations", "conversation-projects", "attachments", "runs", "usage", "harness")):
        if not reading and (path.endswith("/runs") or path.startswith("runs/")):
            return "chat.run"
        if path.startswith("usage") or path.startswith("harness"):
            return "chat.read"
        return "chat.read" if reading else "chat.manage"
    if path.startswith("work-"):
        if not reading and ("/browser/" in path or path.startswith("work-browser-controls/")):
            return "work.execute"
        if not reading and (path.endswith(("/runs", "/resume", "/pause", "/cancel", "/recovery", "/decisions"))):
            return "work.execute"
        return "work.read" if reading else "work.manage"
    if path.startswith("agents"):
        return "agents.read" if reading else "agents.manage"
    if path.startswith(("workflows", "api-keys")):
        if not reading and (path.endswith(("/runs", "/test-runs", "/work-runs")) or path.startswith("api-keys")):
            return "workflows.execute"
        return "workflows.read" if reading else "workflows.manage"
    if path.startswith("capabilities/knowledge"):
        if path.endswith(("/search", "/image-search", "/image-query")):
            return "knowledge.search"
        return "knowledge.read" if reading else "knowledge.manage"
    if path.startswith("browser-skills"):
        return "skills.read" if reading else "skills.manage"
    if path.startswith("capabilities/habits"):
        return "skills.read" if reading else "skills.manage"
    if path.startswith("capabilities/skills"):
        return "skills.read" if reading else "skills.manage"
    if path.startswith("capabilities/mcp"):
        return "tools.read"  # Managing provider connections additionally requires administrator().
    if path.startswith("capabilities/"):
        return "chat.read" if reading else "chat.manage"
    if path.startswith("memories"):
        return "memory.read" if reading else "memory.manage"
    if path.startswith(("speech", "vision")):
        return "media.read" if reading else "media.use"
    if path.startswith("organizations"):
        if not reading and path.endswith("/runs"):
            return "workflows.execute"
        return "organizations.read" if reading else "organizations.manage"
    if path.startswith(("operations", "search")):
        return "operations.read" if path.startswith("operations") else "chat.read"
    return None


async def account(database, owner: UUID) -> dict:
    fence = getattr(database, "service_fence", None)
    if fence:
        await fence.check()
    rows = await database.fetch(
        "SELECT u.id,u.username,u.is_admin,u.enabled,u.approval_status,u.must_change_password,u.role_id,"
        "r.name AS role_name,r.permissions FROM users u JOIN platform_roles r ON r.id=u.role_id "
        "WHERE u.id=%s AND r.enabled",
        (owner,),
    )
    if not rows or not rows[0]["enabled"] or rows[0]["approval_status"] != "approved":
        fail(403, "ACCOUNT_UNAVAILABLE", "账号尚未通过审核或已停用")
    user = rows[0]
    if user["is_admin"]:
        user["permissions"], user["role_name"], user["role_id"] = list(PERMISSIONS), "平台管理员", "admin"
    return user


async def require_account(database, owner: UUID, permission: str) -> dict:
    """Recheck role changes and account suspension at asynchronous execution boundaries."""
    user = await account(database, owner)
    if user["must_change_password"]:
        fail(403, "PASSWORD_CHANGE_REQUIRED", "请先修改临时密码")
    if not user["is_admin"] and permission not in user["permissions"]:
        fail(403, "PERMISSION_REQUIRED", "当前角色没有此功能的权限")
    return user
