// Reminders, and the clinic's list of people who need a call.
import { act, api, badge, form, h, section, show, table, toast, when } from './ui.js';

const STATE_WORDS = {
    pending: 'Scheduled',
    in_app_sent: 'Shown in the app, not confirmed',
    external_delivery_pending: 'With the provider, not confirmed',
    delivered: 'Provider reported delivery',
    failed: 'Delivery failed',
    staff_followup_required: 'Staff follow-up needed',
    acknowledged: 'Confirmed',
    cancelled: 'Cancelled',
};
const OUTCOMES = [
    ['reached', 'Reached the patient'],
    ['no_answer', 'No answer'],
    ['message_left', 'Left a message'],
    ['wrong_number', 'Wrong number'],
    ['patient_declined', 'Patient declined'],
    ['unable_to_contact', 'Could not contact'],
];

function stateBadge(state) {
    if (state === 'staff_followup_required' || state === 'failed') return badge(STATE_WORDS[state], 'bad');
    if (state === 'acknowledged') return badge(STATE_WORDS[state], 'good');
    return badge(STATE_WORDS[state]);
}

function outcomeControl(reminder, context) {
    const choice = h('select', { 'aria-label': 'What happened' }, OUTCOMES.map(([value, label]) => h('option', { value }, label)));
    return h('div', { class: 'row' },
        choice,
        h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                await act(() => api(`/api/reminders/${reminder.id}/outcome`, { method: 'POST', body: { outcome: choice.value } }), context, 'Outcome recorded');
                context.refresh();
            },
        }, 'Record'));
}

async function printCallList(area, context) {
    const list = await act(() => api('/api/reminders/call-list'), context);
    if (!list) return;
    show(area,
        h('div', { class: 'print-list' },
            h('h2', {}, 'Clinic call list'),
            h('p', { class: 'muted' }, `Printed ${when(list.generated_at)}. Confidential: destroy after use.`),
            table(
                [
                    { label: 'Name', cell: (row) => row.name },
                    { label: 'Phone', cell: (row) => row.phone || 'No phone on record' },
                    { label: 'Reason', cell: (row) => row.reason },
                    { label: 'Tries so far', number: true, cell: (row) => row.attempts },
                    { label: 'Outcome', cell: () => '' },
                ],
                list.entries,
                'Nobody needs a call.',
            )));
    document.body.classList.add('printing-list');
    window.addEventListener('afterprint', () => document.body.classList.remove('printing-list'), { once: true });
    window.print();
}

async function preferencesPanel(context) {
    const { patient } = context;
    const saved = await api(`/api/patients/${patient.id}/communication-preferences`);
    const canWrite = patient.relationship !== 'caregiver';
    return form(
        [
            { name: 'in_app', label: 'In the app', type: 'checkbox', value: saved.in_app },
            { name: 'clinic_call', label: 'Phone call from the clinic', type: 'checkbox', value: saved.clinic_call },
            { name: 'sms', label: 'SMS, if the clinic has set this up', type: 'checkbox', value: saved.sms },
            { name: 'voice', label: 'Automated voice call, if the clinic has set this up', type: 'checkbox', value: saved.voice },
        ],
        canWrite ? 'Save preferences' : 'Saving is not available',
        async (values) => {
            if (!canWrite) return;
            await act(() => api(`/api/patients/${patient.id}/communication-preferences`, { method: 'PUT', body: values }), context, 'Preferences saved');
        },
    );
}

export async function renderReminders(container, context) {
    const { patient, isClinician, isStaff, refresh } = context;
    const printArea = h('div', { class: 'print-holder' });
    const followUps = isStaff ? await api('/api/reminders/follow-up') : [];
    const reminders = patient ? await api(`/api/patients/${patient.id}/reminders`) : [];

    const schedule = isClinician ? form(
        [
            { name: 'title', label: 'Reminder', required: true },
            { name: 'purpose', label: 'About', type: 'select', options: [['follow_up', 'Follow-up'], ['appointment', 'Appointment'], ['test', 'Test'], ['medicine_review', 'Medicine review'], ['other', 'Other']] },
            { name: 'module', label: 'Linked to', type: 'select', options: [['general', 'General'], ['chroniccare', 'ChronicCare'], ['dischargecare', 'DischargeCare'], ['maternacare', 'MaternaCare'], ['referralloop', 'ReferralLoop']] },
            { name: 'priority', label: 'Priority', type: 'select', options: ['routine', 'high'] },
            { name: 'scheduled_for', label: 'When', type: 'datetime-local', required: true },
            { name: 'respond_within_hours', label: 'Hours to confirm', type: 'number', min: 1, max: 168, value: context.config.defaults.reminders_respond_hours },
        ],
        'Schedule reminder',
        async (values) => {
            values.scheduled_for = new Date(values.scheduled_for).toISOString();
            const made = await act(() => api(`/api/patients/${patient.id}/reminders`, { method: 'POST', body: values }), context, 'Reminder scheduled');
            if (made) refresh();
        },
        { inline: true },
    ) : null;

    show(container,
        h('h1', {}, 'Reminders'),
        h('p', { class: 'lede' }, 'Tracks whether a reminder reached someone. If it can\'t be confirmed, a person follows up.'),
        h('div', { class: 'banner' }, 'A reminder is not an emergency or clinical escalation. Scheduled or sent does not mean it was received.'),
        isStaff ? section('Clinic follow-up',
            h('button', { type: 'button', class: 'quiet', onclick: () => printCallList(printArea, context) }, 'Print a call list'),
            table(
                [
                    { label: 'Patient', cell: (row) => [`${row.first_name} ${row.last_name}`, row.phone ? h('div', { class: 'muted' }, row.phone) : h('div', { class: 'muted' }, 'No phone on record')] },
                    { label: 'Reminder', cell: (row) => [row.title, h('div', { class: 'muted' }, row.failure_reason)] },
                    { label: 'Priority', cell: (row) => (row.priority === 'high' ? badge('high', 'bad') : 'routine') },
                    { label: 'Follow up by', cell: (row) => [when(row.followup_due_at), row.overdue ? [' ', badge('overdue', 'bad')] : null] },
                    { label: 'Tries', number: true, cell: (row) => row.contact_attempts },
                    { label: 'Outcome', cell: (row) => outcomeControl(row, context) },
                ],
                followUps,
                'Nobody needs a call right now.',
            )) : null,
        printArea,
        patient ? section(`Reminders for ${patient.first_name}`,
            schedule,
            table(
                [
                    { label: 'Reminder', cell: (row) => row.title },
                    { label: 'When', cell: (row) => when(row.scheduled_for) },
                    { label: 'Channel', cell: (row) => (row.channel || 'Not chosen yet').replaceAll('_', ' ') },
                    { label: 'What happened', cell: (row) => stateBadge(row.state) },
                    {
                        label: '',
                        cell: (row) => (['in_app_sent', 'delivered', 'external_delivery_pending'].includes(row.state) && !isClinician
                            ? h('button', {
                                type: 'button',
                                class: 'quiet',
                                onclick: async () => {
                                    const done = await act(() => api(`/api/reminders/${row.id}/acknowledge`, { method: 'POST' }), context);
                                    if (done) { toast('Thank you. Your care team can see this.'); refresh(); }
                                },
                            }, 'I saw this')
                            : ''),
                    },
                ],
                reminders,
                'No reminders.',
            )) : h('p', { class: 'empty' }, 'Open a patient to see or schedule their reminders.'),
        patient ? section('How to reach this person', await preferencesPanel(context)) : null,
    );
    return undefined;
}
