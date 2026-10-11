"""Reset a user's password from the server PC (forgotten password).

    python tools/reset_password.py                       # generate a temporary password for admin
    python tools/reset_password.py ali                  # generate a temporary password for ali
    python tools/reset_password.py ali new-long-password

The user is asked to choose a new password at the next login.
"""
from __future__ import annotations

import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "admin"
    password = sys.argv[2] if len(sys.argv) > 2 else secrets.token_urlsafe(18)
    from sqlalchemy import select
    from hader import models as m
    from hader.bootstrap import init_db
    from hader.db import session_scope
    from hader.security import hash_password, validate_password
    try:
        validate_password(password, identity=username)
    except ValueError as exc:
        print(str(exc))
        return 1
    init_db()
    with session_scope() as db:
        u = db.scalar(select(m.User).where(m.User.username == username))
        if u is None:
            if username != "admin":
                print(f"User '{username}' not found.")
                return 1
            u = m.User(username="admin", full_name="Administrator", is_superuser=True)
            db.add(u)
        u.password_hash = hash_password(password)
        u.must_change_password = True
        u.active = True  # a disabled account is opened again
        db.add(m.AuditLog(username="console", action="user.password_reset", target=username, detail="reset_password"))
    print(f"Password of '{username}' reset. Log in with: {username} / {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
