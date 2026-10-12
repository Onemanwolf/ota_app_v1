from fastapi.testclient import TestClient

from opstriage_webhook_sink.app import Settings, build_app

CARD = {
    "type": "message",
    "attachments": [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": "[P-1] <b>down</b>",
                        "weight": "Bolder",
                        "color": "Attention",
                    },
                    {"type": "FactSet", "facts": [{"title": "Action", "value": "restart_container"}]},
                ],
                "actions": [{"type": "Action.OpenUrl", "title": "Problem", "url": "https://example/p"}],
            },
        }
    ],
}


def test_stores_bounded_history_and_renders_escaped_cards() -> None:
    client = TestClient(build_app(Settings(max_requests=2)))
    for _ in range(3):
        assert client.post("/teams", json=CARD).status_code == 202
    assert len(client.get("/requests").json()) == 2
    page = client.get("/").text
    assert "&lt;b&gt;down&lt;/b&gt;" in page and "restart_container" in page
    assert client.delete("/requests").status_code == 204
    assert client.get("/requests").json() == []
