"""The history's access code (control-plane.md C4, C8, C9); the tables are in app/db/models.py.

    eventlog.py   every protocol B event: written before anyone sees it, then followed (C4)
    carry.py      a conversation's last turns, as context for a new session (C9)
    conversations.py   a caller's conversations and their turns (the history routes)
"""
