from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from app.schemas.alert import AlertCreate
from scripts import send_alert

FIXED_EVENT_ID = UUID("00000000-0000-4000-8000-000000000123")
FIXED_TIMESTAMP = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "controlled-edge-device"
DEVICE_KEY = "edk_private-test-value"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class RecordingPost:
    def __init__(self, response_factory=None):
        self.calls = []
        self.response_factory = response_factory or self._success

    @staticmethod
    def _success(*, json, **_kwargs):
        return FakeResponse(
            200,
            {
                "id": 123,
                "event_id": json["event_id"],
                "delivery_status": "stored",
                "push_status": "pending",
            },
        )

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response_factory(**kwargs)


@pytest.fixture
def credentials():
    return send_alert.DeviceCredentials(device_id=DEVICE_ID, device_key=DEVICE_KEY)


@pytest.fixture
def script_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(send_alert, "REPOSITORY_ROOT", tmp_path)
    (tmp_path / ".env.local.device").write_text(
        f"ECHOSENSE_DEVICE_ID={DEVICE_ID}\nECHOSENSE_DEVICE_KEY={DEVICE_KEY}\n",
        encoding="utf-8",
    )
    return {}


def _payload(credentials, severity="LOW", trigger_type="KEYWORD", transcript=None):
    return send_alert.build_alert_payload(
        severity,
        trigger_type,
        transcript,
        credentials,
        FIXED_EVENT_ID,
        FIXED_TIMESTAMP,
    )


@pytest.mark.parametrize("severity", ["LOW", "MEDIUM", "HIGH"])
def test_all_schema_severities_are_accepted(credentials, severity):
    payload = _payload(credentials, severity=severity)

    assert payload["severity"] == severity
    assert AlertCreate.model_validate(payload).severity.value == severity


@pytest.mark.parametrize("severity", ["CRITICAL", "", "HIGHER"])
def test_invalid_severity_is_rejected(credentials, severity):
    with pytest.raises(send_alert.ControlledAlertError, match="severity must be one of"):
        _payload(credentials, severity=severity)


@pytest.mark.parametrize("trigger_type", ["KEYWORD", "ACOUSTIC", "TEST"])
def test_all_current_schema_trigger_types_are_accepted(credentials, trigger_type):
    payload = _payload(credentials, trigger_type=trigger_type)

    assert payload["trigger_type"] == trigger_type
    assert payload["test_mode"] is (trigger_type == "TEST")
    assert AlertCreate.model_validate(payload).trigger_type.value == trigger_type


@pytest.mark.parametrize("trigger_type", ["VOICE", "", "MANUAL"])
def test_invalid_trigger_type_is_rejected(credentials, trigger_type):
    with pytest.raises(send_alert.ControlledAlertError, match="trigger_type must be one of"):
        _payload(credentials, trigger_type=trigger_type)


def test_acoustic_alert_does_not_require_or_invent_transcript(credentials):
    payload = _payload(credentials, severity="MEDIUM", trigger_type="ACOUSTIC")

    assert "transcribed_text" not in payload
    assert payload["transcription_status"] == "not_attempted"


def test_optional_transcript_uses_supported_contract_field(credentials):
    payload = _payload(
        credentials,
        severity="HIGH",
        trigger_type="KEYWORD",
        transcript="pangit ka",
    )

    assert payload["transcribed_text"] == "pangit ka"
    assert payload["transcription_status"] == "complete"
    assert "waveform_snapshot" not in payload


