import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, func, select

from app import crud
from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Item,
    ItemCreate,
    ItemPublic,
    ItemShare,
    ItemShareCreate,
    ItemSharePublic,
    ItemShareUserPublic,
    ItemsPublic,
    ItemUpdate,
    Message,
    User,
)

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=ItemsPublic)
def read_items(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve items.
    """

    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Item)
        count = session.exec(count_statement).one()
        statement = (
            select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
        )
        items = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(Item)
            .where(Item.owner_id == current_user.id)
        )
        count = session.exec(count_statement).one()
        statement = (
            select(Item)
            .where(Item.owner_id == current_user.id)
            .order_by(col(Item.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        items = session.exec(statement).all()

    items_public = [ItemPublic.model_validate(item) for item in items]
    return ItemsPublic(data=items_public, count=count)


@router.get("/shared-with-me", response_model=ItemsPublic)
def read_shared_items(
    session: SessionDep,
    current_user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> Any:
    """Retrieve items shared with the current user."""
    count = session.exec(
        select(func.count())
        .select_from(ItemShare)
        .where(ItemShare.shared_with_user_id == current_user.id)
    ).one()
    items = session.exec(
        select(Item)
        .join(ItemShare, col(ItemShare.item_id) == Item.id)
        .where(ItemShare.shared_with_user_id == current_user.id)
        .order_by(col(Item.created_at).desc(), col(Item.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return ItemsPublic(
        data=[ItemPublic.model_validate(item) for item in items], count=count
    )


def _get_owned_item(*, session: Session, current_user: User, id: uuid.UUID) -> Item:
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/{id}/share", response_model=ItemSharePublic)
def share_item(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    share_in: ItemShareCreate,
) -> Any:
    """Share an owned item with a user by email."""
    item = _get_owned_item(session=session, current_user=current_user, id=id)
    recipient = crud.get_user_by_email(session=session, email=share_in.email)
    if not recipient:
        raise HTTPException(status_code=404, detail="User not found")
    if recipient.id == current_user.id:
        raise HTTPException(
            status_code=400, detail="Cannot share an item with yourself"
        )

    share = ItemShare(
        item_id=item.id,
        shared_with_user_id=recipient.id,
        shared_by_id=current_user.id,
    )
    # The unique constraint also makes simultaneous requests idempotent.
    session.exec(
        insert(ItemShare)
        .values(share.model_dump())
        .on_conflict_do_nothing(constraint="uq_itemshare_item_recipient")
    )
    session.commit()
    return session.exec(
        select(ItemShare).where(
            ItemShare.item_id == id, ItemShare.shared_with_user_id == recipient.id
        )
    ).one()


@router.delete("/{id}/share/{user_id}", response_model=Message)
def revoke_item_share(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID, user_id: uuid.UUID
) -> Message:
    """Revoke a recipient's access to an owned item."""
    _get_owned_item(session=session, current_user=current_user, id=id)
    share = session.exec(
        select(ItemShare).where(
            ItemShare.item_id == id, ItemShare.shared_with_user_id == user_id
        )
    ).first()
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    session.delete(share)
    session.commit()
    return Message(message="Item share revoked successfully")


@router.get("/{id}/shares", response_model=list[ItemShareUserPublic])
def read_item_shares(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """List recipients of an owned item."""
    _get_owned_item(session=session, current_user=current_user, id=id)
    shares = session.exec(
        select(User, ItemShare)
        .join(ItemShare, col(ItemShare.shared_with_user_id) == User.id)
        .where(ItemShare.item_id == id)
        .order_by(col(ItemShare.created_at).desc(), col(ItemShare.id))
    ).all()
    return [
        ItemShareUserPublic(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            permission=share.permission,
            created_at=share.created_at,
        )
        for user, share in shares
    ]


@router.get("/{id}", response_model=ItemPublic)
def read_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get item by ID.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if (
        not current_user.is_superuser
        and item.owner_id != current_user.id
        and not session.exec(
            select(ItemShare).where(
                ItemShare.item_id == id,
                ItemShare.shared_with_user_id == current_user.id,
            )
        ).first()
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
def create_item(
    *, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate
) -> Any:
    """
    Create new item.
    """
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.put("/{id}", response_model=ItemPublic)
def update_item(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    item_in: ItemUpdate,
) -> Any:
    """
    Update an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if (
        not current_user.is_superuser
        and item.owner_id != current_user.id
        and not session.exec(
            select(ItemShare).where(
                ItemShare.item_id == id,
                ItemShare.shared_with_user_id == current_user.id,
                ItemShare.permission == "edit",
            )
        ).first()
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = item_in.model_dump(exclude_unset=True)
    item.sqlmodel_update(update_dict)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{id}")
def delete_item(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(item)
    session.commit()
    return Message(message="Item deleted successfully")
