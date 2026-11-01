// The shared record: patient choice, consent, care team, timeline, documents, tasks and inbox.
import { act, api, ask, badge, form, h, section, table, when, show } from './ui.js';
import { canUse } from './catalogue.js';
import { visitStrip } from './home.js';
import { packPanel } from './pack.js';

export function needPatient(container, message = 'Choose a patient from the bar above, or go to Home.') {
    show(container, h('h1', {}, 'Open a patient first'), h('p', { class: 'empty' }, message, ' ', h('a', { href: '#/home' }, 'Go to Home')));
}

function startScreen(container, context) {
    const { me } = context;
    const joinForm = form(
        [{ name: 'code', label: 'Share code', required: true }],
        'Use this code',
        async ({ code }) => {
            const joined = await act(() => api('/api/patients/join', { method: 'POST', body: { code } }), context, 'Added');
            if (joined) {
                await context.reloadPatients();
                await context.openPatient(joined.patient_id);
            }
        },
    );
    const details = [
        { name: 'first_name', label: 'First name', required: true },
        { name: 'last_name', label: 'Last name', required: true },
        { name: 'date_of_birth', label: 'Date of birth', type: 'date' },
        { name: 'sex', label: 'Sex', type: 'select', options: [['unknown', 'Not recorded'], ['female', 'Female'], ['male', 'Male'], ['other', 'Other']], value: 'unknown' },
        { name: 'phone', label: 'Phone' },
        { name: 'preferred_language', label: 'Language', type: 'select', options: [['en-IN', 'English'], ['hi-IN', 'Hindi']] },
    ];
    const newRecord = form(details, me.role === 'doctor' ? 'Add patient' : 'Create my record', async (values) => {
        const url = me.role === 'doctor' ? '/api/patients' : '/api/patients/me';
        const created = await act(() => api(url, { method: 'POST', body: values }), context, 'Record created');
        if (created) {
            await context.reloadPatients();
            await context.openPatient(created.id);
        }
    });
    show(container,
        h('h1', {}, me.role === 'doctor' ? 'Open a patient' : 'Your health record'),
        h('p', { class: 'lede' }, me.role === 'doctor'
            ? 'Choose a patient from the bar above, or add one here. You only see patients who are on your list or have shared their record with you.'
            : 'Create your record, or use the code your clinic gave you.'),
        h('div', { class: 'columns' },
            h('div', { class: 'sheet' }, h('h2', { style: 'margin-top:0' }, me.role === 'doctor' ? 'Add a patient' : 'Create my record'), newRecord),
            h('div', { class: 'sheet' }, h('h2', { style: 'margin-top:0' }, 'I have a code'), joinForm)),
    );
}

async function careTeamPanel(context) {
    const { patient, refresh, me } = context;
    const team = await api(`/api/patients/${patient.id}/care-team`);
    const output = h('p', { class: 'muted' });
    const issue = (purpose, scope) => async () => {
        const made = await act(() => api(`/api/patients/${patient.id}/share-codes`, { method: 'POST', body: { purpose, scope } }), context);
        if (made) {
            show(output, 'Share this code once. It works for 24 hours: ', h('strong', {}, made.code));
        }
    };
    const canShare = patient.relationship !== 'caregiver';
    return [
        table(
            [
                { label: 'Name', cell: (row) => `${row.first_name} ${row.last_name}` },
                { label: 'Role', cell: (row) => row.role },
                { label: 'Access', cell: (row) => (row.scope === 'full' ? 'Can update' : 'View only') },
                {
                    label: '',
                    cell: (row) => ((patient.relationship === 'self' || row.user_id === me.id)
                        ? h('button', {
                            type: 'button',
                            class: 'quiet',
                            onclick: async () => {
                                await act(() => api(`/api/patients/${patient.id}/care-team/${row.id}`, { method: 'DELETE' }), context, 'Access removed');
                                if (row.user_id === me.id) window.location.reload();
                                else refresh();
                            },
                        }, row.user_id === me.id ? 'Leave' : 'Remove')
                        : ''),
                },
            ],
            team,
            'Only the patient can see this record.',
        ),
        canShare && h('div', { class: 'row', style: 'margin-top:8px' },
            h('button', { type: 'button', class: 'quiet', onclick: issue('clinician', 'full') }, 'Code for a clinician'),
            (patient.relationship === 'self' || !patient.user_id) && h('button', { type: 'button', class: 'quiet', onclick: issue('caregiver', 'read') }, 'Code for a caregiver (view only)'),
            patient.relationship === 'clinician' && !patient.user_id && h('button', { type: 'button', class: 'quiet', onclick: issue('claim', 'full') }, 'Code for the patient to claim this record')),
        output,
    ];
}

