// ReferralLoop: send a referral and follow it until the outcome is reviewed.
import { act, api, ask, badge, form, h, section, table, when, show } from './ui.js';

const BASE = '/api/addons/referrals';
const RECEIVER_ACTIONS = {
    sent: [['acknowledged', 'Acknowledge'], ['needs_information', 'Ask for more information'], ['declined', 'Decline'], ['redirected', 'Redirect']],
    acknowledged: [['accepted', 'Accept'], ['needs_information', 'Ask for more information'], ['declined', 'Decline'], ['redirected', 'Redirect']],
    booked: [['attended', 'Patient attended'], ['no_show', 'Did not attend'], ['rescheduled', 'Rescheduled']],
    rescheduled: [['attended', 'Patient attended'], ['no_show', 'Did not attend'], ['booked', 'Booked again']],
    appointment_offered: [['booked', 'Patient accepted'], ['declined', 'Decline']],
};

function daysFromNow(days) {
    const date = new Date();
    date.setDate(date.getDate() + days);
    return date.toISOString().slice(0, 10);
}

function referralDetail(area, referral, context, reload) {
    const manual = !referral.receiving_organization_id && !referral.receiving_user_id;
    const canAct = referral.side === 'receiver' || (referral.side === 'referrer' && manual);
    const move = (status) => async () => {
        const needsNote = ['declined', 'redirected', 'needs_information'].includes(status);
        const note = needsNote ? await ask('Please give a reason, so the referrer knows what to do next') : '';
        if (needsNote && !note) return;
        const body = { status, note: note || '' };
        await act(() => api(`${BASE}/${referral.id}/status`, { method: 'PATCH', body }), context, 'Updated');
        reload(referral.id);
    };
    const actions = canAct ? (RECEIVER_ACTIONS[referral.status] || []) : [];
    const booking = canAct && ['accepted', 'appointment_offered', 'no_show'].includes(referral.status) ? form(
        [
            { name: 'scheduled_at', label: 'Date and time', type: 'datetime-local', required: true },
            { name: 'location', label: 'Where' },
            { name: 'booked', label: 'The patient has agreed this time', type: 'checkbox' },
        ],
        'Save appointment',
        async (values) => {
            await act(() => api(`${BASE}/${referral.id}/appointments`, { method: 'POST', body: values }), context, 'Appointment saved');
            reload(referral.id);
        },
        { inline: true },
    ) : null;
    const outcome = canAct && referral.status === 'attended' ? form(
        [
            { name: 'summary', label: 'Outcome', type: 'textarea', required: true },
            { name: 'recommendations', label: 'Recommendations', type: 'textarea' },
            { name: 'follow_up_owner', label: 'Who follows up' },
            { name: 'file', label: 'Consult note (optional)', type: 'file' },
        ],
        'Return the outcome',
        async (values) => {
            const data = new FormData();
            data.append('summary', values.summary);
            data.append('recommendations', values.recommendations || '');
            data.append('follow_up_owner', values.follow_up_owner || '');
            if (values.file) data.append('file', values.file);
            await act(() => api(`${BASE}/${referral.id}/outcome`, { method: 'POST', form: data }), context, 'Outcome returned');
            reload(referral.id);
        },
    ) : null;
    const open = !['closed', 'cancelled'].includes(referral.status);

    show(area,
        h('div', { class: 'sheet' },
            h('div', { class: 'row' },
                h('h2', { style: 'margin:0' }, `${referral.specialty} for ${referral.patient.first_name} ${referral.patient.last_name}`),
                referral.priority === 'urgent' ? badge('urgent', 'bad') : badge('routine'),
                badge(referral.status)),
            (referral.exceptions || []).map((item) => h('div', { class: 'banner danger' }, item)),
            h('p', {}, referral.reason),
            referral.clinical_question ? h('p', {}, h('strong', {}, 'Question: '), referral.clinical_question) : null,
            h('p', { class: 'muted' }, `From ${referral.referring_clinician}. Reply needed by ${when(referral.respond_by)}.${referral.manual_destination ? ` Sent to: ${referral.manual_destination}.` : ''}`),
            referral.documents?.length ? h('ul', {}, referral.documents.map((item) => h('li', {}, h('a', { href: `/api/documents/${item.id}/file` }, item.filename)))) : null,
            referral.appointments.length ? table(
                [
                    { label: 'Appointment', cell: (row) => when(row.scheduled_at) },
                    { label: 'Where', cell: (row) => row.location },
                    { label: 'Status', cell: (row) => badge(row.status) },
                ],
                referral.appointments,
            ) : null,
            referral.outcome?.summary ? h('div', {}, h('h3', {}, 'Outcome'), h('p', {}, referral.outcome.summary), h('p', {}, referral.outcome.recommendations)) : null,
            actions.length ? h('div', { class: 'row', style: 'margin-top:8px' }, actions.map(([status, label]) => h('button', { type: 'button', class: 'quiet', onclick: move(status) }, label))) : null,
            booking, outcome,
            canAct && open ? h('button', {
                type: 'button',
                class: 'link-button',
                style: 'margin-top:8px',
                onclick: async () => {
                    const note = await ask('What happened on this contact attempt?') ?? '';
                    await act(() => api(`${BASE}/${referral.id}/contact-attempts`, { method: 'POST', body: { note } }), context, 'Attempt recorded');
                    reload(referral.id);
                },
            }, `Record a contact attempt (${referral.contact_attempts} so far)`) : null,
            referral.side === 'referrer' && referral.status === 'needs_information' ? h('button', { type: 'button', onclick: move('sent') }, 'Send again with the information') : null,
            referral.side === 'referrer' && referral.status === 'outcome_returned' ? h('button', {
                type: 'button',
                onclick: async () => {
                    await act(() => api(`${BASE}/${referral.id}/review`, { method: 'POST', body: {} }), context, 'Referral closed');
                    reload(referral.id);
                },
            }, 'I have reviewed this: close the referral') : null,
            referral.side !== 'patient' && open ? h('button', {
                type: 'button',
                class: 'link-button',
                style: 'margin-left:12px',
                onclick: async () => {
                    const body = await ask('New priority: routine or urgent');
                    if (body !== 'routine' && body !== 'urgent') return;
                    const reason = await ask('Why is the priority changing?');
                    if (!reason) return;
                    await act(() => api(`${BASE}/${referral.id}/priority`, { method: 'POST', body: { priority: body, reason } }), context, 'Priority changed');
                    reload(referral.id);
                },
            }, 'Change priority') : null,
            referral.side === 'referrer' && open ? h('button', {
                type: 'button',
                class: 'link-button',
                style: 'margin-left:12px',
                onclick: async () => {
                    const reason = await ask('Why is this referral closing without an outcome?');
                    if (!reason) return;
                    await act(() => api(`${BASE}/${referral.id}/close`, { method: 'POST', body: { reason } }), context, 'Referral closed');
                    reload(referral.id);
                },
            }, 'Close without an outcome') : null,
            h('details', { style: 'margin-top:12px' }, h('summary', {}, 'History'),
                table(
                    [
                        { label: 'When', cell: (row) => when(row.created_at) },
                        { label: 'Status', cell: (row) => row.to_status.replaceAll('_', ' ') },
                        { label: 'By', cell: (row) => (row.first_name ? `${row.first_name} ${row.last_name}` : '') },
                        { label: 'Note', cell: (row) => row.note || '' },
                    ],
                    referral.events,
                ))));
}

