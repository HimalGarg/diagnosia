// ClinScribe: capture a consultation, review the draft, sign the note.
import { act, api, badge, form, h, plural, section, show, table, when } from './ui.js';
import { needPatient } from './overview.js';

const BASE = '/api/addons/clinscribe';
const CHUNK_MS = 25000; // the transcript updates about this often
const VERDICT_WORDS = {
    supported: 'Matches the transcript',
    uncertain: 'Please check',
    unsupported: 'Not found in the transcript',
    clinician_entered: 'Written by you',
};

const MIN_CHUNK_BYTES = 2000;
const MIC_PROBLEMS = {
    NotAllowedError: 'Microphone permission was refused. Allow it in the browser, or type the lines below.',
    NotFoundError: 'No microphone was found. Connect one, or type the lines below.',
    NotReadableError: 'The microphone is in use by another app. Close it, or type the lines below.',
    SecurityError: 'This page is not allowed to use the microphone. It needs HTTPS.',
};

let recorder = null;

export function micProblem(error) {
    return MIC_PROBLEMS[error?.name] || 'The microphone could not start. Type the lines below instead.';
}

// Leaving the tool mid-recording must not leave the record saying it's still on.
export async function leaveRecording(context) {
    const sessionId = await stopMicrophone();
    if (sessionId) {
        await act(() => api(`${BASE}/sessions/${sessionId}/state`, { method: 'POST', body: { state: 'paused' } }), context);
    }
}

// Ends capture and waits for the last chunk, so the final words aren't lost.
async function stopMicrophone() {
    const current = recorder;
    if (!current) return null;
    recorder = null;
    current.active = false;
    clearTimeout(current.timer);
    const finished = new Promise((resolve) => { current.finish = resolve; });
    if (current.media?.state === 'recording') current.media.stop();
    else current.finish();
    await finished;
    current.stream?.getTracks().forEach((track) => track.stop());
    await Promise.allSettled(current.uploads);
    return current.sessionId;
}

async function startMicrophone(session, speakerControl, handlers, context) {
    // Claimed before the slow permission step, so Stop works while it is still opening.
    const current = { stream: null, active: true, media: null, timer: null, uploads: [], finish: null, sessionId: session.id };
    recorder = current;
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (problem) {
        if (recorder === current) recorder = null;
        throw problem;
    }
    if (!current.active) {
        stream.getTracks().forEach((track) => track.stop());
        return;
    }
    current.stream = stream;
    const upload = async (blob) => {
        const data = new FormData();
        data.append('audio', blob, 'chunk.webm');
        data.append('speaker', speakerControl.value);
        const saved = await act(() => api(`${BASE}/sessions/${session.id}/transcribe`, { method: 'POST', form: data }), context);
        if (saved === undefined) handlers.onLost();
        else if (saved.segment) handlers.onSegment();
    };
    const nextChunk = () => {
        if (!current.active) return;
        const media = new MediaRecorder(stream);
        const parts = [];
        media.ondataavailable = (event) => parts.push(event.data);
        media.onstop = () => {
            const blob = new Blob(parts, { type: media.mimeType || 'audio/webm' });
            nextChunk();
            if (blob.size >= MIN_CHUNK_BYTES) current.uploads.push(upload(blob));
            if (!current.active) current.finish?.();
        };
        current.media = media;
        media.start();
        current.timer = setTimeout(() => media.state === 'recording' && media.stop(), CHUNK_MS);
    };
    nextChunk();
}

function transcriptView(session) {
    const list = h('div', { class: 'transcript sheet', 'aria-live': 'polite' });
    const fill = (segments) => {
        show(list, ...(segments.length
            ? segments.map((segment) => h('div', {},
                h('span', { class: 'who' }, `Line ${segment.sequence}, ${segment.speaker}`),
                segment.text))
            : [h('p', { class: 'muted' }, 'Lines appear here as they are transcribed or typed.')]));
        list.scrollTop = list.scrollHeight;
    };
    fill(session.segments);
    return { list, fill };
}

