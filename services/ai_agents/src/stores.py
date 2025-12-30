"""
Secure stores with workspace-based access control.

DESIGN PRINCIPLES:
1. NO "fetch then check" - scope is built into every query
2. Every method requires CurrentUser - no exceptions
3. Admin bypass is handled by authz helpers, not duplicated here
4. Document visibility invariants enforced on create/update

PATTERN:
    # WRONG - fetch then check
    entity = await conn.get_entity("things", id)
    if not can_access(user, entity):
        return None
    
    # RIGHT - scope in query
    where, params = await thing_scope(conn, user)
    entity = await conn.find_one("things", f"[id] = ? AND {where}", (id, *params))
"""

from typing import Optional, Any, List
from datetime import datetime
import uuid

from .authz import (
    CurrentUser,
    is_admin,
    # Scope builders (return WHERE clause + params)
    workspace_scope,
    thread_scope,
    agent_scope,
    document_scope,
    # Single-entity checks (for complex cases)
    can_access_workspace,
    can_manage_workspace,
    check_thread_access,
    check_agent_access,
    check_document_access,
    # Workspace helpers
    get_user_workspace_ids,
    is_workspace_member,
    create_workspace,
    add_workspace_member,
    remove_workspace_member,
    get_or_create_default_workspace,
    # Validation
    validate_document_visibility,
    normalize_document_visibility,
    VisibilityError,
)


# =============================================================================
# Helper: Scoped find_one
# =============================================================================

async def _scoped_get(
    conn: Any,
    table: str,
    entity_id: str,
    scope_where: str,
    scope_params: tuple,
) -> Optional[dict]:
    """
    Get entity by ID with scope filter baked into query.
    
    This is the canonical pattern - scope is in the WHERE clause,
    not a post-fetch check.
    """
    results = await conn.find_entities(
        table,
        where_clause=f"[id] = ? AND ({scope_where})",
        params=(entity_id, *scope_params),
        limit=1,
    )
    return results[0] if results else None


# =============================================================================
# Workspace Store
# =============================================================================

class WorkspaceStore:
    """CRUD for workspaces."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        name: str,
        *,
        user: CurrentUser,
        description: str = None,
        metadata: dict = None,
    ) -> dict:
        """Create a new workspace. Creator becomes owner."""
        return await create_workspace(
            self.conn,
            name=name,
            owner_user_id=user.id,
            description=description,
            metadata=metadata,
        )
    
    async def get(self, workspace_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get workspace by ID. Scope is in query."""
        if is_admin(user):
            return await self.conn.get_entity("workspaces", workspace_id)
        
        # Non-admin: must be member - check via join
        results = await self.conn.find_entities(
            "workspaces",
            where_clause="""
                [id] = ? AND [id] IN (
                    SELECT [workspace_id] FROM workspace_members WHERE [user_id] = ?
                )
            """,
            params=(workspace_id, user.id),
            limit=1,
        )
        return results[0] if results else None
    
    async def list(self, *, user: CurrentUser) -> List[dict]:
        """List workspaces user is a member of."""
        if is_admin(user):
            return await self.conn.find_entities("workspaces")
        
        # Scope in query
        return await self.conn.find_entities(
            "workspaces",
            where_clause="""
                [id] IN (SELECT [workspace_id] FROM workspace_members WHERE [user_id] = ?)
            """,
            params=(user.id,),
        )
    
    async def add_member(
        self,
        workspace_id: str,
        member_user_id: str,
        role: str = "member",
        *,
        user: CurrentUser,
    ) -> Optional[dict]:
        """Add member to workspace. Requires owner or admin."""
        if not await can_manage_workspace(self.conn, user, workspace_id):
            return None
        return await add_workspace_member(self.conn, workspace_id, member_user_id, role)
    
    async def remove_member(
        self,
        workspace_id: str,
        member_user_id: str,
        *,
        user: CurrentUser,
    ) -> bool:
        """Remove member from workspace. Requires owner or admin."""
        if not await can_manage_workspace(self.conn, user, workspace_id):
            return False
        return await remove_workspace_member(self.conn, workspace_id, member_user_id)
    
    async def get_members(
        self,
        workspace_id: str,
        *,
        user: CurrentUser,
    ) -> List[dict]:
        """List workspace members. Requires membership."""
        if not await can_access_workspace(self.conn, user, workspace_id):
            return []
        
        return await self.conn.find_entities(
            "workspace_members",
            where_clause="[workspace_id] = ?",
            params=(workspace_id,),
        )


