import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, func, select

from app import crud
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.models import Item, ItemCreate, ItemShare, User
from tests.utils.user import create_random_user
from tests.utils.utils import random_email

ITEMS = f"{settings.API_V1_STR}/items"


def headers_for(user: User) -> dict[str, str]:
    token = create_access_token(user.id, timedelta(minutes=5))
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Sharing:
    owner: User
    recipient: User
    other: User
    item: Item


@pytest.fixture
def sharing(db: Session) -> Generator[Sharing]:
    owner, recipient, other = [create_random_user(db) for _ in range(3)]
    recipient.full_name = "Share recipient"
    db.add(recipient)
    db.commit()
    item = crud.create_item(
        session=db, item_in=ItemCreate(title="Shared item"), owner_id=owner.id
    )
    user_ids = [owner.id, recipient.id, other.id]
    yield Sharing(owner=owner, recipient=recipient, other=other, item=item)
    db.rollback()
    # Database cascades clean up every item and share created by these users.
    db.exec(delete(User).where(col(User.id).in_(user_ids)))
    db.commit()
    db.expire_all()


def share(client: TestClient, sharing: Sharing) -> dict[str, str]:
    response = client.post(
        f"{ITEMS}/{sharing.item.id}/share",
        headers=headers_for(sharing.owner),
        json={"email": sharing.recipient.email},
    )
    assert response.status_code == 200
    return response.json()


def test_share_item(client: TestClient, db: Session, sharing: Sharing) -> None:
    result = share(client, sharing)
    assert set(result) == {
        "id",
        "item_id",
        "shared_with_user_id",
        "shared_by_id",
        "permission",
        "created_at",
    }
    assert result["item_id"] == str(sharing.item.id)
    assert result["shared_with_user_id"] == str(sharing.recipient.id)
    assert result["shared_by_id"] == str(sharing.owner.id)
    assert result["permission"] == "edit"
    assert datetime.fromisoformat(result["created_at"]).utcoffset() == timedelta(0)
    stored = db.get(ItemShare, uuid.UUID(result["id"]))
    assert stored is not None
    assert stored.permission == "edit"


