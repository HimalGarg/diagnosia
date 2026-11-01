"""Self-service routing, confirmation and speech fallbacks use only synthetic patients."""
import asyncio
import base64
import json
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from backend import llm, speech, settings_store
from backend.config import settings
from backend.db import connect
from backend.diagnofast import agent, routes
from backend.tests.test_diagnofast import (
    ALL_REQUIRED, ScriptedAI, create_intake, doctor_session, services, start, turn,
)


def configure_kiosk(doctor, monkeypatch):
    monkeypatch.setattr(settings, 'diagnofast_kiosk_doctor_id', str(doctor.id))


BODY = {'form_id': 'cardiology', 'patient_name': 'Synthetic Visitor', 'consent': True}


def test_disabled_kiosk_exposes_nothing(client):
    assert client.get('/api/diagnofast/kiosk').status_code == 503
    assert client.post('/api/diagnofast/kiosk/intakes', json=BODY).status_code == 503


def test_kiosk_has_six_bilingual_forms(client, doctor, monkeypatch):
    configure_kiosk(doctor, monkeypatch)
    response = client.get('/api/diagnofast/kiosk')
    assert {form['id'] for form in response.json()['forms']} == {
        'general_physician', 'dermatologist', 'cardiology', 'orthopedics', 'ent', 'pediatrics',
    }
    for summary in response.json()['forms']:
        form = agent.load_form(summary['id'])
        assert len([field for field in form['fields'] if field['required']]) <= agent.MAX_QUESTIONS
        assert len({field['key'] for field in form['fields']}) == len(form['fields'])
        assert all(set(field['question']) == {'en-IN', 'hi-IN'} for field in form['fields'])


def test_anonymous_kiosk_visit_reaches_only_configured_doctor(client, doctor, person, monkeypatch, services):
    configure_kiosk(doctor, monkeypatch)
    client.cookies.clear()
    made = client.post('/api/diagnofast/kiosk/intakes', json={**BODY, 'patient_age': 0}).json()
    token = parse_qs(urlparse(made['link']).query)['token'][0]
    monkeypatch.setattr(llm, 'ask', ScriptedAI([turn(urgent=['Chest pain'])]))
    assert start(client, token).status_code == 200
    done = client.post(f'/api/diagnofast/patient/{token}/answer', data={'text': 'Chest pain'})
    assert done.json()['urgent'] is True
    assert client.get(f'/api/diagnofast/patient/{token}').status_code == 404
    assert client.get(f'/api/diagnofast/intakes/{made["id"]}').status_code == 401
    detail = doctor.get(f'/api/diagnofast/intakes/{made["id"]}').json()
    assert detail['patient']['age'] == 0
    assert detail['created_at']
    assert detail['patient']['record_id'] is None
    stranger = person('doctor')
    assert stranger.get(f'/api/diagnofast/intakes/{made["id"]}').status_code == 404
    db = connect()
    row = db.execute('SELECT * FROM intakes WHERE id = ?', (made['id'],)).fetchone()
    db.close()
    assert row['doctor_id'] == doctor.id and row['token_hash'] != token


@pytest.mark.parametrize('extra', [
    {'doctor_id': 999}, {'patient_id': 'someone-elses-chart'}, {'abha_id': '12345'},
    {'form_id': '../../forms/general_physician'}, {'form_id': 'no-such-form'},
    {'consent': False}, {'patient_name': '   '}, {'patient_age': 131},
])
def test_kiosk_rejects_unsafe_or_invalid_requests(client, doctor, monkeypatch, extra):
    configure_kiosk(doctor, monkeypatch)
    assert client.post('/api/diagnofast/kiosk/intakes', json={**BODY, **extra}).status_code == 422
    assert doctor.get('/api/diagnofast/intakes').json() == []


