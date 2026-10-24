"""Derived native discussion routing; never copy message or work content."""
from sqlmodel import select

from ..models.message import Message


def native_discussion_root(session, channel_id, message_id, *, cache=None):
    """Resolve a live same-channel ancestry after the caller checked membership.

    The optional cache belongs to one authorized response, never a process or
    user session. Invalid, deleted, cyclic and overlong paths have no root.
    """
    if not isinstance(message_id, str) or not message_id:
        return None
    roots = cache if cache is not None else {}
    path = []
    visited = set()
    current = message_id
    root = None
    tail_depth = 0
    cacheable = True
    while current and len(path) < 64:
        if current in visited:
            break
        key = (channel_id, current)
        if key in roots:
            root, tail_depth = roots[key]
            if len(path) + tail_depth > 64:
                root = None
                cacheable = False
            break
        visited.add(current)
        path.append(key)
        row = session.exec(select(Message.id, Message.channel_id, Message.parent_id, Message.deleted)
                           .where(Message.id == current)).first()
        if not row or row[1] != channel_id or row[3]:
            break
        if not row[2]:
            root = row[0]
            break
        current = row[2]
    else:
        # A suffix may still have a valid root within the bound when queried
        # independently. Do not cache an overlong prefix as invalid ancestry.
        cacheable = False
    if cacheable:
        for index, key in enumerate(path):
            roots[key] = (root, len(path) - index + tail_depth if root else 0)
    return root