function list(rows, open) {
    return table(
        [
            { label: 'Patient', cell: (row) => `${row.patient.first_name} ${row.patient.last_name}` },
            { label: 'Specialty', cell: (row) => row.specialty },
            { label: 'Priority', cell: (row) => (row.priority === 'urgent' ? badge('urgent', 'bad') : 'routine') },
            { label: 'Status', cell: (row) => badge(row.status) },
            { label: 'Reply by', cell: (row) => when(row.respond_by) },
            { label: '', cell: (row) => h('button', { type: 'button', class: 'quiet', onclick: () => open(row.id) }, 'Open') },
        ],
        rows,
        'None.',
    );
}

function newReferral(context, organizations, documents, duplicate) {
    const { patient, refresh } = context;
    const { defaults } = context.config;
    const picks = documents.map((item) => ({ item, box: h('input', { type: 'checkbox' }) }));
    const fields = form(
        [
            { name: 'specialty', label: 'Specialty', required: true },
            { name: 'priority', label: 'Priority, by your judgement', type: 'select', options: ['routine', 'urgent'] },
            { name: 'respond_by', label: 'Reply needed by', type: 'date', required: true, value: daysFromNow(defaults.referrals_routine_days) },
            { name: 'organization', label: 'Send to', type: 'select', options: [['', 'Phone, fax or paper'], ...organizations.filter((item) => item.verification_status === 'verified').map((item) => [item.id, `${item.name} (${item.specialities || item.type})`])] },
            { name: 'manual_destination', label: 'If by phone, fax or paper: who' },
            { name: 'reason', label: 'Reason', type: 'textarea', required: true },
            { name: 'clinical_question', label: 'Your question for the specialist', type: 'textarea' },
        ],
        'Send referral',
        async (values) => {
            const body = {
                patient_id: patient.id,
                specialty: values.specialty,
                priority: values.priority,
                respond_by: values.respond_by,
                reason: values.reason,
                clinical_question: values.clinical_question || '',
                document_ids: picks.filter((pick) => pick.box.checked).map((pick) => pick.item.id),
                receiving_organization_id: values.organization || null,
                manual_destination: values.organization ? '' : values.manual_destination || '',
            };
            const send = () => api(BASE, { method: 'POST', body });
            try {
                await send();
            } catch (error) {
                if (error.detail?.code === 'possible_duplicate') {
                    const reason = await ask(`${error.detail.message}\n\nReason:`);
                    if (!reason) return;
                    body.confirm_duplicate_reason = reason;
                    await act(send, context, 'Referral sent');
                    refresh();
                    return;
                }
                await act(() => { throw error; }, context);
                return;
            }
            refresh();
        },
    );
    fields.elements.priority.addEventListener('change', (event) => {
        const days = event.target.value === 'urgent' ? defaults.referrals_urgent_days : defaults.referrals_routine_days;
        fields.elements.respond_by.value = daysFromNow(days);
    });
    return h('div', {},
        fields,
        picks.length ? h('div', { style: 'margin-top:8px' }, h('h3', {}, 'Share these documents'),
            picks.map((pick) => h('label', { class: 'tick' }, pick.box, pick.item.filename))) : null,
        duplicate);
}

