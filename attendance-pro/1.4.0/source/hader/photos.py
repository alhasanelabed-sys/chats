"""An employee's picture: the photo set in the program or on the terminal (USERPIC), else the
face photo the terminal took when the face was enrolled (BIOPHOTO, visible-light faces)."""
from __future__ import annotations

import base64

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m


def photo_bytes(db: Session, e: m.Employee) -> bytes | None:
    data = e.photo or db.scalar(select(m.BioPhoto.content).where(m.BioPhoto.employee_id == e.id)
                                .order_by((m.BioPhoto.bio_type == 9).desc(), m.BioPhoto.id.desc()).limit(1))
    if not data:
        return None
    try:
        return base64.b64decode(data)
    except ValueError:
        return None


def with_photo(db: Session, ids) -> set[int]:
    """Which of these employees have a picture of either kind."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return set()
    own = set(db.scalars(select(m.Employee.id).where(m.Employee.id.in_(ids), m.Employee.photo != "",
                                                    m.Employee.photo.is_not(None))).all())
    return own | set(db.scalars(select(m.BioPhoto.employee_id).where(m.BioPhoto.employee_id.in_(ids))).all())