@pytest.mark.parametrize('status', ['pending_verification', 'suspended', 'rejected'])
def test_kiosk_closes_if_destination_doctor_loses_verification(client, doctor, monkeypatch, status):
    configure_kiosk(doctor, monkeypatch)
    db = connect()
    db.execute('UPDATE users SET account_status = ? WHERE id = ?', (status, doctor.id))
    db.commit()
    db.close()
    assert client.get('/api/diagnofast/kiosk').status_code == 503
    assert client.post('/api/diagnofast/kiosk/intakes', json=BODY).status_code == 503


def test_kiosk_respects_tool_switch(client, doctor, monkeypatch):
    configure_kiosk(doctor, monkeypatch)
    db = connect()
    db.execute("INSERT INTO platform_settings (key, value, updated_by, updated_at) VALUES ('tools.diagnofast', 'false', ?, 'now')", (doctor.id,))
    db.commit()
    db.close()
    assert client.get('/api/diagnofast/kiosk').status_code == 403
    assert client.post('/api/diagnofast/kiosk/intakes', json=BODY).status_code == 403


def test_preview_does_not_advance_or_store_answer(client, details, services):
    doctor_session(client, details)
    intake_id, token = create_intake(client)
    path = f'/api/diagnofast/patient/{token}/transcribe'
    audio = {'audio': ('a.webm', b'synthetic-audio', 'audio/webm')}
    assert client.post(path, files=audio).status_code == 409
    start(client, token)
    response = client.post(path, files=audio)
    assert response.json()['text'] == 'मुझे बुखार है'
    detail = client.get(f'/api/diagnofast/intakes/{intake_id}').json()
    assert detail['transcript'] == [] and detail['status'] == 'in_progress'
    assert not list(settings.documents_dir.rglob('*.webm.enc'))


@pytest.mark.parametrize('payload, mime, expected', [(b'', 'audio/webm', 422), (b'MZ', 'application/octet-stream', 422), (b'x' * (routes.MAX_AUDIO_BYTES + 1), 'audio/webm', 413)])
def test_preview_validates_upload(client, details, services, payload, mime, expected):
    doctor_session(client, details)
    _, token = create_intake(client)
    start(client, token)
    assert client.post(f'/api/diagnofast/patient/{token}/transcribe', files={'audio': ('a', payload, mime)}).status_code == expected


@pytest.mark.parametrize('transcript', ['', ' ' * 5, 'x' * 2001])
def test_unusable_preview_is_not_presented(client, details, services, monkeypatch, transcript):
    doctor_session(client, details)
    _, token = create_intake(client)
    start(client, token)
    monkeypatch.setattr(speech, 'transcribe', lambda *args: transcript)
    assert client.post(f'/api/diagnofast/patient/{token}/transcribe', files={'audio': ('a.webm', b'synthetic', 'audio/webm')}).status_code == 422


def test_confirmed_edit_wins_over_audio_and_is_given_to_ai(client, details, services, monkeypatch):
    doctor_session(client, details)
    intake_id, token = create_intake(client)
    start(client, token)
    ai = ScriptedAI([turn(ALL_REQUIRED, finished=True)])
    monkeypatch.setattr(llm, 'ask', ai)
    monkeypatch.setattr(speech, 'transcribe', lambda *args: pytest.fail('Must not overwrite confirmed text'))
    response = client.post(f'/api/diagnofast/patient/{token}/answer',
        data={'text': 'No fever. My knee hurts.'}, files={'audio': ('a.webm', b'synthetic', 'audio/webm')})
    assert response.status_code == 200
    assert json.loads(ai.calls[0]['content'])['conversation'][-1]['answer'] == 'No fever. My knee hurts.'
    detail = client.get(f'/api/diagnofast/intakes/{intake_id}').json()
    assert detail['transcript'][0]['answer'] == 'No fever. My knee hurts.'
    assert detail['transcript'][0]['has_audio'] is True


