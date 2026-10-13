from pathlib import Path

import pytest
from insights_testkit import STORE
from pydantic import ValidationError

from opstriage_insights.adapters.cluster_config import fixture_clusters, load_clusters

REPO = Path(__file__).resolve().parents[3]


def test_example_config_loads() -> None:
    clusters = load_clusters(REPO / "config" / "insights.example.yaml")
    c = clusters["prod-kafka-01"]
    assert c.namespace == "streaming" and c.containers == ("kafka",) and len(c.entity_ids) == 3


def test_missing_file_means_no_configured_clusters(tmp_path: Path) -> None:
    assert load_clusters(tmp_path / "absent.yaml") == {}


@pytest.mark.parametrize(
    "body", ["clusters: [{name: '../etc'}]", "clusters: [{name: a, extra: 1}]", "other: 1"]
)
def test_invalid_config_is_rejected(tmp_path: Path, body: str) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(body)
    with pytest.raises(ValidationError):
        load_clusters(f)


def test_fixture_clusters_carry_their_scope() -> None:
    scopes = fixture_clusters(STORE)
    assert len(scopes) == 16 and scopes["healthy-steady"].namespace == "streaming"
    assert len(scopes["healthy-steady"].entity_ids) == 5