# =============================================================================
# Thread Store
# =============================================================================

class SecureThreadStore:
    """Thread store with workspace-based access control."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        agent_id: str,
        workspace_id: str,
        *,
        user: CurrentUser,
        title: str = None,
        config: dict = None,
        metadata: dict = None,
    ) -> Optional[dict]:
        """Create a new thread. Requires workspace membership."""
        # Check workspace access before insert
        if not is_admin(user) and not await is_workspace_member(self.conn, user.id, workspace_id):
            return None
        
        now = datetime.utcnow().isoformat()
        return await self.conn.save_entity("threads", {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "title": title,
            "config": config or {},
            "user_id": user.id,
            "metadata": metadata or {},
            "message_count": 0,
            "total_bytes": 0,
            "archived": False,
            "created_at": now,
            "updated_at": now,
        })
    
    async def get(self, thread_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get thread by ID. Scope in query."""
        scope_where, scope_params = await thread_scope(self.conn, user)
        return await _scoped_get(self.conn, "threads", thread_id, scope_where, scope_params)
    
    async def update(
        self,
        thread_id: str,
        *,
        user: CurrentUser,
        **fields,
    ) -> Optional[dict]:
        """Update thread. Fetches with scope first."""
        thread = await self.get(thread_id, user=user)
        if not thread:
            return None
        
        # Prevent changing workspace_id or user_id
        fields.pop("workspace_id", None)
        fields.pop("user_id", None)
        
        for k, v in fields.items():
            thread[k] = v
        
        thread["updated_at"] = datetime.utcnow().isoformat()
        return await self.conn.save_entity("threads", thread)
    
    async def delete(self, thread_id: str, *, user: CurrentUser) -> bool:
        """Delete thread. Fetches with scope first."""
        thread = await self.get(thread_id, user=user)
        if not thread:
            return False
        return await self.conn.delete_entity("threads", thread_id)
    
    async def list(
        self,
        *,
        user: CurrentUser,
        workspace_id: str = None,
        agent_id: str = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> List[dict]:
        """List threads. Scope in query."""
        scope_where, scope_params = await thread_scope(self.conn, user)
        
        conditions = [scope_where]
        params = list(scope_params)
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        
        if agent_id:
            conditions.append("[agent_id] = ?")
            params.append(agent_id)
        
        if not include_archived:
            conditions.append("([archived] IS NULL OR [archived] = '0')")
        
        final_where = " AND ".join(conditions)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=final_where,
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )
    
    async def list_archived(
        self,
        *,
        user: CurrentUser,
        workspace_id: str = None,
        limit: int = 50,
    ) -> List[dict]:
        """List archived threads. Scope in query."""
        scope_where, scope_params = await thread_scope(self.conn, user)
        
        conditions = [scope_where, "[archived] = '1'"]
        params = list(scope_params)
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        
        return await self.conn.find_entities(
            "threads",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )
    
    async def archive(self, thread_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Archive a thread."""
        return await self.update(thread_id, user=user, archived=True)
    
    async def unarchive(self, thread_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Unarchive a thread."""
        return await self.update(thread_id, user=user, archived=False)
    
    async def fork(
        self,
        thread_id: str,
        *,
        user: CurrentUser,
        workspace_id: str = None,
        title: str = None,
        up_to_message_id: str = None,
    ) -> Optional[dict]:
        """Fork a thread."""
        source = await self.get(thread_id, user=user)
        if not source:
            return None
        
        target_workspace = workspace_id or source["workspace_id"]
        
        # Check can create in target workspace
        if not is_admin(user) and not await is_workspace_member(self.conn, user.id, target_workspace):
            return None
        
        fork_title = title or f"Fork of {source.get('title') or thread_id[:8]}"
        new_thread = await self.create(
            agent_id=source["agent_id"],
            workspace_id=target_workspace,
            user=user,
            title=fork_title,
            config=source.get("config", {}),
            metadata={
                **source.get("metadata", {}),
                "forked_from": thread_id,
                "forked_at": datetime.utcnow().isoformat(),
            },
        )
        
        if not new_thread:
            return None
        
        # Copy messages
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
            order_by="created_at ASC",
        )
        
        if up_to_message_id:
            truncated = []
            for msg in messages:
                truncated.append(msg)
                if msg["id"] == up_to_message_id:
                    break
            messages = truncated
        
        total_bytes = 0
        now = datetime.utcnow().isoformat()
        for msg in messages:
            content = msg.get("content", "")
            total_bytes += len(content.encode("utf-8"))
            
            await self.conn.save_entity("messages", {
                "id": str(uuid.uuid4()),
                "thread_id": new_thread["id"],
                "role": msg["role"],
                "content": content,
                "tool_calls": msg.get("tool_calls", []),
                "tool_call_id": msg.get("tool_call_id"),
                "attachments": msg.get("attachments", []),
                "metadata": {
                    **msg.get("metadata", {}),
                    "copied_from": msg["id"],
                },
                "created_at": now,
            })
        
        return await self.update(
            new_thread["id"],
            user=user,
            message_count=len(messages),
            total_bytes=total_bytes,
        )
    
    async def branch(
        self,
        thread_id: str,
        from_message_id: str,
        *,
        user: CurrentUser,
        title: str = None,
    ) -> Optional[dict]:
        """Branch from a specific message."""
        return await self.fork(
            thread_id,
            user=user,
            up_to_message_id=from_message_id,
            title=title or "Branch",
        )
    
    async def get_stats(self, thread_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get thread statistics."""
        thread = await self.get(thread_id, user=user)
        if not thread:
            return None
        
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
        
        return {
            "thread_id": thread_id,
            "message_count": len(messages),
            "user_messages": sum(1 for m in messages if m["role"] == "user"),
            "assistant_messages": sum(1 for m in messages if m["role"] == "assistant"),
            "tool_messages": sum(1 for m in messages if m["role"] == "tool"),
            "total_bytes": thread.get("total_bytes", 0),
            "archived": thread.get("archived", False),
        }


# =============================================================================
# Message Store
# =============================================================================

class SecureMessageStore:
    """Message store - access inherited from thread."""
    
    def __init__(self, conn: Any):
        self.conn = conn
        self._threads = SecureThreadStore(conn)
    
    async def _verify_thread_access(self, thread_id: str, user: CurrentUser) -> Optional[dict]:
        """Verify user can access thread. Returns thread or None."""
        return await self._threads.get(thread_id, user=user)
    
    async def create(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        user: CurrentUser,
        tool_calls: list = None,
        tool_call_id: str = None,
        attachments: list = None,
        metadata: dict = None,
    ) -> Optional[dict]:
        """Create message. Requires thread access."""
        thread = await self._verify_thread_access(thread_id, user)
        if not thread:
            return None
        
        now = datetime.utcnow().isoformat()
        msg = await self.conn.save_entity("messages", {
            "id": str(uuid.uuid4()),
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls or [],
            "tool_call_id": tool_call_id,
            "attachments": attachments or [],
            "metadata": metadata or {},
            "user_id": user.id,
            "created_at": now,
        })
        
        await self._update_thread_size(thread_id, user)
        return msg
    
    async def get(self, message_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get message. Requires thread access."""
        # First get message to find thread_id
        msg = await self.conn.get_entity("messages", message_id)
        if not msg:
            return None
        
        # Then verify thread access
        thread = await self._verify_thread_access(msg["thread_id"], user)
        if not thread:
            return None
        
        return msg
    
    async def list(
        self,
        thread_id: str,
        *,
        user: CurrentUser,
        limit: int = 100,
        before: str = None,
        after: str = None,
    ) -> List[dict]:
        """List messages. Requires thread access."""
        thread = await self._verify_thread_access(thread_id, user)
        if not thread:
            return []
        
        conditions = ["[thread_id] = ?"]
        params = [thread_id]
        
        if before:
            conditions.append("[created_at] < (SELECT created_at FROM messages WHERE id = ?)")
            params.append(before)
        
        if after:
            conditions.append("[created_at] > (SELECT created_at FROM messages WHERE id = ?)")
            params.append(after)
        
        return await self.conn.find_entities(
            "messages",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at ASC",
            limit=limit,
        )
    
    async def delete(self, message_id: str, *, user: CurrentUser) -> bool:
        """Delete message. Requires thread access."""
        msg = await self.get(message_id, user=user)
        if not msg:
            return False
        
        result = await self.conn.delete_entity("messages", message_id)
        if result:
            await self._update_thread_size(msg["thread_id"], user)
        return result
    
    async def _update_thread_size(self, thread_id: str, user: CurrentUser):
        """Update thread message count and size."""
        messages = await self.conn.find_entities(
            "messages",
            where_clause="[thread_id] = ?",
            params=(thread_id,),
        )
        
        total_bytes = sum(
            len(m.get("content", "").encode("utf-8"))
            for m in messages
        )
        
        await self._threads.update(
            thread_id,
            user=user,
            message_count=len(messages),
            total_bytes=total_bytes,
        )


# =============================================================================
# Agent Store
# =============================================================================

class SecureAgentStore:
    """Agent store with ownership enforcement."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        name: str,
        *,
        user: CurrentUser,
        owner_user_id: str = None,
        workspace_id: str = None,
        system_prompt: str = None,
        model: str = "claude-sonnet-4-20250514",
        provider: str = "anthropic",
        tools: list = None,
        capabilities: list = None,
        context_schema: dict = None,
        metadata: dict = None,
        **kwargs,
    ) -> Optional[dict]:
        """
        Create agent.
        
        Must specify exactly one of owner_user_id or workspace_id.
        """
        # Validate XOR
        if bool(owner_user_id) == bool(workspace_id):
            raise ValueError("Must specify exactly one of owner_user_id or workspace_id")
        
        # Personal agent: owner must be self (unless admin)
        if owner_user_id and owner_user_id != user.id and not is_admin(user):
            return None
        
        # Workspace agent: require membership
        if workspace_id and not is_admin(user):
            if not await is_workspace_member(self.conn, user.id, workspace_id):
                return None
        
        now = datetime.utcnow().isoformat()
        return await self.conn.save_entity("agents", {
            "id": str(uuid.uuid4()),
            "name": name,
            "owner_user_id": owner_user_id,
            "workspace_id": workspace_id,
            "system_prompt": system_prompt,
            "model": model,
            "provider": provider,
            "tools": tools or [],
            "capabilities": capabilities or [],
            "context_schema": context_schema,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            **{k: v for k, v in kwargs.items() if k in [
                "temperature", "max_tokens", "memory_strategy", "memory_params",
                "premium_provider", "premium_model",
            ]},
        })
    
    async def get(self, agent_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get agent by ID. Scope in query."""
        scope_where, scope_params = await agent_scope(self.conn, user)
        return await _scoped_get(self.conn, "agents", agent_id, scope_where, scope_params)
    
    async def update(
        self,
        agent_id: str,
        *,
        user: CurrentUser,
        **fields,
    ) -> Optional[dict]:
        """Update agent. Fetches with scope first."""
        agent = await self.get(agent_id, user=user)
        if not agent:
            return None
        
        # Prevent changing ownership
        fields.pop("owner_user_id", None)
        fields.pop("workspace_id", None)
        
        for k, v in fields.items():
            agent[k] = v
        
        agent["updated_at"] = datetime.utcnow().isoformat()
        return await self.conn.save_entity("agents", agent)
    
    async def delete(self, agent_id: str, *, user: CurrentUser) -> bool:
        """Delete agent. Fetches with scope first."""
        agent = await self.get(agent_id, user=user)
        if not agent:
            return False
        return await self.conn.delete_entity("agents", agent_id)
    
    async def list(
        self,
        *,
        user: CurrentUser,
        workspace_id: str = None,
        include_personal: bool = True,
        limit: int = 50,
    ) -> List[dict]:
        """List agents. Scope in query."""
        scope_where, scope_params = await agent_scope(self.conn, user)
        
        conditions = [scope_where]
        params = list(scope_params)
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        elif not include_personal:
            conditions.append("[workspace_id] IS NOT NULL")
        
        return await self.conn.find_entities(
            "agents",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )


# =============================================================================
# Document Store
# =============================================================================

class SecureDocumentStore:
    """Document store with ownership and visibility enforcement."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def create(
        self,
        filename: str,
        *,
        user: CurrentUser,
        id: str = None,  # Optional pre-generated ID
        workspace_id: str = None,
        visibility: str = "private",
        content_type: str = None,
        size_bytes: int = 0,
        agent_id: str = None,
        title: str = None,
        tags: list = None,
        metadata: dict = None,
    ) -> Optional[dict]:
        """
        Create document.
        
        Args:
            id: Optional pre-generated ID (useful for async processing)
        
        Enforces visibility invariant:
        - private → workspace_id must be NULL
        - workspace → workspace_id must be NOT NULL + user must be member
        """
        # Normalize and validate visibility
        visibility, workspace_id = normalize_document_visibility(visibility, workspace_id)
        
        try:
            validate_document_visibility(visibility, workspace_id)
        except VisibilityError as e:
            raise ValueError(str(e))
        
        # If workspace visibility, require membership
        if workspace_id and not is_admin(user):
            if not await is_workspace_member(self.conn, user.id, workspace_id):
                return None
        
        now = datetime.utcnow().isoformat()
        doc_id = id or str(uuid.uuid4())
        return await self.conn.save_entity("documents", {
            "id": doc_id,
            "filename": filename,
            "owner_user_id": user.id,
            "workspace_id": workspace_id,
            "visibility": visibility,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "agent_id": agent_id,
            "title": title,
            "tags": tags or [],
            "metadata": metadata or {},
            "created_by": user.id,
            "created_at": now,
            "updated_at": now,
        })
    
    async def get(self, document_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get document by ID. Scope in query."""
        scope_where, scope_params = await document_scope(self.conn, user)
        return await _scoped_get(self.conn, "documents", document_id, scope_where, scope_params)
    
    async def update(
        self,
        document_id: str,
        *,
        user: CurrentUser,
        **fields,
    ) -> Optional[dict]:
        """Update document. Fetches with scope first."""
        doc = await self.get(document_id, user=user)
        if not doc:
            return None
        
        # Handle visibility changes
        new_visibility = fields.get("visibility", doc.get("visibility"))
        new_workspace = fields.get("workspace_id", doc.get("workspace_id"))
        
        # Only owner can change visibility/workspace
        if doc.get("owner_user_id") != user.id and not is_admin(user):
            fields.pop("visibility", None)
            fields.pop("workspace_id", None)
        else:
            # Validate visibility invariant
            if "visibility" in fields or "workspace_id" in fields:
                new_visibility, new_workspace = normalize_document_visibility(new_visibility, new_workspace)
                try:
                    validate_document_visibility(new_visibility, new_workspace)
                except VisibilityError as e:
                    raise ValueError(str(e))
                fields["visibility"] = new_visibility
                fields["workspace_id"] = new_workspace
        
        for k, v in fields.items():
            doc[k] = v
        
        doc["updated_at"] = datetime.utcnow().isoformat()
        doc["updated_by"] = user.id
        return await self.conn.save_entity("documents", doc)
    
    async def delete(self, document_id: str, *, user: CurrentUser) -> bool:
        """Delete document. Only owner or admin can delete."""
        # Get with scope first
        doc = await self.get(document_id, user=user)
        if not doc:
            return False
        
        # Only owner or admin can delete
        if doc.get("owner_user_id") != user.id and not is_admin(user):
            return False
        
        return await self.conn.delete_entity("documents", document_id)
    
    async def list(
        self,
        *,
        user: CurrentUser,
        workspace_id: str = None,
        agent_id: str = None,
        visibility: str = None,
        limit: int = 50,
    ) -> List[dict]:
        """List documents. Scope in query."""
        scope_where, scope_params = await document_scope(self.conn, user)
        
        conditions = [scope_where]
        params = list(scope_params)
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        
        if agent_id:
            conditions.append("[agent_id] = ?")
            params.append(agent_id)
        
        if visibility:
            conditions.append("[visibility] = ?")
            params.append(visibility)
        
        return await self.conn.find_entities(
            "documents",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="created_at DESC",
            limit=limit,
        )


# =============================================================================
# User Context Store
# =============================================================================

class SecureUserContextStore:
    """User context - users can only access their own."""
    
    def __init__(self, conn: Any):
        self.conn = conn
    
    async def get(self, target_user_id: str, *, user: CurrentUser) -> Optional[dict]:
        """Get user context. Users can only access their own (admin can access any)."""
        if target_user_id != user.id and not is_admin(user):
            return None
        
        results = await self.conn.find_entities(
            "user_context",
            where_clause="[user_id] = ?",
            params=(target_user_id,),
            limit=1,
        )
        
        if not results:
            return {}
        
        import json
        context = results[0].get("context", "{}")
        if isinstance(context, str):
            try:
                return json.loads(context)
            except:
                return {}
        return context or {}
    
    async def set(
        self,
        target_user_id: str,
        context: dict,
        *,
        user: CurrentUser,
        reason: str = None,
    ) -> Optional[dict]:
        """Set user context. Users can only modify their own."""
        if target_user_id != user.id and not is_admin(user):
            return None
        
        import json
        now = datetime.utcnow().isoformat()
        
        results = await self.conn.find_entities(
            "user_context",
            where_clause="[user_id] = ?",
            params=(target_user_id,),
            limit=1,
        )
        
        if results:
            entity = results[0]
            entity["context"] = json.dumps(context)
            entity["updated_at"] = now
            entity["last_reason"] = reason
            await self.conn.save_entity("user_context", entity)
        else:
            await self.conn.save_entity("user_context", {
                "id": str(uuid.uuid4()),
                "user_id": target_user_id,
                "context": json.dumps(context),
                "created_at": now,
                "updated_at": now,
                "last_reason": reason,
            })
        
        return context
    
    async def update(
        self,
        target_user_id: str,
        updates: dict,
        *,
        user: CurrentUser,
        reason: str = None,
    ) -> Optional[dict]:
        """Update user context (deep merge)."""
        current = await self.get(target_user_id, user=user)
        if current is None:
            return None
        
        merged = self._deep_merge(current, updates)
        return await self.set(target_user_id, merged, user=user, reason=reason)
    
    async def delete(self, target_user_id: str, *, user: CurrentUser) -> bool:
        """Delete user context. Users can only delete their own (admin can delete any)."""
        if target_user_id != user.id and not is_admin(user):
            return False
        
        results = await self.conn.find_entities(
            "user_context",
            where_clause="[user_id] = ?",
            params=(target_user_id,),
            limit=1,
        )
        
        if results:
            return await self.conn.delete_entity("user_context", results[0]["id"])
        return True  # Already doesn't exist
    
    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge updates into base."""
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# =============================================================================
# Aliases
# =============================================================================

ThreadStore = SecureThreadStore
MessageStore = SecureMessageStore
AgentStore = SecureAgentStore
DocumentStore = SecureDocumentStore
UserContextStore = SecureUserContextStore
