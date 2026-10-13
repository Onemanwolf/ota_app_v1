from insights_testkit import digest_for

from opstriage_insights.domain.findings import AnalystOutput, Finding, Recommendation, rules_only, validate


def F(cites: tuple[str, ...], risk: str = "medium", kind: str = "trend", title: str = "t") -> Finding:
    return Finding(kind, title, "detail", risk, "high", cites)  # type: ignore[arg-type]


async def test_uncited_and_unknown_citations_are_dropped() -> None:
    d = await digest_for("planted-disk-trend")
    out = AnalystOutput("s", (F(()), F(("M99",)), F(("M1", "x1")), F(("m1",), title="kept")), ())
    v = validate(out, d)
    model = [f for f in v.findings if f.source == "model"]
    assert [f.title for f in model] == ["kept"] and model[0].citations == ("M1",)
    assert v.dropped_uncited == 3


async def test_critical_needs_a_breach_or_active_problem() -> None:
    d = await digest_for("planted-disk-trend")  # B1 cites M1
    v = validate(
        AnalystOutput("s", (F(("M1",), "critical", title="a"), F(("B1",), "critical", title="b")), ()), d
    )
    by_title = {f.title: f for f in v.findings if f.source == "model"}
    assert by_title["a"].risk == "high" and by_title["a"].notes and by_title["b"].risk == "critical"
    assert v.downgraded == 1


async def test_rule_findings_are_added_unless_the_model_cites_the_breach() -> None:
    d = await digest_for("planted-under-min-isr")
    alone = validate(AnalystOutput("s", (), ()), d)
    assert {f.source for f in alone.findings} == {"rule"} and len(alone.findings) == len(d.breaches)
    assert alone.findings[0].risk == "critical"  # under-min-ISR is a critical rule
    cites_b1 = validate(AnalystOutput("s", (F(("B1",), "critical"),), ()), d)
    assert sum(1 for f in cites_b1.findings if f.source == "rule") == len(d.breaches) - 1


async def test_forecasts_come_from_code_not_the_model() -> None:
    d = await digest_for("planted-disk-trend")
    v = validate(AnalystOutput("s", (F(("M1",), "high", "potential_issue"),), ()), d)
    f = next(f for f in v.findings if f.source == "model")
    assert f.forecasts and "above 90" in f.forecasts[0] and "days" in f.forecasts[0]


async def test_automated_action_recommendations_are_dropped() -> None:
    d = await digest_for("planted-injected-log")
    recs = (
        Recommendation("Restart all brokers immediately", "fixes everything", "low", ("M1",)),
        Recommendation("Automatically restart kafka-0 when URP > 0", "x", "low", ("M1",)),
        Recommendation("Delete the topic payments", "x", "low", ("M1",)),
        Recommendation("Investigate why kafka-0 followers lag", "URP persists", "medium", ("M1", "B1"), (0,)),
        Recommendation("Uncited idea", "x", "low", ("Z9",)),
    )
    v = validate(AnalystOutput("s", (F(("M1",)),), recs), d)
    assert [r.title for r in v.recommendations] == ["Investigate why kafka-0 followers lag"]
    assert v.dropped_actions == 3 and v.recommendations[0].addresses == (0,)


async def test_rules_only_report_when_the_analyst_is_down() -> None:
    d = await digest_for("planted-handler-saturation")
    v = rules_only(d, "analyst unavailable: LlmUnavailable")
    assert not v.analyst_available and v.findings and all(f.source == "rule" for f in v.findings)
    assert v.recommendations == () and "unavailable" in v.notes[0]
