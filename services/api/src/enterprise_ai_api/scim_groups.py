"""SCIM directory groups. Sync never grants roles or knowledge-space access."""

import json
import re
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.auth_routes import DatabaseSession
from enterprise_ai_api.federation import subject_hash
from enterprise_ai_api.federation_routes import (
    SCIM_PATCH,
    SCIM_USER,
    _body,
    _identities,
    _scim_auth,
    _scim_error,
)
from enterprise_ai_api.models import FederatedGroup, FederatedIdentity

router = APIRouter(prefix="/scim/v2")
SCIM_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


def _groups(provider):
    return select(FederatedGroup).where(
        FederatedGroup.tenant_id == provider.tenant_id,
        FederatedGroup.provider == provider.key,
        FederatedGroup.issuer == provider.issuer,
    )


def _members(session, provider, values):
    if not isinstance(values, list) or len(values) > 500:
        raise ValueError("Groups support at most 500 User members")
    ids = set()
    for item in values:
        if not isinstance(item, dict) or item.get("type", "User") != "User":
            raise ValueError("Only User members supported")
        try:
            ids.add(UUID(item["value"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("Invalid member id") from None
    found = set(
        session.scalars(
            _identities(provider)
            .with_only_columns(FederatedIdentity.id)
            .where(FederatedIdentity.id.in_(ids))
        )
    )
    if ids != found:
        raise ValueError("Members must be active directory resources of this provider and tenant")
    return {str(value) for value in ids}


def _resource(session, provider, group):
    ids = [UUID(value) for value in json.loads(group.members_json)]
    existing = session.scalars(
        _identities(provider)
        .with_only_columns(FederatedIdentity.id)
        .where(FederatedIdentity.id.in_(ids))
    )
    return {
        "schemas": [SCIM_GROUP],
        "id": str(group.id),
        "externalId": group.external_id,
        "displayName": group.display_name,
        "members": [{"value": str(value), "type": "User"} for value in existing],
        "meta": {"resourceType": "Group"},
    }


@router.api_route("/{key}/Groups", methods=["GET", "POST"])
async def groups(key: str, request: Request, session: DatabaseSession):
    provider = _scim_auth(request, key, session)
    if request.method == "GET":
        try:
            start, count = (
                int(request.query_params.get("startIndex", "1")),
                int(request.query_params.get("count", "100")),
            )
            if start < 1 or not 0 <= count <= 100:
                raise ValueError()
        except ValueError:
            return _scim_error(400, "Invalid pagination")
        statement = _groups(provider)
        filter_value = request.query_params.get("filter")
        name_filter = None
        if filter_value:
            match = re.fullmatch(r'(externalId|displayName) eq ("(?:[^"\\]|\\.)*")', filter_value)
            if not match:
                return _scim_error(
                    400, "Supported filters: externalId eq or displayName eq", "invalidFilter"
                )
            try:
                value = json.loads(match[2])
            except ValueError:
                return _scim_error(400, "Invalid filter", "invalidFilter")
            if match[1] == "externalId":
                statement = statement.where(FederatedGroup.external_id_hash == subject_hash(value))
            else:
                name_filter = value.casefold()
        items = [
            group
            for group in session.scalars(statement.order_by(FederatedGroup.id))
            if name_filter is None or group.display_name.casefold() == name_filter
        ]
        resources = [
            _resource(session, provider, group) for group in items[start - 1 : start - 1 + count]
        ]
        return {
            "schemas": [LIST],
            "Resources": resources,
            "totalResults": len(items),
            "startIndex": start,
            "itemsPerPage": len(resources),
        }
    payload = await _body(request)
    if isinstance(payload, Response):
        return payload
    try:
        name, external_id = payload["displayName"], payload["externalId"]
        if (
            SCIM_GROUP not in payload.get("schemas", [])
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 160
            or not isinstance(external_id, str)
            or not 1 <= len(external_id) <= 255
        ):
            raise ValueError("Group schema, displayName and externalId required")
        members = _members(session, provider, payload.get("members", []))
    except (ValueError, KeyError) as error:
        return _scim_error(400, str(error), "invalidValue")
    group = FederatedGroup(
        tenant_id=provider.tenant_id,
        provider=key,
        issuer=provider.issuer,
        external_id=external_id,
        external_id_hash=subject_hash(external_id),
        display_name=name.strip(),
        members_json=json.dumps(sorted(members)),
    )
    session.add(group)
    try:
        session.flush()
        record_audit_event(
            session,
            tenant_id=provider.tenant_id,
            actor_id=None,
            action="scim.group_created",
            resource_type="group",
            resource_id=group.id,
            details={"provider": key},
        )
        result = _resource(session, provider, group)
        session.commit()
    except IntegrityError:
        session.rollback()
        return _scim_error(409, "Group already exists", "uniqueness")
    from fastapi.responses import JSONResponse

    return JSONResponse(
        result,
        status_code=201,
        media_type="application/scim+json",
        headers={"Location": f"{request.app.state.public_url}/scim/v2/{key}/Groups/{group.id}"},
    )


@router.api_route("/{key}/Groups/{group_id}", methods=["GET", "PUT", "PATCH", "DELETE"])
async def group_resource(key: str, group_id: UUID, request: Request, session: DatabaseSession):
    provider = _scim_auth(request, key, session)
    if request.method != "GET":
        # Serialize group updates so add/remove never overwrite a concurrent update.
        session.execute(
            update(FederatedGroup)
            .where(
                FederatedGroup.id == group_id,
                FederatedGroup.tenant_id == provider.tenant_id,
                FederatedGroup.provider == key,
                FederatedGroup.issuer == provider.issuer,
            )
            .values(display_name=FederatedGroup.display_name)
        )
    group = session.scalar(_groups(provider).where(FederatedGroup.id == group_id))
    if group is None:
        return _scim_error(404, "Group not found")
    if request.method == "GET":
        return _resource(session, provider, group)
    if request.method == "DELETE":
        session.delete(group)
    else:
        payload = await _body(request)
        if isinstance(payload, Response):
            return payload
        members, name = set(json.loads(group.members_json)), group.display_name
        try:
            if request.method == "PUT":
                if (
                    SCIM_GROUP not in payload.get("schemas", [])
                    or payload.get("externalId", group.external_id) != group.external_id
                ):
                    raise ValueError("Group schema required; externalId is immutable")
                name = payload.get("displayName")
                members = _members(session, provider, payload.get("members", []))
            else:
                operations = payload.get("Operations")
                if (
                    SCIM_PATCH not in payload.get("schemas", [])
                    or not isinstance(operations, list)
                    or not 1 <= len(operations) <= 20
                ):
                    raise ValueError("Invalid PATCH operations")
                for operation in operations:
                    if not isinstance(operation, dict):
                        raise ValueError("Invalid PATCH operation")
                    op, path, value = (
                        str(operation.get("op", "")).lower(),
                        operation.get("path"),
                        operation.get("value"),
                    )
                    if path is None and op in {"add", "replace"} and isinstance(value, dict):
                        if set(value) - {"displayName", "members"}:
                            raise ValueError("Unsupported attribute")
                        if "displayName" in value:
                            name = value["displayName"]
                        if "members" in value:
                            incoming = _members(session, provider, value["members"])
                            members = members | incoming if op == "add" else incoming
                    elif path == "displayName" and op in {"add", "replace"}:
                        name = value
                    elif path == "members" and op in {"add", "replace", "remove"}:
                        incoming = _members(session, provider, value or [])
                        members = (
                            members | incoming
                            if op == "add"
                            else incoming
                            if op == "replace"
                            else members - incoming
                            if value is not None
                            else set()
                        )
                    elif (
                        isinstance(path, str)
                        and op == "remove"
                        and (
                            match := re.fullmatch(r'members\[value eq "([0-9a-fA-F-]{36})"\]', path)
                        )
                    ):
                        members.discard(str(UUID(match[1])))
                    else:
                        raise ValueError("Unsupported PATCH path or operation")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160 or len(members) > 500:
                raise ValueError("Invalid group name or size")
        except ValueError as error:
            return _scim_error(400, str(error), "invalidValue")
        group.members_json, group.display_name = json.dumps(sorted(members)), name.strip()
    record_audit_event(
        session,
        tenant_id=provider.tenant_id,
        actor_id=None,
        action="scim.group_deleted" if request.method == "DELETE" else "scim.group_updated",
        resource_type="group",
        resource_id=group.id,
        details={"provider": key},
    )
    result = None if request.method == "DELETE" else _resource(session, provider, group)
    session.commit()
    return Response(status_code=204) if result is None else result


@router.get("/{key}/ResourceTypes")
def resource_types(key: str, request: Request, session: DatabaseSession):
    _scim_auth(request, key, session)
    resources = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": name,
            "name": name,
            "endpoint": f"/{name}s",
            "schema": schema,
        }
        for name, schema in (("User", SCIM_USER), ("Group", SCIM_GROUP))
    ]
    return {
        "schemas": [LIST],
        "Resources": resources,
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
    }


def _schemas():
    def attribute(name, kind="string", *, required=False, mutable="readWrite", multi=False):
        return {
            "name": name,
            "type": kind,
            "multiValued": multi,
            "required": required,
            "caseExact": name in {"id", "externalId"},
            "mutability": mutable,
            "returned": "default",
            "uniqueness": "server" if name in {"id", "userName"} else "none",
        }

    common = [
        attribute("id", mutable="readOnly"),
        attribute("externalId", required=True, mutable="immutable"),
    ]
    members = attribute("members", "complex", multi=True)
    members["subAttributes"] = [attribute("value", required=True), attribute("type")]
    return [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
            "id": SCIM_USER,
            "name": "User",
            "attributes": common
            + [attribute("userName", required=True), attribute("active", "boolean")],
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
            "id": SCIM_GROUP,
            "name": "Group",
            "attributes": common + [attribute("displayName", required=True), members],
        },
    ]


@router.get("/{key}/Schemas")
def schemas(key: str, request: Request, session: DatabaseSession):
    _scim_auth(request, key, session)
    return {
        "schemas": [LIST],
        "Resources": _schemas(),
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
    }


@router.get("/{key}/Schemas/{schema_id}")
def schema(key: str, schema_id: str, request: Request, session: DatabaseSession):
    _scim_auth(request, key, session)
    return next(
        (item for item in _schemas() if item["id"] == schema_id),
        _scim_error(404, "Schema not found"),
    )


@router.get("/{key}/ResourceTypes/{resource_id}")
def resource_type(key: str, resource_id: str, request: Request, session: DatabaseSession):
    resources = resource_types(key, request, session)["Resources"]
    return next(
        (item for item in resources if item["id"] == resource_id),
        _scim_error(404, "Resource type not found"),
    )
