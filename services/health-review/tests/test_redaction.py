import pytest

from opstriage_insights.domain.redaction import Redactor

R = Redactor()


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "expected_fragment"),
    [
        (
            'sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="svc" '
            'password="S3cr3t!";',
            "S3cr3t",
            "sasl.jaas.config=<redacted>",
        ),
        ("ssl.keystore.password=hunter2 loaded", "hunter2", "ssl.keystore.password=<redacted>"),
        ("Authorization: Bearer abc.def-123_xyz==", "abc.def", "Bearer <token>"),
        ("token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGci", "<"),
        ("api_key: 'k-12345'", "k-12345", "api_key: <redacted>"),
        ("client_secret=abcdef, next", "abcdef", "client_secret=<redacted>, next"),
        (
            "dt0c01.ABCDEFGHIJKLMNOPQRSTUVWX.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUV",
            "ABCDEFGHIJ",
            "<token>",
        ),
        ("order placed by jane.doe@example.com", "jane.doe", "<email>"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc", "<private-key>"),
        ("session 0123456789abcdef0123456789abcdef expired", "0123456789abcdef0123", "<hex>"),
        ("peer fe80:0:0:0:200:f8ff:fe21:67cf closed", "fe21", "<ipv6>"),
    ],
)
def test_secrets_and_personal_data_are_removed(
    raw: str, must_not_contain: str, expected_fragment: str
) -> None:
    out = R(raw)
    assert must_not_contain not in out
    assert expected_fragment in out


def test_ipv4_keeps_the_24_network() -> None:
    out = R("Failed authentication with /10.42.3.17 (SSL handshake failed)")
    assert out == "Failed authentication with /10.42.3.x (SSL handshake failed)"


def test_ordinary_kafka_lines_are_left_alone() -> None:
    line = (
        "[ReplicaManager broker=1] Error processing append operation on partition payments-2 "
        "org.apache.kafka.common.errors.NotEnoughReplicasException: The size of the current ISR Set(1) is "
        "insufficient to satisfy the min.isr requirement of 2 for partition payments-2"
    )
    assert R(line) == line


def test_counts_name_the_rules_never_the_values() -> None:
    result = R.redact("password=x token=y user a@b.io from 10.0.0.1")
    assert result.counts == {"secret_assignment": 2, "email": 1, "ipv4": 1}
    assert "x" not in result.counts and all(isinstance(v, int) for v in result.counts.values())
