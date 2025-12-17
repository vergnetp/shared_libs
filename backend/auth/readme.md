# Auth Module

Authentication and authorization with resource-scoped roles.

## Quick Start

```python
from auth import AuthService, DatabaseUserStore, DatabaseRoleStore

# Setup
auth = AuthService(
    user_store=DatabaseUserStore("postgres", database="myapp"),
    role_store=DatabaseRoleStore("postgres", database="myapp"),
    token_secret=os.environ["JWT_SECRET"]
)

# Register
user = await auth.register("alice@example.com", "password123")

# Login (returns access + refresh tokens)
user, access_token, refresh_token = await auth.login("alice@example.com", "password123")

# Verify token
user = await auth.verify_token(access_token)

# Refresh tokens
new_access, new_refresh = await auth.refresh_tokens(refresh_token)
```

## Roles & Permissions

Roles are scoped to resources. A user can have different roles on different resources.

```python
# Create roles (once, at app startup)
await auth.create_role("viewer", ["read"])
await auth.create_role("editor", ["read", "write", "comment"])
await auth.create_role("owner", ["read", "write", "comment", "delete", "admin"])

# Assign role to user on a resource
await auth.assign_role(
    user_id=user.id,
    role_name="editor",
    resource_type="project",
    resource_id="proj-123"
)

# Global role (no resource_id)
await auth.assign_role(
    user_id=admin.id,
    role_name="admin",
    resource_type="system",
    resource_id=None  # global
)

# Check permission
can_write = await auth.has_permission(user.id, "write", "project", "proj-123")

# Require permission (raises AuthError if denied)
await auth.require_permission(user.id, "delete", "project", "proj-123")
```

## Use Cases

### Mediation Platform

```python
# Roles
await auth.create_role("party", ["read_own", "write_own", "comment"])
await auth.create_role("moderator", ["read_all", "comment", "suggest", "summarize"])
await auth.create_role("counsel", ["read_client", "advise", "draft"])

# Assign to a case
await auth.assign_role(alice.id, "party", "case", "case-123")
await auth.assign_role(bob.id, "party", "case", "case-123")
await auth.assign_role(ai_mediator.id, "moderator", "case", "case-123")

# Document-level permissions
await auth.assign_role(alice.id, "owner", "document", "doc-456")
await auth.assign_role(bob.id, "viewer", "document", "doc-456")
```

### Property Management

```python
# Roles
await auth.create_role("tenant", ["read_own", "submit_request", "view_payments"])
await auth.create_role("manager", ["read", "write", "handle_requests", "view_tenants"])
await auth.create_role("owner", ["read_all", "financials", "reports"])

# Assign to properties
await auth.assign_role(tenant.id, "tenant", "property", "prop-123")
await auth.assign_role(pm.id, "manager", "property", "prop-123")
await auth.assign_role(owner.id, "owner", "portfolio", "portfolio-456")
```

## FastAPI Integration

```python
from fastapi import FastAPI, Depends, HTTPException, Header
from auth import AuthService, DatabaseUserStore, DatabaseRoleStore, AuthError

auth = AuthService(
    user_store=DatabaseUserStore("postgres", database="myapp"),
    role_store=DatabaseRoleStore("postgres", database="myapp"),
    token_secret=os.environ["JWT_SECRET"]
)

async def get_current_user(authorization: str = Header()):
    try:
        token = authorization.replace("Bearer ", "")
        return await auth.verify_token(token)
    except AuthError as e:
        raise HTTPException(401, str(e))

def require_permission(permission: str, resource_type: str):
    async def checker(user = Depends(get_current_user), resource_id: str = None):
        if not await auth.has_permission(user.id, permission, resource_type, resource_id):
            raise HTTPException(403, "Permission denied")
        return user
    return checker

@app.post("/register")
async def register(email: str, password: str):
    user = await auth.register(email, password)
    return {"id": user.id}

@app.post("/login")
async def login(email: str, password: str):
    user, access, refresh = await auth.login(email, password)
    return {"access_token": access, "refresh_token": refresh}

@app.get("/me")
async def me(user = Depends(get_current_user)):
    return {"email": user.email}

@app.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    user = Depends(require_permission("write", "project"))
):
    # User has write permission on this project
    ...
```

## Testing

Use `MemoryUserStore` and `MemoryRoleStore` for tests:

```python
from auth import AuthService, MemoryUserStore, MemoryRoleStore

@pytest.fixture
def auth():
    return AuthService(
        user_store=MemoryUserStore(),
        role_store=MemoryRoleStore(),
        token_secret="test-secret"
    )

async def test_register_and_login(auth):
    user = await auth.register("test@example.com", "password")
    assert user.email == "test@example.com"
    
    user, access, refresh = await auth.login("test@example.com", "password")
    assert access is not None
    
    verified = await auth.verify_token(access)
    assert verified.id == user.id
```

## Database Tables

The module creates these tables automatically via the entity framework:

- `auth_users` - User accounts
- `auth_roles` - Role definitions
- `auth_role_assignments` - User-role-resource mappings

## Dependencies

```
pip install bcrypt PyJWT
```

Falls back to PBKDF2 if bcrypt is not installed.