async function renderSession(area, session, context, showNotes) {
    const speaker = h('select', { 'aria-label': 'Who is speaking' },
        ['unknown', 'clinician', 'patient', 'other'].map((name) => h('option', { value: name }, name === 'unknown' ? 'Speaker not set' : name)));
    const { list, fill } = transcriptView(session);
    const reload = async () => fill((await api(`${BASE}/sessions/${session.id}`)).segments);

    const warning = h('div', { class: 'banner danger', id: 'mic-warning', hidden: true });
    const warn = (message) => {
        warning.textContent = message;
        warning.hidden = false;
    };
    let lost = 0;
    const lostChunk = () => {
        lost += 1;
        warn(`${plural(lost, 'part')} of this recording could not be transcribed. Type what was missed below.`);
    };

    const state = h('div', { class: 'recording' }, h('span', { class: 'dot' }), h('span', {}));
    const paint = (name) => {
        state.classList.toggle('on', name === 'recording');
        state.lastChild.textContent = { recording: 'Recording. The patient has been told.', paused: 'Not recording', stopped: 'Session ended' }[name];
    };
    paint(session.state);

    const setState = async (name) => {
        const changed = await act(() => api(`${BASE}/sessions/${session.id}/state`, { method: 'POST', body: { state: name } }), context);
        if (!changed) return false;
        session.state = name;
        paint(name);
        return true;
    };
    const controls = h('div', { class: 'row' },
        h('button', {
            type: 'button',
            id: 'start-recording',
            onclick: async () => {
                if (recorder) return;
                if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
                    warn('This browser cannot record. Type the lines below instead.');
                    return;
                }
                // The server checks consent first, so the microphone never opens without it.
                if (!(await setState('recording'))) return;
                try {
                    await startMicrophone(session, speaker, { onSegment: reload, onLost: lostChunk }, context);
                } catch (problem) {
                    await setState('paused');
                    warn(micProblem(problem));
                }
            },
        }, 'Start recording'),
        h('button', { type: 'button', class: 'quiet', onclick: async () => { await stopMicrophone(); await setState('paused'); } }, 'Pause'),
        h('button', { type: 'button', class: 'danger', onclick: async () => { await stopMicrophone(); await setState('stopped'); await reload(); } }, 'Stop and finish'),
        speaker,
        warning);

    const typed = form(
        [{ name: 'text', label: 'Type a line, for example if audio failed', type: 'textarea', required: true }],
        'Add line',
        async ({ text }, element) => {
            await act(() => api(`${BASE}/sessions/${session.id}/segments`, { method: 'POST', body: { text, speaker: speaker.value } }), context);
            element.reset();
            reload();
        },
    );
    const draft = form(
        [{
            name: 'template',
            label: 'Note format',
            type: 'select',
            options: [['soap', 'SOAP'], ['history_physical', 'History and physical'], ...(context.config.defaults.clinscribe_custom_headings.length ? [['custom', 'Our clinic format']] : [])],
        }],
        'Draft the note',
        async (values) => {
            if (values.template === 'custom') values.custom_headings = context.config.defaults.clinscribe_custom_headings;
            if (session.state !== 'stopped') {
                await stopMicrophone();
                if (!(await setState('stopped'))) return;
            }
            const note = await act(() => api(`${BASE}/sessions/${session.id}/draft-note`, { method: 'POST', body: values }), context, 'Draft ready');
            if (note) showNotes(note.id);
        },
        { inline: true },
    );
    show(area,
        h('h2', {}, `Consultation started ${when(session.started_at)}`),
        state,
        session.state !== 'stopped' ? controls : null,
        list,
        typed,
        ...section('Draft a note', draft));
}