def test_share_nonexistent_recipient(client: TestClient, sharing: Sharing) -> None:
    response = client.post(
        f"{ITEMS}/{sharing.item.id}/share",
        headers=headers_for(sharing.owner),
        json={"email": random_email()},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_cannot_share_with_self(client: TestClient, sharing: Sharing) -> None:
    response = client.post(
        f"{ITEMS}/{sharing.item.id}/share",
        headers=headers_for(sharing.owner),
        json={"email": sharing.owner.email},
    )
    assert response.status_code == 400


def test_share_invalid_email(client: TestClient, sharing: Sharing) -> None:
    response = client.post(
        f"{ITEMS}/{sharing.item.id}/share",
        headers=headers_for(sharing.owner),
        json={"email": "invalid"},
    )
    assert response.status_code == 422


def test_duplicate_share_is_idempotent(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    first = share(client, sharing)
    assert share(client, sharing) == first
    assert (
        db.exec(
            select(func.count())
            .select_from(ItemShare)
            .where(ItemShare.item_id == sharing.item.id)
        ).one()
        == 1
    )


def test_concurrent_share_is_idempotent(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    barrier = Barrier(2)
    lookup = crud.get_user_by_email
    url = f"{ITEMS}/{sharing.item.id}/share"
    headers = headers_for(sharing.owner)
    body = {"email": sharing.recipient.email}

    def synchronized_lookup(*, session: Session, email: str) -> User | None:
        user = lookup(session=session, email=email)
        barrier.wait(timeout=10)
        return user

    def request_share() -> dict[str, str]:
        # Each request gets an independent database session, as in production.
        with TestClient(app) as concurrent_client:
            response = concurrent_client.post(url, headers=headers, json=body)
        assert response.status_code == 200
        return response.json()

    with (
        patch(
            "app.api.routes.items.crud.get_user_by_email",
            side_effect=synchronized_lookup,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [executor.submit(request_share) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1]
    assert (
        db.exec(
            select(func.count())
            .select_from(ItemShare)
            .where(ItemShare.item_id == sharing.item.id)
        ).one()
        == 1
    )
    # The session remains usable following the duplicate request.
    assert (
        client.get(url.replace("/share", "/shares"), headers=headers).status_code == 200
    )


def test_recipient_can_read_and_edit(client: TestClient, sharing: Sharing) -> None:
    share(client, sharing)
    url = f"{ITEMS}/{sharing.item.id}"
    headers = headers_for(sharing.recipient)
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(sharing.item.id)
    response = client.put(
        url,
        headers=headers,
        json={"title": "Edited by recipient", "description": "Shared edit"},
    )
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(sharing.owner.id)
    response = client.get(url, headers=headers_for(sharing.owner))
    assert response.json()["title"] == "Edited by recipient"
    assert response.json()["description"] == "Shared edit"


def test_recipient_cannot_delete(client: TestClient, sharing: Sharing) -> None:
    share(client, sharing)
    response = client.delete(
        f"{ITEMS}/{sharing.item.id}", headers=headers_for(sharing.recipient)
    )
    assert response.status_code == 403
    assert (
        client.get(
            f"{ITEMS}/{sharing.item.id}", headers=headers_for(sharing.owner)
        ).status_code
        == 200
    )


@pytest.mark.parametrize("actor", ["recipient", "other", "superuser"])
@pytest.mark.parametrize("operation", ["share", "list", "revoke"])
def test_only_actual_owner_manages_sharing(
    client: TestClient,
    sharing: Sharing,
    superuser_token_headers: dict[str, str],
    actor: str,
    operation: str,
) -> None:
    share(client, sharing)
    headers = (
        superuser_token_headers
        if actor == "superuser"
        else headers_for(getattr(sharing, actor))
    )
    if operation == "share":
        response = client.post(
            f"{ITEMS}/{sharing.item.id}/share",
            headers=headers,
            json={"email": sharing.other.email},
        )
    elif operation == "list":
        response = client.get(f"{ITEMS}/{sharing.item.id}/shares", headers=headers)
    else:
        response = client.delete(
            f"{ITEMS}/{sharing.item.id}/share/{sharing.recipient.id}", headers=headers
        )
    assert response.status_code == 403
    # A denied operation neither revokes access nor creates another share.
    recipients = client.get(
        f"{ITEMS}/{sharing.item.id}/shares", headers=headers_for(sharing.owner)
    ).json()
    assert [entry["id"] for entry in recipients] == [str(sharing.recipient.id)]


@pytest.mark.parametrize("operation", ["share", "list", "revoke"])
def test_sharing_missing_item(
    client: TestClient, sharing: Sharing, operation: str
) -> None:
    url = f"{ITEMS}/{uuid.uuid4()}"
    headers = headers_for(sharing.owner)
    if operation == "share":
        response = client.post(
            f"{url}/share", headers=headers, json={"email": sharing.recipient.email}
        )
    elif operation == "list":
        response = client.get(f"{url}/shares", headers=headers)
    else:
        response = client.delete(f"{url}/share/{sharing.recipient.id}", headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"


def test_owner_lists_sharees(client: TestClient, sharing: Sharing) -> None:
    first = share(client, sharing)
    response = client.get(
        f"{ITEMS}/{sharing.item.id}/shares", headers=headers_for(sharing.owner)
    )
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(sharing.recipient.id),
            "email": sharing.recipient.email,
            "full_name": sharing.recipient.full_name,
            "permission": "edit",
            "created_at": first["created_at"],
        }
    ]
    assert response.json()[0]["id"] != first["id"]


def test_owner_can_revoke_and_access_is_removed(
    client: TestClient, sharing: Sharing
) -> None:
    share(client, sharing)
    url = f"{ITEMS}/{sharing.item.id}"
    response = client.delete(
        f"{url}/share/{sharing.recipient.id}", headers=headers_for(sharing.owner)
    )
    assert response.status_code == 200
    assert response.json()["message"]
    headers = headers_for(sharing.recipient)
    assert client.get(url, headers=headers).status_code == 403
    assert client.put(url, headers=headers, json={"title": "Denied"}).status_code == 403
    assert client.get(f"{ITEMS}/shared-with-me", headers=headers).json() == {
        "data": [],
        "count": 0,
    }
    assert client.get(f"{url}/shares", headers=headers_for(sharing.owner)).json() == []
    assert client.get(url, headers=headers_for(sharing.owner)).status_code == 200


def test_revoke_missing_share(client: TestClient, sharing: Sharing) -> None:
    response = client.delete(
        f"{ITEMS}/{sharing.item.id}/share/{sharing.recipient.id}",
        headers=headers_for(sharing.owner),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Share not found"


def test_revoke_is_scoped_to_item(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    share(client, sharing)
    second = crud.create_item(
        session=db, item_in=ItemCreate(title="Other item"), owner_id=sharing.owner.id
    )
    response = client.delete(
        f"{ITEMS}/{second.id}/share/{sharing.recipient.id}",
        headers=headers_for(sharing.owner),
    )
    assert response.status_code == 404
    assert (
        client.get(
            f"{ITEMS}/{sharing.item.id}", headers=headers_for(sharing.recipient)
        ).status_code
        == 200
    )


def test_users_cannot_read_other_users_shares_or_inbox(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    share(client, sharing)
    other_item = crud.create_item(
        session=db,
        item_in=ItemCreate(title="Other user's item"),
        owner_id=sharing.other.id,
    )
    response = client.post(
        f"{ITEMS}/{other_item.id}/share",
        headers=headers_for(sharing.other),
        json={"email": sharing.owner.email},
    )
    assert response.status_code == 200
    for user, expected in [
        (sharing.owner, [str(other_item.id)]),
        (sharing.recipient, [str(sharing.item.id)]),
        (sharing.other, []),
    ]:
        # Forged identity query parameters must never select another user's inbox.
        response = client.get(
            f"{ITEMS}/shared-with-me",
            headers=headers_for(user),
            params={
                "user_id": str(sharing.recipient.id),
                "shared_with_user_id": str(sharing.owner.id),
            },
        )
        assert response.status_code == 200
        assert response.json()["count"] == len(expected)
        assert [item["id"] for item in response.json()["data"]] == expected
        assert (
            client.get(f"{ITEMS}/shared-with-me", headers=headers_for(user)).json()
            == response.json()
        )
        for actor in (sharing.owner, sharing.recipient, sharing.other):
            assert actor.email not in response.text
    for owner, item, recipient in [
        (sharing.owner, sharing.item, sharing.recipient),
        (sharing.other, other_item, sharing.owner),
    ]:
        response = client.get(f"{ITEMS}/{item.id}/shares", headers=headers_for(owner))
        assert response.status_code == 200
        assert [entry["email"] for entry in response.json()] == [recipient.email]
    for user, item in [(sharing.owner, other_item), (sharing.other, sharing.item)]:
        response = client.get(f"{ITEMS}/{item.id}/shares", headers=headers_for(user))
        assert response.status_code == 403
        assert response.json() == {"detail": "Not enough permissions"}
    assert (
        client.get(
            f"{ITEMS}/{other_item.id}", headers=headers_for(sharing.recipient)
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"{ITEMS}/{other_item.id}",
            headers=headers_for(sharing.recipient),
            json={"title": "Denied"},
        ).status_code
        == 403
    )


def test_shared_items_pagination_and_order(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    items = []
    for index in range(3):
        item = Item(
            title=f"Item {index}",
            owner_id=sharing.owner.id,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        items.append(item)
    # Insert shares in a different order: ordering follows Item.created_at.
    for item in reversed(items):
        db.add(
            ItemShare(
                item_id=item.id,
                shared_by_id=sharing.owner.id,
                shared_with_user_id=sharing.recipient.id,
            )
        )
    db.commit()
    url = f"{ITEMS}/shared-with-me"
    headers = headers_for(sharing.recipient)
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert [entry["id"] for entry in response.json()["data"]] == [
        str(item.id) for item in reversed(items)
    ]
    response = client.get(url, headers=headers, params={"skip": 1, "limit": 1})
    assert response.json()["count"] == 3
    assert [entry["id"] for entry in response.json()["data"]] == [str(items[1].id)]
    assert client.get(url, headers=headers, params={"skip": 3}).json() == {
        "data": [],
        "count": 3,
    }


@pytest.mark.parametrize("params", [{"skip": -1}, {"limit": 0}, {"limit": 101}])
def test_shared_items_pagination_bounds(
    client: TestClient, sharing: Sharing, params: dict[str, int]
) -> None:
    assert (
        client.get(
            f"{ITEMS}/shared-with-me",
            headers=headers_for(sharing.recipient),
            params=params,
        ).status_code
        == 422
    )


def test_owned_items_list_excludes_shared_items(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    share(client, sharing)
    owned = crud.create_item(
        session=db,
        item_in=ItemCreate(title="Recipient's own"),
        owner_id=sharing.recipient.id,
    )
    response = client.get(f"{ITEMS}/", headers=headers_for(sharing.recipient))
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert [item["id"] for item in response.json()["data"]] == [str(owned.id)]
    response = client.get(
        f"{ITEMS}/shared-with-me", headers=headers_for(sharing.recipient)
    )
    assert [item["id"] for item in response.json()["data"]] == [str(sharing.item.id)]


def test_superuser_access_and_list_behavior_preserved(
    client: TestClient, sharing: Sharing, superuser_token_headers: dict[str, str]
) -> None:
    share(client, sharing)
    url = f"{ITEMS}/{sharing.item.id}"
    assert client.get(url, headers=superuser_token_headers).status_code == 200
    assert (
        client.put(
            url, headers=superuser_token_headers, json={"title": "Admin edit"}
        ).status_code
        == 200
    )
    listed = client.get(
        f"{ITEMS}/", headers=superuser_token_headers, params={"limit": 1000}
    ).json()
    assert str(sharing.item.id) in [item["id"] for item in listed["data"]]
    assert client.get(
        f"{ITEMS}/shared-with-me", headers=superuser_token_headers
    ).json() == {"data": [], "count": 0}
    assert client.delete(url, headers=superuser_token_headers).status_code == 200


def test_superuser_can_manage_sharing_for_own_item(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    sharing.owner.is_superuser = True
    db.add(sharing.owner)
    db.commit()
    share(client, sharing)
    assert (
        client.get(
            f"{ITEMS}/{sharing.item.id}/shares", headers=headers_for(sharing.owner)
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"{ITEMS}/{sharing.item.id}/share/{sharing.recipient.id}",
            headers=headers_for(sharing.owner),
        ).status_code
        == 200
    )


def test_recipient_email_changes_preserve_share(
    client: TestClient, db: Session, sharing: Sharing
) -> None:
    first = share(client, sharing)
    sharing.recipient.email = random_email()
    db.add(sharing.recipient)
    db.commit()
    assert share(client, sharing) == first
    assert (
        client.get(
            f"{ITEMS}/{sharing.item.id}", headers=headers_for(sharing.recipient)
        ).status_code
        == 200
    )
    sharees = client.get(
        f"{ITEMS}/{sharing.item.id}/shares", headers=headers_for(sharing.owner)
    ).json()
    assert sharees[0]["email"] == sharing.recipient.email


@pytest.mark.parametrize("deleted", ["item", "recipient", "sharer"])
def test_database_cascades_remove_shares(
    db: Session, client: TestClient, sharing: Sharing, deleted: str
) -> None:
    result = share(client, sharing)
    share_id = uuid.UUID(result["id"])
    item_id = sharing.item.id
    if deleted == "item":
        db.exec(delete(Item).where(Item.id == item_id))
    elif deleted == "recipient":
        db.exec(delete(User).where(User.id == sharing.recipient.id))
    else:
        # Exercise shared_by_id independently of the item ownership cascade.
        sharing.item.owner_id = sharing.other.id
        db.add(sharing.item)
        db.commit()
        db.exec(delete(User).where(User.id == sharing.owner.id))
    db.commit()
    assert db.get(ItemShare, share_id) is None
    if deleted != "item":
        assert db.get(Item, item_id) is not None


@pytest.mark.parametrize("operation", ["share", "list", "revoke", "inbox"])
def test_sharing_requires_authentication(
    client: TestClient, sharing: Sharing, operation: str
) -> None:
    if operation == "share":
        response = client.post(
            f"{ITEMS}/{sharing.item.id}/share", json={"email": sharing.recipient.email}
        )
    elif operation == "list":
        response = client.get(f"{ITEMS}/{sharing.item.id}/shares")
    elif operation == "revoke":
        response = client.delete(
            f"{ITEMS}/{sharing.item.id}/share/{sharing.recipient.id}"
        )
    else:
        response = client.get(f"{ITEMS}/shared-with-me")
    assert response.status_code == 401
