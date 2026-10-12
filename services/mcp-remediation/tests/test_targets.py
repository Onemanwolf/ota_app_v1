import pytest
from pydantic import ValidationError

from opstriage_contracts.problems import EntityRef
from opstriage_remediation.domain.targets import TargetRegistry, lower_autonomy

API = EntityRef(id="CONTAINER-8A1F00C2D3E4B5A6", type="container", name="demo-orders-api")
WORKER = EntityRef(id="CONTAINER-9B2E11D3E4F5A6B7", type="container", name="demo-orders-worker")
HOST = EntityRef(id="HOST-017198AD253CBD63", type="host", name="vm-local-demo")


def test_entity_id_match(registry: TargetRegistry) -> None:
    target, ambiguous = registry.match([API])
    assert target is not None and target.id == "demo-orders-api" and not ambiguous


def test_host_plus_container_name_match(registry: TargetRegistry) -> None:
    renamed = EntityRef(id="CONTAINER-NEWID", type="container", name="demo-orders-api")
    target, _ = registry.match([renamed, HOST])
    assert target is not None and target.id == "demo-orders-api"


def test_container_name_alone_does_not_match(registry: TargetRegistry) -> None:
    renamed = EntityRef(id="CONTAINER-NEWID", type="container", name="demo-orders-api")
    assert registry.match([renamed]) == (None, False)


def test_two_matches_is_ambiguous(registry: TargetRegistry) -> None:
    assert registry.match([API, WORKER, HOST]) == (None, True)


def test_no_match(registry: TargetRegistry) -> None:
    other = EntityRef(id="SERVICE-1", type="service", name="checkout")
    assert registry.match([other]) == (None, False)


def test_autonomy_can_only_be_lowered() -> None:
    assert lower_autonomy("autonomous", "approval_required") == "approval_required"
    assert lower_autonomy("notify_only", "autonomous") == "notify_only"


@pytest.mark.parametrize(
    "bad",
    [
        {
            "targets": [
                {
                    "id": "Bad Id",
                    "description": "d",
                    "match": {"entity_ids": ["X"]},
                    "executor": {"kind": "local_docker", "container": "c"},
                }
            ]
        },
        {
            "targets": [
                {
                    "id": "a",
                    "description": "d",
                    "match": {"container_names": ["c"]},
                    "executor": {"kind": "local_docker", "container": "c"},
                }
            ]
        },
        {
            "targets": [
                {
                    "id": "a",
                    "description": "d",
                    "match": {"entity_ids": ["X"]},
                    "executor": {"kind": "local_docker", "container": "c; rm -rf /"},
                }
            ]
        },
    ],
)
def test_registry_validation_rejects_unsafe_entries(bad: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValidationError):
        TargetRegistry.model_validate(bad)


def test_duplicate_ids_rejected(registry: TargetRegistry) -> None:
    data = registry.model_dump()
    data["targets"] = [data["targets"][0], data["targets"][0]]
    with pytest.raises(ValidationError, match="duplicate"):
        TargetRegistry.model_validate(data)
