"""Generic create/read/update/delete routes for the config tables."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from app.db import get_session


def crud_router(model: type[SQLModel], prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[prefix.strip("/")])

    @router.get("")
    def list_items(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return [i.model_dump() for i in db.exec(select(model).order_by(model.id)).all()]

    @router.get("/{item_id}")
    def get_item(item_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
        item = db.get(model, item_id)
        if item is None:
            raise HTTPException(404)
        return item.model_dump()

    @router.post("")
    def create_item(body: dict[str, Any], db: Session = Depends(get_session)) -> dict[str, Any]:
        body.pop("id", None)
        item = model.model_validate(body)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.model_dump()

    @router.put("/{item_id}")
    def update_item(item_id: int, body: dict[str, Any], db: Session = Depends(get_session)) -> dict[str, Any]:
        item = db.get(model, item_id)
        if item is None:
            raise HTTPException(404)
        body.pop("id", None)
        merged = model.model_validate({**item.model_dump(), **body})
        for k, v in merged.model_dump().items():
            setattr(item, k, v)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.model_dump()

    @router.delete("/{item_id}")
    def delete_item(item_id: int, db: Session = Depends(get_session)) -> dict[str, bool]:
        item = db.get(model, item_id)
        if item is None:
            raise HTTPException(404)
        from app.models import DismissedDefault, Role, Skill, Tool

        if getattr(item, "slug", None):
            # Remember the deletion so built-in defaults aren't recreated on the next start.
            db.add(DismissedDefault(kind=model.__tablename__, slug=item.slug))
        if model is Tool:  # don't leave the deleted source in roles' or skills' lists
            for owner in [*db.exec(select(Role)).all(), *db.exec(select(Skill)).all()]:
                if item.id in (owner.tool_ids or []):
                    owner.tool_ids = [i for i in owner.tool_ids if i != item.id]
                    db.add(owner)
        db.delete(item)
        db.commit()
        return {"ok": True}

    return router
