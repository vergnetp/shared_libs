"""
app_kernel.db - Database connection management.

Entity methods auto-acquire connections when db is omitted:
    project = await Project.get(id="abc")       # auto-acquires + releases
    projects = await Project.find(where="x=?")  # auto-acquires + releases

For batching multiple ops on one connection:
    async with db_context() as db:
        project = await Project.get(db, id="abc")
        service = await Service.get(db, id="xyz")
"""

from .session import db_context

__all__ = ["db_context"]