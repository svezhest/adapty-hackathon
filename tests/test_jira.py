"""docs/agents.md, "Jira": the client against Jira's REST shapes, one issue per ticket, and who may file it."""

import json

import pytest
import responses
from fastapi.testclient import TestClient

from resolve import db, jira, triage
from resolve.llm import FakeLLM
from resolve.models import AgentConfig, BugReport, JiraConfig, JiraExport, JiraTeam
from resolve.workflow import Services
from tests.fakes_v2 import CUID, submit, ticket_row, tickets_file, triage_ticket

SITE = "https://example.atlassian.net"
ISSUE = SITE + "/rest/api/3/issue"
SEARCH = SITE + "/rest/api/3/search/jql"
TEAMS = [
    JiraTeam(name="Mobile", project_key="APP", handles="crashes and screens"),
    JiraTeam(name="Payments", project_key="PAY", handles="purchases and restore"),
]
REPORT = BugReport(team="Mobile", summary="Crash on saving a workout", details="Android, 3.6.0. Reporter jo@mail.com sees it on save.", priority="high")


def services(tmp_path, *, auto=False, teams=TEAMS, demo=False, connected=True, llm=None, max_auto=20):
    conn = db.connect(tmp_path / "jira.sqlite")
    db.seed(conn, tickets_file(tmp_path, [ticket_row()]))
    db.save_config(conn, AgentConfig(jira=JiraConfig(auto_create=auto, teams=teams, max_auto_per_day=max_auto)).model_dump())
    client = jira.Client(SITE, "me@example.com", "secret-token") if connected else None
    return Services(conn=conn, demo=demo, jira=client, llm=llm)


def proposed(svc, report=REPORT):
    ticket_id = db.list_tickets(svc.conn)[0]["id"]
    ticket = triage_ticket()
    ticket.bug_report = report
    db.update_ticket(svc.conn, ticket_id, triage_json=ticket.model_dump_json(), status="proposed")
    return ticket_id


def mock_ok(found=None):
    responses.post(SEARCH, json={"issues": [{"key": found}] if found else []})
    responses.post(ISSUE, json={"id": "1", "key": "APP-7", "self": ISSUE + "/1"}, status=201)


def sent(url):
    return [json.loads(c.request.body) for c in responses.calls if c.request.url == url]