export async function renderReferrals(container, context) {
    const { patient, isClinician, isStaff } = context;
    const area = h('div', {});
    const lists = h('div', {});
    const loadLists = async () => {
        const [rows, inbox, exceptions] = await Promise.all([
            patient ? api(`${BASE}/patients/${patient.id}`) : [],
            api(`${BASE}/inbox`),
            api(`${BASE}/exceptions`),
        ]);
        show(
            lists,
            section('Needs chasing', list(exceptions, open)),
            section('Received', list(inbox, open)),
            patient ? section(`Referrals for ${patient.first_name}`, list(rows, open)) : null,
        );
    };
    const open = async (id) => {
        referralDetail(area, await api(`${BASE}/${id}`), context, open);
        await loadLists();
        area.scrollIntoView({ block: 'start' });
    };
    const organizations = isClinician ? await api('/api/organizations') : [];
    const documents = isClinician ? await api(`/api/patients/${patient.id}/documents`) : [];

    if (!isStaff) {
        const patientRows = patient ? await api(`${BASE}/patients/${patient.id}`) : [];
        show(
            container,
            h('h1', {}, 'ReferralLoop'),
            h('p', { class: 'lede' }, 'Where your specialist referrals have got to.'),
            patient ? table(
                [
                    { label: 'Specialty', cell: (row) => row.specialty },
                    { label: 'Status', cell: (row) => badge(row.status) },
                    { label: 'Appointment', cell: (row) => (row.appointments.at(-1) ? `${when(row.appointments.at(-1).scheduled_at)} ${row.appointments.at(-1).location}` : '') },
                ],
                patientRows,
                'No referrals.',
            ) : h('p', { class: 'empty' }, 'Open your record first.'),
        );
        return undefined;
    }
    show(
            container,
        h('h1', {}, 'ReferralLoop'),
        h('p', { class: 'lede' }, 'Follows a referral until the outcome is back and reviewed. Priority and timing stay your decision.'),
        area,
        lists,
        isClinician ? h('details', { style: 'margin-top:12px' }, h('summary', {}, `New referral for ${patient.first_name}`), h('div', { class: 'sheet' }, newReferral(context, organizations, documents))) : null,
    );
    await loadLists();
    return undefined;
}
