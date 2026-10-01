from src.schemas import AgentTaskNextResponse, DebateStatus


def test_tasks_next_response_includes_polling_contract_flags():
    resp = AgentTaskNextResponse(
        ok=True,
        debate_status=DebateStatus.OPENING,
        task=None,
        terminal=False,
    )

    data = resp.model_dump()
    assert data["must_continue_polling"] is True
    assert data["stop_only_if_terminal"] is True