def test_one_uuid_populates_event_and_idempotency_headers(script_environment):
    generated = []

    def uuid_factory():
        generated.append(FIXED_EVENT_ID)
        return FIXED_EVENT_ID

    post = RecordingPost()
    result = send_alert.run(
        ["HIGH", "KEYWORD"],
        environ=script_environment,
        post=post,
        uuid_factory=uuid_factory,
        now_factory=lambda: FIXED_TIMESTAMP,
    )

    assert result == 0
    assert generated == [FIXED_EVENT_ID]
    assert len(post.calls) == 1
    _, request = post.calls[0]
    assert UUID(request["json"]["event_id"]) == FIXED_EVENT_ID
    assert request["headers"] == {
        "X-EchoSense-Device-Id": DEVICE_ID,
        "X-EchoSense-Device-Key": DEVICE_KEY,
        "Idempotency-Key": str(FIXED_EVENT_ID),
    }


def test_default_backend_is_local_and_only_one_alert_is_submitted(script_environment, capsys):
    post = RecordingPost()

    result = send_alert.run(
        ["LOW", "TEST"],
        environ=script_environment,
        post=post,
        uuid_factory=lambda: FIXED_EVENT_ID,
        now_factory=lambda: FIXED_TIMESTAMP,
    )

    assert result == 0
    assert len(post.calls) == 1
    assert post.calls[0][0] == "http://127.0.0.1:8000/alerts/"
    assert "Backend: local" in capsys.readouterr().out


def test_nonlocal_backend_fails_closed_without_explicit_override(script_environment):
    environment = {
        **script_environment,
        "ECHOSENSE_TEST_BACKEND_URL": "https://production.example",
    }
    post = RecordingPost()

    with pytest.raises(send_alert.ControlledAlertError, match="Refusing a non-local backend"):
        send_alert.run(["HIGH", "KEYWORD"], environ=environment, post=post)

    assert post.calls == []


def test_failure_output_never_prints_device_key(script_environment, capsys):
    def rejected(**_kwargs):
        return FakeResponse(422, {"detail": f"bad credential {DEVICE_KEY}"})

    result = send_alert.run(
        ["HIGH", "KEYWORD"],
        environ=script_environment,
        post=RecordingPost(rejected),
        uuid_factory=lambda: FIXED_EVENT_ID,
        now_factory=lambda: FIXED_TIMESTAMP,
    )

    captured = capsys.readouterr()
    assert result == 1
    assert DEVICE_KEY not in captured.out
    assert DEVICE_KEY not in captured.err
    assert "API contract validation failed" in captured.out


def test_backend_failure_is_not_retried(script_environment, capsys):
    attempts = []

    def unavailable(url, **_kwargs):
        attempts.append(url)
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    result = send_alert.run(
        ["MEDIUM", "ACOUSTIC"],
        environ=script_environment,
        post=unavailable,
        uuid_factory=lambda: FIXED_EVENT_ID,
        now_factory=lambda: FIXED_TIMESTAMP,
    )

    assert result == 1
    assert attempts == ["http://127.0.0.1:8000/alerts/"]
    assert "no retry attempted" in capsys.readouterr().out


@pytest.mark.parametrize("status_code", [401, 403])
def test_unregistered_or_disabled_device_fails_closed(script_environment, capsys, status_code):
    post = RecordingPost(
        lambda **_kwargs: FakeResponse(status_code, {"detail": "Invalid device credentials"})
    )

    result = send_alert.run(
        ["HIGH", "KEYWORD"],
        environ=script_environment,
        post=post,
        uuid_factory=lambda: FIXED_EVENT_ID,
        now_factory=lambda: FIXED_TIMESTAMP,
    )

    assert result == 1
    assert len(post.calls) == 1
    assert "DEVICE REGISTRATION REQUIRED" in capsys.readouterr().out


def test_missing_credentials_stops_before_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(send_alert, "REPOSITORY_ROOT", tmp_path)
    post = RecordingPost()

    with pytest.raises(send_alert.DeviceRegistrationRequired, match="DEVICE REGISTRATION REQUIRED"):
        send_alert.run(["HIGH", "KEYWORD"], environ={}, post=post)

    assert post.calls == []




# cd ~/echosense-backend
# ./scripts/send_alert.sh HIGH KEYWORD "pangit ka"