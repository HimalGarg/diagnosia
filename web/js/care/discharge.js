// DischargeCare: the ward's plan, and the checklist the patient takes home.
import { act, api, ask, badge, form, h, section, table, when, show } from './ui.js';
import { taskList } from './overview.js';

const BASE = '/api/addons/dischargecare';

function board(rows, open) {
    return table(
        [
            { label: 'Patient', cell: (row) => [row.flagged ? '⚑ ' : '', `${row.first_name} ${row.last_name}`] },
            { label: 'Discharge', cell: (row) => when(row.discharge_date) },
            { label: 'To', cell: (row) => row.destination },
            { label: 'Barriers', number: true, cell: (row) => row.barriers_open },
            { label: 'Open tasks', number: true, cell: (row) => row.tasks_open },
            { label: 'Overdue', number: true, cell: (row) => (row.tasks_overdue ? badge(String(row.tasks_overdue), 'bad') : 0) },
            { label: 'Results due', number: true, cell: (row) => row.results_pending },
            { label: 'Status', cell: (row) => (row.status === 'draft' ? h('mark', {}, 'Not approved') : badge(row.status)) },
            { label: '', cell: (row) => h('button', { type: 'button', class: 'quiet', onclick: () => open(row.id) }, 'Open') },
        ],
        rows,
        'No open discharges.',
    );
}

function episodeEditor(area, episode, context, open) {
    const url = `${BASE}/episodes/${episode.id}`;
    const reload = () => open(episode.id);
    const post = (path, body, message) => act(() => api(`${url}/${path}`, { method: 'POST', body }), context, message);
    const draft = episode.status === 'draft';
    const live = episode.status !== 'closed';

    const summary = draft ? form(
        [
            { name: 'problems', label: 'Problem list', type: 'textarea', value: episode.problems },
            { name: 'procedures', label: 'Procedures', type: 'textarea', value: episode.procedures },
            { name: 'emergency_contact', label: 'Who the patient calls if worried', value: episode.emergency_contact },
        ],
        'Save summary',
        async (values) => {
            await act(() => api(url, { method: 'PATCH', body: values }), context, 'Saved');
            reload();
        },
    ) : h('div', {}, h('p', {}, episode.problems), h('p', {}, episode.procedures), h('p', { class: 'muted' }, `Patient contact: ${episode.emergency_contact}`));

    const instruction = draft ? form(
        [
            { name: 'topic', label: 'Topic', required: true },
            { name: 'text', label: 'Instruction, as approved by you', type: 'textarea', required: true },
            { name: 'language', label: 'Language', type: 'select', options: [['en-IN', 'English'], ['hi-IN', 'Hindi']] },
        ],
        'Add instruction',
        async (values) => { await post('instructions', values, 'Added'); reload(); },
    ) : null;
    const barrier = live ? form(
        [
            { name: 'description', label: 'What is in the way', required: true },
            { name: 'owner', label: 'Who is on it' },
        ],
        'Add barrier',
        async (values) => { await post('barriers', values, 'Added'); reload(); },
        { inline: true },
    ) : null;
    const pending = live ? form(
        [
            { name: 'test_name', label: 'Test', required: true },
            { name: 'expected_by', label: 'Expected by', type: 'date' },
        ],
        'Add pending result',
        async (values) => { await post('pending-results', values, 'Added'); reload(); },
        { inline: true },
    ) : null;
    const task = live ? form(
        [
            { name: 'title', label: 'Task', required: true },
            { name: 'audience', label: 'For', type: 'select', options: [['patient', 'Patient or caregiver'], ['care_team', 'Care team']] },
            { name: 'priority', label: 'Priority', type: 'select', options: ['routine', 'high'] },
            { name: 'due_at', label: 'Due', type: 'datetime-local' },
            { name: 'required', label: 'Needed before closing', type: 'checkbox', value: true },
        ],
        'Add task',
        async (values) => {
            if (values.due_at) values.due_at = new Date(values.due_at).toISOString();
            await post('tasks', values, 'Added');
            reload();
        },
        { inline: true },
    ) : null;

    show(area, h('div', { class: 'sheet' },
        h('div', { class: 'row' },
            h('h2', { style: 'margin:0' }, `Discharge to ${episode.destination}`),
            draft ? h('mark', {}, 'Not approved. The patient sees nothing yet.') : badge(episode.status)),
        h('p', { class: 'muted' }, `${when(episode.discharge_date)}`),
        h('div', { class: 'row', style: 'margin:8px 0' },
            h('button', {
                type: 'button',
                class: 'quiet',
                onclick: async () => {
                    await act(() => api(url, { method: 'PATCH', body: { flagged: !episode.flagged } }), context, 'Updated');
                    reload();
                },
            }, episode.flagged ? 'Remove flag' : 'Flag for attention')),
        ...section('Summary', summary),
        ...section('Instructions', instruction, table(
            [{ label: 'Topic', cell: (row) => row.topic }, { label: 'Text', cell: (row) => row.text }],
            episode.instructions,
            'Add at least one instruction before approving.',
        )),
        ...section('Barriers', barrier, table(
            [
                { label: 'Barrier', cell: (row) => row.description },
                { label: 'Owner', cell: (row) => row.owner },
                {
                    label: '',
                    cell: (row) => (row.status === 'open' ? h('button', {
                        type: 'button',
                        class: 'quiet',
                        onclick: async () => {
                            await act(() => api(`${BASE}/barriers/${row.id}/resolve`, { method: 'POST' }), context);
                            reload();
                        },
                    }, 'Resolved') : badge('resolved', 'good')),
                },
            ],
            episode.barriers,
            'No barriers recorded.',
        )),
        ...section('Results still to come', pending, table(
            [
                { label: 'Test', cell: (row) => row.test_name },
                { label: 'Expected', cell: (row) => when(row.expected_by) },
                {
                    label: 'Status',
                    cell: (row) => (row.status === 'pending' ? h('button', {
                        type: 'button',
                        class: 'quiet',
                        onclick: async () => {
                            const note = await ask('Result summary');
                            if (!note) return;
                            await act(() => api(`${BASE}/pending-results/${row.id}/finalise`, { method: 'POST', body: { result_note: note } }), context);
                            reload();
                        },
                    }, 'Mark final') : `Final: ${row.result_note}`),
                },
            ],
            episode.pending_results,
            'None pending.',
        )),
        ...section('Tasks', task, h('div', { style: 'margin-top:8px' }, taskList(episode.tasks.filter((item) => item.status !== 'held'), context, reload)),
            episode.tasks.some((item) => item.status === 'held') ? h('p', { class: 'muted' }, `${episode.tasks.filter((item) => item.status === 'held').length} tasks start when you approve.`) : null),
        draft ? h('button', {
            type: 'button',
            style: 'margin-top:16px',
            onclick: async () => {
                const done = await post('approve', undefined, 'Discharge approved');
                if (done) reload();
            },
        }, 'Approve discharge plan') : null,
        episode.status === 'active' ? h('div', { class: 'sheet', style: 'margin-top:16px' },
            (episode.close_blockers || []).map((item) => h('div', { class: 'banner' }, item)),
            h('button', {
                type: 'button',
                onclick: async () => {
                    const needsReason = episode.close_blockers?.length;
                    const reason = needsReason ? await ask('Work is outstanding. Why close anyway?') : '';
                    if (needsReason && !reason) return;
                    const done = await post('close', { exception_reason: reason || '' }, 'Discharge closed');
                    if (done) context.refresh();
                },
            }, 'Close discharge')) : null));
}