export async function renderOverview(container, context) {
    const { patient, isClinician, refresh } = context;
    if (!patient) return startScreen(container, context);
    const showInterviews = context.isStaff && canUse({ id: 'diagnofast', audience: 'staff' }, context);
    const [timeline, encounters, documents, interviews] = await Promise.all([
        api(`/api/patients/${patient.id}/timeline`),
        api(`/api/patients/${patient.id}/encounters`),
        api(`/api/patients/${patient.id}/documents`),
        showInterviews ? api(`/api/diagnofast/intakes?patient_id=${patient.id}`).catch(() => []) : [],
    ]);
    const allergyForm = patient.relationship === 'caregiver' ? null : form(
        [
            { name: 'substance', label: 'Allergy to', required: true },
            { name: 'reaction', label: 'Reaction' },
            { name: 'severity', label: 'Severity', type: 'select', options: ['unknown', 'mild', 'moderate', 'severe'] },
        ],
        'Add allergy',
        async (values) => {
            await act(() => api(`/api/patients/${patient.id}/allergies`, { method: 'POST', body: values }), context, 'Allergy added');
            await context.openPatient(patient.id);
        },
        { inline: true },
    );
    const visitForm = isClinician ? form(
        [
            { name: 'type', label: 'Type', type: 'select', options: ['outpatient', 'inpatient', 'emergency', 'telehealth', 'home'] },
            { name: 'reason', label: 'Reason for visit' },
        ],
        'Start visit',
        async (values) => {
            await act(() => api(`/api/patients/${patient.id}/encounters`, { method: 'POST', body: values }), context, 'Visit started');
            refresh();
        },
        { inline: true },
    ) : null;
    const upload = form(
        [
            { name: 'file', label: 'PDF, JPG or PNG', type: 'file', required: true },
            { name: 'document_type', label: 'What is it', type: 'select', options: [['other', 'Other'], ['x_ray', 'X-ray'], ['scan', 'Scan'], ['prescription', "Doctor's prescription"], ['report', 'Report']] },
            { name: 'description', label: 'Describe it, so it can be found later', maxlength: 2000 },
        ],
        'Upload document',
        async ({ file, document_type: documentType, description }) => {
            const data = new FormData();
            data.append('file', file);
            data.append('document_type', documentType);
            data.append('description', description || '');
            await act(() => api(`/api/patients/${patient.id}/documents`, { method: 'POST', form: data }), context, 'Document uploaded');
            refresh();
        },
        { inline: true },
    );

    show(container,
        h('h1', {}, 'Patient record'),
        h('p', { class: 'lede' }, 'Everything in this journey, in order. Only reviewed and approved events appear here.'),
        visitStrip(context),
        ...section('Journey',
            timeline.length
                ? h('ol', { class: 'timeline' }, timeline.map((event) => h('li', {},
                    h('strong', {}, event.title), event.detail ? ` ${event.detail}` : '',
                    h('div', { class: 'muted' }, `${when(event.occurred_at)}, ${event.module}`))))
                : h('p', { class: 'empty' }, 'Nothing yet. Visits, confirmed lab reports, signed notes and referrals will appear here.')),
        ...section('Visits',
            visitForm,
            table(
                [
                    { label: 'Started', cell: (row) => when(row.started_at) },
                    { label: 'Type', cell: (row) => row.type },
                    { label: 'Reason', cell: (row) => row.reason },
                    { label: 'Status', cell: (row) => badge(row.status) },
                ],
                encounters,
                'No visits recorded.',
            )),
        showInterviews ? section('Pre-visit interviews',
            interviews.length
                ? interviews.map((item) => h('a', { class: `queue-item${item.urgent.length ? ' urgent' : ''}`, href: `#/diagnofast/${item.id}` },
                    h('span', {}, item.urgent.length ? `Urgent: ${item.urgent.join(', ')}` : 'DiagnoFast interview'),
                    h('span', { class: 'muted' }, when(item.created_at))))
                : h('p', { class: 'muted' }, 'None yet. ', h('a', { href: '#/diagnofast' }, 'Send a DiagnoFast link'))) : null,
        patient.relationship !== 'caregiver' ? section('Patient pack', await packPanel(patient.id, context)) : null,
        ...section('Allergies', allergyForm),
        ...section('Who can see this record', await careTeamPanel(context)),
        ...section('Documents',
            patient.relationship !== 'caregiver' ? upload : null,
            table(
                [
                    { label: 'File', cell: (row) => h('a', { href: `/api/documents/${row.id}/file` }, row.filename) },
                    { label: 'Type', cell: (row) => row.document_type.replaceAll('_', ' ') },
                    { label: 'Added', cell: (row) => when(row.created_at) },
                ],
                documents,
                'No documents yet.',
            )),
    );
    return undefined;
}

