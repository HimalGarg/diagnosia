import json

from backend.diagnofast import agent


def test_report_receives_all_specialty_answers_and_original_conversation(monkeypatch):
    form = agent.load_form('orthopedics')
    answers = {field['key']: f"Answer {i}: uncertain, no fever" for i, field in enumerate(form['fields'])}
    conversation = [{'question': 'What happened?', 'answer': 'Pain after walking, unsure when it began; no fever.'}]
    records = {'is_demo_data': True, 'conditions': ['Demo condition'], 'prescriptions': ['Demo medicine'], 'lab_reports': []}
    captured = {}

    def fake(system, content, schema, **kwargs):
        captured.update(system=system, request=json.loads(content), schema=schema)
        return agent.Report(chief_complaint='Pain', present_illness='Reported pain', highlights=[], current_medicines=[], allergies=[], past_history='', gaps=['Onset uncertain'])

    monkeypatch.setattr(agent.llm, 'ask', fake)
    agent.write_report(form, answers, conversation, records)
    assert list(captured['request']['form_answers'].values()) == list(answers.values())
    assert captured['request']['conversation'] == conversation
    assert captured['request']['past_records'] == records
    for instruction in ('explicit negatives', 'uncertainty', 'contradictions', 'DEMO DATA', 'never verified', 'not orders'):
        assert instruction in captured['system']


def test_report_accepts_more_than_eight_findings_and_old_reports():
    base = dict(chief_complaint='Pain', present_illness='Reported', highlights=[], current_medicines=[], allergies=[], past_history='', gaps=[])
    old = agent.Report(**base)
    assert old.review_of_systems == [] and old.suggested_investigations == []
    row = dict(system='MSK', parameter='Location', finding='Patient unsure', flag='NOT_ASSESSED', norm='Not applicable (symptom)')
    assert len(agent.Report(**base, review_of_systems=[row] * 20).review_of_systems) == 20
