// MaternaCare: one place for pregnancy and postnatal care, and a direct line for a worry.
import { act, api, ask, badge, form, h, section, table, when, show } from './ui.js';
import { needPatient } from './overview.js';

const BASE = '/api/addons/maternacare';

function concernPanel(episode, context) {
    const result = h('div', {});
    const raise = form(
        [{ name: 'description', label: 'What is worrying you?', type: 'textarea', required: true }],
        'Tell my care team',
        async ({ description }, element) => {
            const sent = await act(() => api(`${BASE}/episodes/${episode.id}/concerns`, { method: 'POST', body: { description } }), context);
            if (!sent) return;
            element.reset();
            show(result, h('div', { class: 'banner danger' },
                `Your care team has been told and should reply by ${when(sent.respond_by)}. You can also call ${sent.contact}. ${sent.emergency_help}`));
        },
    );
    return h('div', { class: 'sheet alert-card' },
        h('h2', { style: 'margin-top:0' }, 'Worried about something?'),
        h('p', {}, `${episode.emergency_help} Otherwise tell us here, or call ${episode.escalation_contact}.`),
        raise, result);
}

function clinicianForms(episode, context) {
    const url = `${BASE}/episodes/${episode.id}`;
    const save = (path, message) => async (values) => {
        await act(() => api(`${url}/${path}`, { method: 'POST', body: values }), context, message);
        context.refresh();
    };
    return h('details', { style: 'margin-top:16px' }, h('summary', {}, 'Update the care record'), h('div', { class: 'columns', style: 'margin-top:8px' },
        h('div', { class: 'sheet' }, h('h3', { style: 'margin-top:0' }, 'Confirm the due date'), form(
            [
                { name: 'estimated_due_date', label: 'Due date', type: 'date', required: true, value: episode.estimated_due_date },
                { name: 'basis', label: 'Based on', required: true, value: episode.due_date_basis },
            ],
            'Confirm due date',
            save('due-date', 'Due date confirmed'),
        )),
        h('div', { class: 'sheet' }, h('h3', { style: 'margin-top:0' }, 'Plan a visit'), h('p', { class: 'muted' }, 'Follow your approved local schedule. None is built in.'), form(
            [
                { name: 'planned_date', label: 'Date', type: 'date', required: true },
                { name: 'purpose', label: 'Purpose', required: true },
                { name: 'stage', label: 'Stage', type: 'select', options: ['antenatal', 'postnatal'] },
                { name: 'subject', label: 'For', type: 'select', options: ['mother', 'newborn'] },
            ],
            'Add visit',
            save('visits', 'Visit planned'),
        )),
        h('div', { class: 'sheet' }, h('h3', { style: 'margin-top:0' }, 'Confirmed clinical risk'), form(
            [{ name: 'description', label: 'Risk you have confirmed', required: true }],
            'Add to register',
            save('risks', 'Added'),
        )),
        h('div', { class: 'sheet' }, h('h3', { style: 'margin-top:0' }, 'Reviewed education'), form(
            [
                { name: 'title', label: 'Title', required: true },
                { name: 'body', label: 'Text', type: 'textarea', required: true },
                { name: 'publisher', label: 'Publisher', required: true },
                { name: 'content_version', label: 'Version', required: true },
                { name: 'language', label: 'Language', type: 'select', options: [['en-IN', 'English'], ['hi-IN', 'Hindi']] },
            ],
            'Publish to patient',
            save('education', 'Published'),
        )),
        episode.status === 'antenatal' ? h('div', { class: 'sheet' }, h('h3', { style: 'margin-top:0' }, 'Record delivery'), form(
            [
                { name: 'delivered_at', label: 'Date and time', type: 'datetime-local', required: true },
                { name: 'facility', label: 'Facility', required: true },
                { name: 'outcome', label: 'Outcome', required: true },
                { name: 'create_newborn_record', label: 'Create a record for the baby', type: 'checkbox' },
            ],
            'Save delivery',
            save('delivery', 'Delivery recorded'),
        )) : null));
}

function visitTable(episode, context, isClinician) {
    const mark = (visit, status) => async () => {
        const notes = status === 'missed' ? await ask('Anything to note?') ?? '' : '';
        await act(() => api(`${BASE}/visits/${visit.id}`, { method: 'PATCH', body: { status, notes } }), context, 'Visit updated');
        context.refresh();
    };
    return table(
        [
            { label: 'Date', cell: (row) => [when(row.planned_date), row.overdue ? [' ', badge('overdue', 'bad')] : null] },
            { label: 'For', cell: (row) => `${row.subject}, ${row.stage}` },
            { label: 'Purpose', cell: (row) => row.purpose },
            { label: 'Status', cell: (row) => badge(row.status, row.status === 'missed' ? 'bad' : '') },
            {
                label: '',
                cell: (row) => (isClinician && row.status === 'scheduled' ? h('div', { class: 'row' },
                    h('button', { type: 'button', class: 'quiet', onclick: mark(row, 'attended') }, 'Attended'),
                    h('button', { type: 'button', class: 'quiet', onclick: mark(row, 'missed') }, 'Missed')) : ''),
            },
        ],
        episode.visits,
        'No visits planned yet.',
    );
}