def test_chips_follow_question_and_do_not_survive_urgent_stop():
    form = agent.load_form('general_physician')
    suggested = turn(question='How long?')
    suggested.suggested_chips = [agent.SuggestedChip(label='Today', value='Today')]
    assert agent.apply_turn(form, {}, suggested, 1, 'en-IN').suggested_chips == suggested.suggested_chips
    suggested.urgent_symptoms = ['Chest pain']
    assert agent.apply_turn(form, {}, suggested, 1, 'en-IN').suggested_chips == []
    suggested.urgent_symptoms = []
    suggested.finished = True
    fallback = agent.apply_turn(form, {'chief_complaint': 'Pain'}, suggested, 1, 'hi-IN')
    assert fallback.suggested_chips[0].label == 'आज'


def test_chip_api_payload(client, details, services, monkeypatch):
    doctor_session(client, details)
    _, token = create_intake(client)
    assert start(client, token).json()['suggested_chips'] == []
    choice = turn(question='How long?')
    choice.suggested_chips = [agent.SuggestedChip(label='Today', value='Today')]
    monkeypatch.setattr(llm, 'ask', ScriptedAI([choice]))
    reply = client.post(f'/api/diagnofast/patient/{token}/answer', data={'text': 'Knee pain'}).json()
    assert reply['suggested_chips'] == [{'label': 'Today', 'value': 'Today'}]


def test_report_schema_is_compatible_but_rejects_made_up_flag():
    from backend.tests.test_diagnofast import report
    old = report()
    assert old.review_of_systems == [] and old.suggested_investigations == []
    with pytest.raises(ValidationError):
        agent.ReviewOfSystem(system='Respiratory', parameter='Cough', finding='Not asked', flag='CLEAR', norm='None')
    with pytest.raises(ValidationError):
        agent.SuggestedChip(label='x' * 61, value='value')


@pytest.mark.parametrize('language, voice', [('en-IN', 'en-IN-NeerjaNeural'), ('hi-IN', 'hi-IN-SwaraNeural')])
def test_neural_audio_collects_only_audio_chunks(monkeypatch, language, voice):
    monkeypatch.setattr(settings, 'neural_tts_enabled', True)
    calls = []
    class FakeVoice:
        def __init__(self, text, name, **kwargs):
            calls.append((text, name, kwargs))
        async def stream(self):
            yield {'type': 'WordBoundary', 'text': 'ignored'}
            yield {'type': 'audio', 'data': b'ID3'}
            yield {'type': 'audio', 'data': b'synthetic'}
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(Communicate=FakeVoice))
    assert speech.speak_neural('Synthetic question', language) == b'ID3synthetic'
    assert calls[0][1] == voice and calls[0][2]['rate'] == '-8%'
    assert routes._spoken('Synthetic question', language).startswith('data:audio/mpeg;base64,')


def test_neural_timeout_is_bounded_and_falls_back(monkeypatch):
    monkeypatch.setattr(settings, 'neural_tts_enabled', True)
    monkeypatch.setattr(speech, 'NEURAL_TIMEOUT_SECONDS', 0.01)
    class SlowVoice:
        def __init__(self, *args, **kwargs): pass
        async def stream(self):
            await asyncio.sleep(5)
            yield {'type': 'audio', 'data': b'late'}
    monkeypatch.setitem(sys.modules, 'edge_tts', SimpleNamespace(Communicate=SlowVoice))
    monkeypatch.setattr(speech, 'speak', lambda *args: b'wav')
    assert routes._spoken('Question', 'en-IN') == 'data:audio/wav;base64,' + base64.b64encode(b'wav').decode()
    assert routes._spoken('प्रश्न', 'hi-IN') is None


def test_neural_rejects_long_text_before_network(monkeypatch):
    monkeypatch.setattr(settings, 'neural_tts_enabled', True)
    with pytest.raises(speech.SpeechRejected):
        speech.speak_neural('x' * (speech.MAX_SPEECH_CHARS + 1), 'en-IN')


def test_intake_normalization_prefers_gemini_with_groq_fallback(client, monkeypatch):
    monkeypatch.setattr(settings, 'gemini_api_key', 'synthetic-test-key')
    monkeypatch.setattr(settings, 'groq_api_key', 'synthetic-test-key')
    assert settings_store.provider_order(tool='diagnofast_turn') == ['gemini', 'groq']