function checklist(data, context, onChange) {
    return h('div', {},
        h('div', { class: 'banner danger' }, `If you are worried, contact ${data.emergency_contact}. In an emergency call 112.`),
        ...section('What to do', taskList(data.tasks, context, onChange)),
        ...section('Your instructions', data.instructions.map((item) => h('div', {}, h('h3', {}, item.topic), h('p', {}, item.text)))),
        data.pending_results.length ? h('div', {}, ...section('Test results still to come', table(
            [
                { label: 'Test', cell: (row) => row.test_name },
                { label: 'Expected', cell: (row) => when(row.expected_by) },
                { label: 'Status', cell: (row) => (row.status === 'pending' ? 'Not back yet' : 'Back') },
            ],
            data.pending_results,
        ))) : null);
}

export async function renderDischarge(container, context) {
    const { patient, isClinician, isStaff } = context;
    const area = h('div', {});
    const open = async (id) => {
        const episode = await api(`${BASE}/episodes/${id}`);
        episodeEditor(area, episode, context, open);
        area.scrollIntoView({ block: 'start' });
    };

    if (!isStaff) {
        if (!patient) {
            show(container, h('h1', {}, 'DischargeCare'), h('p', { class: 'empty' }, 'Open your record first.'));
            return undefined;
        }
        const episodes = await api(`${BASE}/patients/${patient.id}/episodes`);
        const latest = episodes[0];
        const loadChecklist = async () => {
            show(area, checklist(await api(`${BASE}/episodes/${latest.id}/checklist`), context, loadChecklist));
        };
        show(container, h('h1', {}, 'Going home'), latest ? area : h('p', { class: 'empty' }, 'Your checklist will appear here once your doctor approves your discharge.'));
        if (latest) await loadChecklist();
        return undefined;
    }

    const rows = await api(`${BASE}/board`);
    const start = isClinician ? form(
        [
            { name: 'discharge_date', label: 'Discharge date', type: 'date', required: true },
            { name: 'destination', label: 'Going to', required: true },
        ],
        `Start a discharge for ${patient.first_name}`,
        async (values) => {
            const made = await act(() => api(`${BASE}/episodes`, { method: 'POST', body: { ...values, patient_id: patient.id } }), context, 'Discharge started');
            if (made) open(made.id);
        },
        { inline: true },
    ) : null;
    show(container,
        h('h1', {}, 'DischargeCare'),
        h('p', { class: 'lede' }, 'Turns a discharge plan into tracked tasks. It coordinates the work and leaves the decision to discharge with you.'),
        start,
        area,
        ...section('Discharge board', board(rows, open)),
    );
    return undefined;
}