@responses.activate
def test_creates_the_issue_in_the_team_project_and_records_it(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    ticket = jira.export(svc, ticket_id)
    assert ticket["jira"]["key"] == "APP-7" and ticket["jira"]["url"] == SITE + "/browse/APP-7"
    assert ticket["jira"]["team"] == "Mobile" and ticket["jira"]["by"] == "person" and ticket["jira_error"] is None
    fields = sent(ISSUE)[0]["fields"]
    assert fields["project"] == {"key": "APP"} and fields["issuetype"] == {"name": "Bug"}
    assert fields["summary"] == "Crash on saving a workout" and fields["priority"] == {"name": "High"}
    assert jira.label_for(ticket_id) in fields["labels"]
    assert fields["description"]["type"] == "doc" and fields["description"]["version"] == 1
    assert responses.calls[-1].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_the_users_email_never_leaves(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc, REPORT.model_copy(update={"summary": "Crash for jo@mail.com"}))
    db.update_ticket(svc.conn, ticket_id, email="jo@mail.com")
    mock_ok()
    jira.export(svc, ticket_id)
    body = responses.calls[-1].request.body.decode()
    assert "jo@mail.com" not in body and "[email removed]" in body and CUID in body


@responses.activate
def test_second_export_files_nothing(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    jira.export(svc, ticket_id)
    jira.export(svc, ticket_id)
    assert len(sent(ISSUE)) == 1


@responses.activate
def test_after_a_crash_the_label_finds_the_issue_instead_of_a_duplicate(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok(found="APP-3")
    assert jira.export(svc, ticket_id)["jira"]["key"] == "APP-3"
    assert sent(ISSUE) == []
    assert jira.label_for(ticket_id) in sent(SEARCH)[0]["jql"] and 'project = "APP"' in sent(SEARCH)[0]["jql"]


@responses.activate
def test_a_field_the_project_refuses_is_dropped(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    responses.post(SEARCH, json={"issues": []})
    responses.post(ISSUE, json={"errors": {"priority": "Field 'priority' cannot be set."}}, status=400)
    responses.post(ISSUE, json={"id": "1", "key": "APP-8"}, status=201)
    assert jira.export(svc, ticket_id)["jira"]["key"] == "APP-8"
    assert "priority" not in sent(ISSUE)[1]["fields"]


@pytest.mark.parametrize("status,body,expected", [
    (401, {}, "email or API token"),
    (400, {"errors": {"issuetype": "Specify a valid issue type"}}, "issuetype"),
    (404, {"errorMessages": ["No project could be found with key 'APP'."]}, "No project"),
])
@responses.activate
def test_a_jira_failure_is_recorded_and_can_be_retried(tmp_path, status, body, expected):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    responses.post(SEARCH, json={"issues": []})
    responses.post(ISSUE, json=body, status=status)
    ticket = jira.export(svc, ticket_id)
    assert ticket["jira"] is None and expected in ticket["jira_error"] and "secret-token" not in ticket["jira_error"]
    responses.replace(responses.POST, ISSUE, json={"id": "1", "key": "APP-9"}, status=201)
    ticket = jira.export(svc, ticket_id)
    assert ticket["jira"]["key"] == "APP-9" and ticket["jira_error"] is None


@responses.activate
def test_the_persons_edits_win(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    responses.replace(responses.POST, ISSUE, json={"id": "1", "key": "PAY-1"}, status=201)
    jira.export(svc, ticket_id, edits=JiraExport(team="Payments", summary="Restore fails", priority="low"))
    fields = sent(ISSUE)[0]["fields"]
    assert fields["project"] == {"key": "PAY"} and fields["summary"] == "Restore fails" and fields["priority"] == {"name": "Low"}


def test_unknown_team_and_no_team_are_refused(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    with pytest.raises(ValueError, match="no team named"):
        jira.export(svc, ticket_id, edits=JiraExport(team="Nobody"))
    db.update_ticket(svc.conn, ticket_id, triage_json=triage_ticket().model_dump_json())
    with pytest.raises(ValueError, match="pick a team"):
        jira.export(svc, ticket_id)


def test_without_settings_it_says_so(tmp_path):
    svc = services(tmp_path, connected=False)
    with pytest.raises(jira.NotConnected):
        jira.export(svc, proposed(svc))


def test_demo_mode_calls_nothing_and_says_so(tmp_path):
    svc = services(tmp_path, demo=True, connected=False)
    ticket = jira.export(svc, proposed(svc))  # no `responses` here: a real call would fail the test
    assert ticket["jira"]["demo"] is True and ticket["jira"].get("key") is None


def test_the_token_goes_over_https_only():
    with pytest.raises(ValueError, match="https"):
        jira.Client("http://example.atlassian.net", "a", "b")
    jira.Client("http://127.0.0.1:9000", "a", "b")  # a local test server


# --- the agent ------------------------------------------------------------------------------------


def run_triage(svc, report):
    ticket_id = db.list_tickets(svc.conn)[0]["id"]
    drafted = triage_ticket()
    drafted.bug_report = report
    svc.llm = FakeLLM([submit(drafted), submit(drafted)])
    return ticket_id, triage.run(svc, ticket_id)


@responses.activate
def test_auto_on_the_agent_files_its_report(tmp_path):
    svc = services(tmp_path, auto=True)
    mock_ok()
    _, ticket = run_triage(svc, REPORT)
    assert ticket["status"] == "proposed" and ticket["jira"]["key"] == "APP-7" and ticket["jira"]["by"] == "agent"


@responses.activate
def test_auto_off_the_report_waits_for_a_click(tmp_path):
    svc = services(tmp_path, auto=False)
    _, ticket = run_triage(svc, REPORT)
    assert ticket["triage"]["bug_report"]["team"] == "Mobile" and ticket["jira"] is None
    assert len(responses.calls) == 0


@responses.activate
def test_auto_with_no_report_files_nothing(tmp_path):
    svc = services(tmp_path, auto=True)
    _, ticket = run_triage(svc, None)
    assert ticket["jira"] is None and len(responses.calls) == 0


@responses.activate
def test_auto_stops_at_the_daily_limit_and_a_person_still_can(tmp_path):
    svc = services(tmp_path, auto=True, max_auto=0)
    ticket_id, ticket = run_triage(svc, REPORT)
    assert ticket["jira"] is None and "limit" in ticket["jira_error"] and len(responses.calls) == 0
    mock_ok()
    assert jira.export(svc, ticket_id)["jira"]["by"] == "person"


@responses.activate
def test_a_jira_failure_does_not_fail_the_triage(tmp_path):
    svc = services(tmp_path, auto=True)
    responses.post(SEARCH, json={}, status=500)
    _, ticket = run_triage(svc, REPORT)
    assert ticket["status"] == "proposed" and ticket["triage"] and ticket["jira"] is None and ticket["jira_error"]


def test_a_team_outside_the_config_gets_one_correction(tmp_path):
    svc = services(tmp_path)
    ticket_id = db.list_tickets(svc.conn)[0]["id"]
    bad, good = triage_ticket(), triage_ticket()
    bad.bug_report = REPORT.model_copy(update={"team": "Nobody"})
    good.bug_report = REPORT
    svc.llm = FakeLLM([submit(bad), submit(good)])
    ticket = triage.run(svc, ticket_id)
    assert ticket["triage"]["bug_report"]["team"] == "Mobile"
    assert "bug_report.team must be one of" in json.dumps(svc.llm.calls[-1]["messages"])


def test_with_no_teams_a_report_is_dropped_without_a_correction(tmp_path):
    svc = services(tmp_path, teams=[])
    _, ticket = run_triage(svc, REPORT)
    assert ticket["triage"]["bug_report"] is None and len(svc.llm.calls) == 1


def test_the_model_sees_the_teams_and_not_the_project_keys():
    facts = triage.config_facts(AgentConfig(jira=JiraConfig(teams=TEAMS)))
    assert facts["teams"][0] == {"name": "Mobile", "handles": "crashes and screens"}


# --- API ------------------------------------------------------------------------------------------


def api_client(tmp_path, connected=True, demo=False):
    from resolve.web import create_app

    seed = tickets_file(tmp_path, [ticket_row()])
    client = jira.Client(SITE, "me@example.com", "secret-token") if connected else None
    app = create_app(tmp_path / "api.sqlite", demo=demo, seed_file=seed, jira_client=client)
    svc = app.state.services
    db.save_config(svc.conn, AgentConfig(jira=JiraConfig(teams=TEAMS)).model_dump())
    return TestClient(app), svc


@responses.activate
def test_api_files_and_shows_the_issue(tmp_path):
    http, svc = api_client(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    body = http.post(f"/api/tickets/{ticket_id}/jira", json={"priority": "low"}).json()
    assert body["jira"]["key"] == "APP-7" and http.get(f"/api/tickets/{ticket_id}").json()["jira"]["key"] == "APP-7"


def test_api_statuses(tmp_path):
    http, svc = api_client(tmp_path, connected=False)
    ticket_id = proposed(svc)
    assert http.post("/api/tickets/nope/jira", json={}).status_code == 404
    assert http.post(f"/api/tickets/{ticket_id}/jira", json={}).status_code == 409
    assert http.post(f"/api/tickets/{ticket_id}/jira", json={"team": "Nobody"}).status_code == 422


def test_api_info_never_has_the_token(tmp_path):
    http, _ = api_client(tmp_path)
    info = http.get("/api/jira")
    assert info.json() == {"connected": True, "site": SITE, "demo": False} and "secret-token" not in info.text


def test_config_refuses_bad_teams(tmp_path):
    http, _ = api_client(tmp_path)
    config = http.get("/api/config").json()
    config["jira"]["teams"] = [{"name": "A", "project_key": "not a key"}]
    assert http.put("/api/config", json=config).status_code == 422
    config["jira"]["teams"] = [{"name": "A", "project_key": "AA"}, {"name": "A", "project_key": "BB"}]
    assert http.put("/api/config", json=config).status_code == 422


def test_an_old_stored_config_still_loads(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite")
    stored = AgentConfig().model_dump()
    del stored["jira"]
    db.save_config(conn, stored)
    from resolve import policy

    assert policy.load_config(conn).jira.teams == []


# --- filed with the reply -------------------------------------------------------------------------


def accept(svc, ticket_id, **decision):
    from resolve import workflow
    from resolve.models import Decision

    return workflow.accept(svc, ticket_id, Decision(option=0, **decision))


@responses.activate
def test_sending_the_reply_files_the_report(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    ticket = accept(svc, ticket_id)
    assert ticket["status"] == "done" and ticket["jira"]["key"] == "APP-7" and ticket["jira"]["by"] == "person"


@responses.activate
def test_the_persons_edits_go_with_the_reply(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    accept(svc, ticket_id, jira=JiraExport(team="Payments"))
    assert sent(ISSUE)[0]["fields"]["project"] == {"key": "PAY"}


@responses.activate
def test_a_jira_failure_never_undoes_the_reply(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    responses.post(SEARCH, json={}, status=500)
    ticket = accept(svc, ticket_id)
    assert ticket["status"] == "done" and ticket["reply_sent_at"] and ticket["jira"] is None and ticket["jira_error"]


@responses.activate
def test_a_ticket_with_no_report_files_nothing_on_reply(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc, None)
    assert accept(svc, ticket_id)["jira"] is None and len(responses.calls) == 0


@responses.activate
def test_an_issue_already_filed_is_not_filed_again_on_reply(tmp_path):
    svc = services(tmp_path)
    ticket_id = proposed(svc)
    mock_ok()
    jira.export(svc, ticket_id)
    accept(svc, ticket_id)
    assert len(sent(ISSUE)) == 1


def test_without_jira_the_model_is_not_offered_teams_and_a_report_is_dropped(tmp_path):
    svc = services(tmp_path, connected=False)
    _, ticket = run_triage(svc, REPORT)
    assert ticket["triage"]["bug_report"] is None and len(svc.llm.calls) == 1
    assert '"teams": []' in json.dumps(svc.llm.calls[0]["messages"]).replace('\\"', '"')