function statementEditor(statement, section_) {
    const check = statement.check || {};
    const flagged = check.verdict === 'uncertain' || check.verdict === 'unsupported';
    const box = h('textarea', { 'aria-label': `Statement in ${section_}`, value: statement.text });
    return {
        box,
        element: h('div', { class: `verdict-${check.verdict}`, style: 'margin:8px 0' },
            box,
            h('div', { class: 'row muted' },
                flagged ? h('mark', {}, VERDICT_WORDS[check.verdict]) : h('span', {}, VERDICT_WORDS[check.verdict]),
                check.evidence?.length ? h('span', {}, `Transcript ${check.evidence.length === 1 ? 'line' : 'lines'} ${check.evidence.join(', ')}`) : null,
                h('span', {}, statement.origin === 'patient_statement' ? 'Patient said' : statement.origin === 'ai_summary' ? 'AI summary' : 'Clinician said')),
            check.reasons?.length ? h('ul', { class: 'reasons' }, check.reasons.map((reason) => h('li', {}, reason))) : null),
    };
}

function renderNote(area, note, context, afterChange) {
    const content = note.content;
    if (note.status === 'approved') {
        show(area,
            h('h2', {}, 'Signed note'),
            h('p', { class: 'muted' }, `Signed ${when(note.approved_at)}. ${note.template.toUpperCase()}.`),
            h('div', { class: 'sheet' }, content.sections.map((part) => [
                h('h3', {}, part.heading),
                part.statements.length ? part.statements.map((line) => h('p', {}, line.text)) : h('p', { class: 'muted' }, 'Nothing recorded.'),
            ])),
            h('a', { class: 'button', href: `${BASE}/notes/${note.id}/fhir`, download: `note-${note.id.slice(0, 8)}.json` }, 'Export as FHIR'),
        );
        return;
    }
    const editors = content.sections.map((part) => ({
        heading: part.heading,
        lines: part.statements.map((statement) => statementEditor(statement, part.heading)),
    }));
    const collect = () => ({
        sections: editors.map((part) => ({
            heading: part.heading,
            statements: part.lines.map((line) => ({ text: line.box.value.trim() })).filter((line) => line.text),
        })),
    });
    const save = () => act(() => api(`${BASE}/notes/${note.id}`, { method: 'PATCH', body: collect() }), context, 'Saved');
    const acknowledge = h('input', { type: 'checkbox' });
    show(area,
        h('h2', {}, 'Review the draft'),
        h('div', { class: 'banner' }, 'This is an AI draft and not part of the record until you sign it. Highlighted lines need your eye.'),
        h('p', { class: 'muted' }, `${plural(note.open_flags, 'line')} to check. Drafted by ${note.model}, prompt ${note.prompt_version}.`),
        editors.map((part) => h('div', {},
            h('h3', {}, part.heading),
            part.lines.length ? part.lines.map((line) => line.element) : h('p', { class: 'muted' }, 'Nothing said on this.'),
            h('button', {
                type: 'button',
                class: 'quiet',
                onclick: (event) => {
                    const line = statementEditor({ text: '', check: { verdict: 'clinician_entered' } }, part.heading);
                    part.lines.push(line);
                    event.target.before(line.element);
                },
            }, 'Add a line'))),
        content.missing_information?.length
            ? h('div', {}, h('h3', {}, 'Not in the transcript'), h('ul', {}, content.missing_information.map((item) => h('li', {}, item))))
            : null,
        h('div', { class: 'sheet' },
            note.open_flags ? h('label', { class: 'tick' }, acknowledge, 'I have checked every highlighted line') : null,
            h('div', { class: 'row', style: 'margin-top:8px' },
                h('button', {
                    type: 'button',
                    class: 'quiet',
                    onclick: async () => { if (await save()) afterChange(note.id); },
                }, 'Save changes'),
                h('button', {
                    type: 'button',
                    onclick: async () => {
                        if (!(await save())) return;
                        const signed = await act(() => api(`${BASE}/notes/${note.id}/approve`, { method: 'POST', body: { acknowledge_flags: acknowledge.checked } }), context, 'Note signed');
                        if (signed) afterChange(note.id);
                    },
                }, 'Sign note'),
                h('button', {
                    type: 'button',
                    class: 'quiet',
                    onclick: async () => {
                        await act(() => api(`${BASE}/notes/${note.id}/discard`, { method: 'POST' }), context, 'Draft discarded');
                        afterChange(null);
                    },
                }, 'Discard draft'))),
    );
}

