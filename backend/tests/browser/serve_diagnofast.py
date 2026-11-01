"""Synthetic DiagnoFast browser fixture. Never run against a real database."""
import os
import sys

if 'test-run' not in os.environ.get('DIAGNOSIA_DB', ''):
    sys.exit('Use a throwaway DIAGNOSIA_DB containing test-run.')

import uvicorn
from backend import app as app_module, llm, speech
from backend.config import settings
from backend.diagnofast.agent import Report, ReviewOfSystem, SuggestedChip, Turn, FieldUpdate
import json

settings.neural_tts_enabled = False
settings.diagnofast_kiosk_doctor_id = '1'
settings.groq_api_key = settings.gemini_api_key = ''
settings.document_key_file = ''
app_module.get_medicine_list = lambda: None
speech.transcribe = lambda *args: 'Synthetic knee pain for three days'

def unavailable(*args):
    raise speech.SpeechUnavailable('Synthetic fallback')

speech.speak = unavailable

def scripted(system, content, schema, **kwargs):
    request = json.loads(content)
    if schema is Turn:
        conversation = request['conversation']
        if 'chest pain' in conversation[-1]['answer'].lower():
            return Turn(updates=[], next_question='', finished=True, urgent_symptoms=['Chest pain'])
        finished = len(conversation) >= 2
        return Turn(updates=[FieldUpdate(key=field['key'], value='Patient reported: see transcript') for field in request['form_fields']] if finished else [],
            next_question='Thank you for sharing. When did this begin?', finished=finished, urgent_symptoms=[],
            suggested_chips=[] if finished else [SuggestedChip(label=label, value=label) for label in ['Today', '2-3 days', '1 week']])
    return Report(chief_complaint='Knee discomfort for three days',
        present_illness='Synthetic patient reports knee discomfort after walking. No measured observations were supplied.',
        highlights=['Review weight-bearing ability with the patient.'], current_medicines=['None reported'],
        allergies=['No known medicine allergies reported'], past_history='No long-term conditions reported.', gaps=['Examination and measured vital signs pending.'],
        review_of_systems=[ReviewOfSystem(system='Musculoskeletal', parameter='Knee pain', finding='Patient reports discomfort for three days', flag='NOT_ASSESSED', norm='Not applicable (symptom)'),
            ReviewOfSystem(system='Constitutional', parameter='Fever', finding='Patient denies fever', flag='NORMAL', norm='Explicit negative history')],
        suggested_investigations=['Consider targeted imaging after clinical examination, if indicated'])

llm.ask = scripted
uvicorn.run(app_module.app, host='127.0.0.1', port=int(sys.argv[1]), log_level='error')