const STATUS_CHOICES = { open: 'Reopen', done: 'Done', blocked: 'Blocked', missed: 'Missed' };

export function taskList(tasks, context, onChange) {
    return table(
        [
            {
                label: 'Task',
                cell: (task) => [
                    h('strong', {}, task.title),
                    task.detail ? h('div', { class: 'muted' }, task.detail) : null,
                ],
            },
            { label: 'Due', cell: (task) => [when(task.due_at), task.overdue ? [' ', badge('overdue', 'bad')] : null] },
            { label: 'Priority', cell: (task) => (task.priority === 'high' ? badge('high', 'bad') : 'routine') },
            { label: 'Status', cell: (task) => badge(task.status, task.status === 'done' ? 'good' : '') },
            {
                label: '',
                cell: (task) => h('div', { class: 'row' }, Object.entries(STATUS_CHOICES)
                    .filter(([status]) => status !== task.status && (status !== 'open' || task.status !== 'open'))
                    .map(([status, label]) => h('button', {
                        type: 'button',
                        class: 'quiet',
                        onclick: async () => {
                            const note = status === 'blocked' || status === 'missed' ? await ask('What happened?') ?? '' : '';
                            await act(() => api(`/api/tasks/${task.id}/status`, { method: 'POST', body: { status, note } }), context, 'Task updated');
                            onChange();
                        },
                    }, label))),
            },
        ],
        tasks,
        'No tasks.',
    );
}

export async function renderTasks(container, context) {
    const { patient, isClinician, refresh } = context;
    if (!patient) return needPatient(container);
    const tasks = await api(`/api/patients/${patient.id}/tasks`);
    const add = isClinician ? form(
        [
            { name: 'title', label: 'Task', required: true },
            { name: 'audience', label: 'For', type: 'select', options: [['care_team', 'Care team'], ['patient', 'Patient']] },
            { name: 'priority', label: 'Priority', type: 'select', options: ['routine', 'high'] },
            { name: 'due_at', label: 'Due', type: 'datetime-local' },
        ],
        'Add task',
        async (values) => {
            if (values.due_at) values.due_at = new Date(values.due_at).toISOString();
            await act(() => api(`/api/patients/${patient.id}/tasks`, { method: 'POST', body: values }), context, 'Task added');
            refresh();
        },
        { inline: true },
    ) : null;
    show(container, h('h1', {}, 'Tasks'), add, h('div', { style: 'margin-top:12px' }, taskList(tasks, context, refresh)));
    return undefined;
}

export async function renderInbox(container, context) {
    const notes = await api('/api/notifications');
    show(container,
        h('h1', {}, 'Inbox'),
        notes.length
            ? h('ul', { class: 'timeline' }, notes.map((note) => h('li', {},
                note.read_at ? note.title : h('strong', {}, note.title),
                note.body ? h('div', {}, note.body) : null,
                h('div', { class: 'row muted' }, when(note.created_at), !note.read_at && h('button', {
                    type: 'button',
                    class: 'link-button',
                    onclick: async () => {
                        await act(() => api(`/api/notifications/${note.id}/read`, { method: 'POST' }), context);
                        context.refresh();
                    },
                }, 'Mark as read')))))
            : h('p', { class: 'empty' }, 'Nothing new.'),
    );
}