export async function renderMaterna(container, context) {
    const { patient, isClinician, refresh } = context;
    if (!patient) return needPatient(container);
    const episode = await api(`${BASE}/patients/${patient.id}/episode`);
    const heading = [
        h('h1', {}, 'MaternaCare'),
        h('p', { class: 'lede' }, 'Keeps pregnancy and postnatal care together. It follows your care team\'s plan and does not assess risk by itself.'),
    ];
    if (!episode) {
        show(container, ...heading, isClinician ? form(
            [
                { name: 'facility', label: 'Facility' },
                { name: 'escalation_contact', label: 'Who the patient calls for help', required: true },
                { name: 'concern_response_hours', label: 'Your team replies to a concern within (hours)', type: 'number', min: 1, max: 72, required: true },
                { name: 'gravida', label: 'Gravida', type: 'number', min: 1 },
                { name: 'para', label: 'Para', type: 'number', min: 0 },
            ],
            'Start maternal care',
            async (values) => {
                await act(() => api(`${BASE}/episodes`, { method: 'POST', body: { ...values, patient_id: patient.id } }), context, 'Maternal care started');
                refresh();
            },
        ) : h('p', { class: 'empty' }, 'Your care team starts this with you.'));
        return undefined;
    }
    const open = episode.status !== 'closed';
    const birthPlan = patient.relationship !== 'caregiver' ? form(
        [
            { name: 'facility', label: 'Where you plan to give birth', value: episode.birth_plan?.facility },
            { name: 'transport', label: 'How you will get there', value: episode.birth_plan?.transport },
            { name: 'contacts', label: 'Who to call', value: episode.birth_plan?.contacts },
            { name: 'referral_arrangements', label: 'Referral arrangements', value: episode.birth_plan?.referral_arrangements },
        ],
        'Save birth plan',
        async (values) => {
            await act(() => api(`${BASE}/episodes/${episode.id}/birth-plan`, { method: 'PUT', body: values }), context, 'Birth plan saved');
            refresh();
        },
    ) : null;
    const observation = patient.relationship !== 'caregiver' ? form(
        [
            { name: 'label', label: 'What', required: true },
            { name: 'value_number', label: 'Value', type: 'number', step: 'any', required: true },
            { name: 'unit', label: 'Unit' },
        ],
        'Save',
        async (values) => {
            await act(() => api(`${BASE}/episodes/${episode.id}/observations`, { method: 'POST', body: values }), context, 'Saved');
            refresh();
        },
        { inline: true },
    ) : null;

    show(container,
        ...heading,
        h('p', { class: 'row' },
            badge(episode.status),
            episode.gestation ? h('strong', {}, `${episode.gestation.weeks} weeks ${episode.gestation.days} days`) : h('span', { class: 'muted' }, 'Due date not confirmed yet'),
            episode.estimated_due_date ? h('span', { class: 'muted' }, `Due ${when(episode.estimated_due_date)} (${episode.due_date_basis})`) : null),
        open ? concernPanel(episode, context) : null,
        isClinician ? h('div', {}, ...section('Concerns raised', table(
            [
                { label: 'Raised', cell: (row) => when(row.reported_at) },
                { label: 'Concern', cell: (row) => row.description },
                {
                    label: 'Response',
                    cell: (row) => (row.acknowledged_at ? row.outcome : h('button', {
                        type: 'button',
                        onclick: async () => {
                            const outcome = await ask('What did you do?');
                            if (!outcome) return;
                            await act(() => api(`${BASE}/concerns/${row.id}/respond`, { method: 'POST', body: { outcome } }), context, 'Response recorded');
                            refresh();
                        },
                    }, 'Record response')),
                },
            ],
            episode.concerns,
            'No concerns raised.',
        ))) : null,
        ...section('Visits', visitTable(episode, context, isClinician)),
        ...section('Readings', observation, table(
            [
                { label: 'When', cell: (row) => when(row.effective_at) },
                { label: 'What', cell: (row) => row.label },
                { label: 'Value', number: true, cell: (row) => `${row.value_number ?? row.value_text} ${row.unit}` },
                { label: 'From', cell: (row) => (row.source_type === 'patient_entered' ? 'Patient reported' : 'Clinician confirmed') },
            ],
            episode.observations,
            'None yet.',
        )),
        episode.risks.length ? h('div', {}, ...section('Clinician-confirmed risks', h('ul', {}, episode.risks.map((risk) => h('li', {}, risk.description))))) : null,
        episode.status === 'antenatal' ? h('div', {}, ...section('Birth plan', birthPlan)) : null,
        episode.delivery ? h('div', {}, ...section('Delivery', h('p', {}, `${when(episode.delivery.delivered_at)}, ${episode.delivery.facility}. ${episode.delivery.outcome}`))) : null,
        episode.education.length ? h('div', {}, ...section('Reading from your care team', episode.education.map((item) => h('div', { class: 'sheet' },
            h('h3', { style: 'margin-top:0' }, item.title), h('p', {}, item.body), h('p', { class: 'muted' }, `${item.publisher}, version ${item.content_version}`))))) : null,
        isClinician && open ? clinicianForms(episode, context) : null,
    );
    return undefined;
}