export async function renderClinScribe(container, context) {
    await leaveRecording(context);
    const { patient, refresh } = context;
    if (!patient) return needPatient(container);
    const [encounters, sessions, notes] = await Promise.all([
        api(`/api/patients/${patient.id}/encounters`),
        api(`${BASE}/patients/${patient.id}/sessions`),
        api(`${BASE}/patients/${patient.id}/notes`),
    ]);
    const area = h('div', {});
    const showNote = async (noteId) => {
        if (!noteId) return refresh();
        renderNote(area, await api(`${BASE}/notes/${noteId}`), context, (next) => showNote(next));
        return undefined;
    };
    const showSession = async (id) => renderSession(area, await api(`${BASE}/sessions/${id}`), context, showNote);

    const open = encounters.filter((item) => item.status === 'in_progress');
    const start = open.length ? form(
        [
            { name: 'encounter', label: 'Visit', type: 'select', options: open.map((item) => [item.id, `${when(item.started_at)} ${item.reason}`]) },
            { name: 'language', label: 'Language spoken', type: 'select', value: context.config.defaults.clinscribe_language, options: [['en-IN', 'English'], ['hi-IN', 'Hindi']] },
        ],
        'Open a session',
        async ({ encounter, language }) => {
            const made = await act(() => api(`${BASE}/encounters/${encounter}/sessions`, { method: 'POST', body: { language } }), context);
            if (made) showSession(made.id);
        },
        { inline: true },
    ) : form(
        [{ name: 'reason', label: 'Reason for this visit' }],
        'Start a visit',
        async (values) => {
            const made = await act(() => api(`/api/patients/${patient.id}/encounters`, { method: 'POST', body: values }), context, 'Visit started');
            if (made) refresh();
        },
        { inline: true },
    );

    show(container,
        h('h1', {}, 'ClinScribe'),
        h('p', { class: 'lede' }, 'Turns a consultation into a draft note for you to check and sign. Audio is sent for transcription and not kept.'),
        h('div', { class: 'banner' }, 'Tell the patient before you start. Recording never begins on its own, and you can stop at any time.'),
        context.config.ai_ready ? null : h('div', { class: 'banner danger' }, 'AI isn\'t set up, so audio can\'t be transcribed and notes can\'t be drafted. Add a free Groq key in Settings.'),
        start,
        area,
        ...section('Earlier sessions', table(
            [
                { label: 'Started', cell: (row) => when(row.started_at) },
                { label: 'Lines', number: true, cell: (row) => row.segments },
                { label: 'State', cell: (row) => badge(row.transcript_deleted_at ? 'transcript deleted' : row.state) },
                { label: '', cell: (row) => h('button', { type: 'button', class: 'quiet', onclick: () => showSession(row.id) }, 'Open') },
            ],
            sessions,
            'No sessions yet.',
        )),
        ...section('Notes', table(
            [
                { label: 'Created', cell: (row) => when(row.created_at) },
                { label: 'Status', cell: (row) => (row.status === 'draft' ? h('mark', {}, 'Draft') : badge(row.status, 'good')) },
                { label: 'To check', number: true, cell: (row) => (row.status === 'draft' ? row.open_flags : '') },
                { label: '', cell: (row) => h('button', { type: 'button', class: 'quiet', onclick: () => showNote(row.id) }, 'Open') },
            ],
            notes,
            'No notes yet.',
        )),
    );
    return undefined;
}
