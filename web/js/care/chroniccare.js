// ChronicCare: home readings, symptoms, adherence and the clinician-approved plan.
import { act, api, badge, form, h, section, table, trendChart, when, show } from './ui.js';
import { labTrends } from './lablens.js';
import { needPatient } from './overview.js';

const BASE = '/api/addons/chroniccare';

function measureRow(catalog) {
    const fields = {
        measure: h('select', { 'aria-label': 'Measure' }, Object.entries(catalog).map(([key, item]) => h('option', { value: key }, item.label))),
        target_text: h('input', { 'aria-label': 'Target, in words', placeholder: 'Target, in words' }),
        cadence_days: h('input', { type: 'number', min: 1, 'aria-label': 'Measure every (days)', placeholder: 'Every (days)' }),
        alert_low: h('input', { type: 'number', step: 'any', 'aria-label': 'Alert below', placeholder: 'Alert below' }),
        alert_high: h('input', { type: 'number', step: 'any', 'aria-label': 'Alert above', placeholder: 'Alert above' }),
        escalation_text: h('input', { 'aria-label': 'What the patient should do if a limit is crossed', placeholder: 'What to do if a limit is crossed' }),
    };
    const number = (control) => (control.value === '' ? null : Number(control.value));
    return {
        element: h('div', { class: 'row', style: 'margin-bottom:8px' }, Object.values(fields)),
        read: () => ({
            measure: fields.measure.value,
            target_text: fields.target_text.value,
            cadence_days: number(fields.cadence_days),
            alert_low: number(fields.alert_low),
            alert_high: number(fields.alert_high),
            escalation_text: fields.escalation_text.value,
        }),
    };
}

function planBuilder(context, catalog, conditions) {
    const { patient, refresh } = context;
    const rows = [measureRow(catalog)];
    const holder = h('div', {}, rows[0].element);
    const details = form(
        [
            { name: 'title', label: 'Plan title', required: true },
            { name: 'condition_id', label: 'Condition', type: 'select', options: [['', 'None'], ...conditions.map((item) => [item.id, item.condition_name])] },
            { name: 'goals', label: 'Goals, one per line', type: 'textarea' },
        ],
        'Save draft plan',
        async (values) => {
            const body = {
                title: values.title,
                condition_id: values.condition_id || null,
                goals: (values.goals || '').split('\n').map((item) => item.trim()).filter(Boolean),
                measures: rows.map((row) => row.read()),
            };
            await act(() => api(`${BASE}/patients/${patient.id}/plans`, { method: 'POST', body }), context, 'Draft saved');
            refresh();
        },
    );
    return [
        h('h3', { style: 'margin-top:0' }, 'What to measure'),
        holder,
        h('button', {
            type: 'button',
            class: 'quiet',
            style: 'margin-bottom:12px',
            onclick: () => {
                const row = measureRow(catalog);
                rows.push(row);
                holder.append(row.element);
            },
        }, 'Add another measure'),
        details,
    ];
}

function planView(plan, context) {
    return h('div', { class: 'sheet' },
        h('div', { class: 'row' }, h('h3', { style: 'margin:0' }, plan.title), plan.status === 'draft' ? h('mark', {}, 'Draft: not active') : badge('Approved', 'good')),
        plan.goals.length ? h('ul', {}, plan.goals.map((goal) => h('li', {}, goal.description))) : null,
        plan.review_date ? h('p', { class: 'muted' }, `Review by ${when(plan.review_date)}`) : null,
        plan.status === 'draft' && context.isClinician ? h('button', {
            type: 'button',
            onclick: async () => {
                await act(() => api(`${BASE}/plans/${plan.id}/approve`, { method: 'POST' }), context, 'Plan approved');
                context.refresh();
            },
        }, 'Approve plan') : null);
}

function measureCard(measure) {
    return h('div', { class: 'sheet' },
        measure.points.length
            ? trendChart({
                label: measure.label,
                unit: measure.unit,
                points: measure.points,
                describe: (point) => (point.source_type === 'patient_entered' ? 'Patient-entered, not clinician-verified' : 'Clinician entered'),
            })
            : h('div', {}, h('h3', { style: 'margin-top:0' }, `${measure.label} (${measure.unit})`), h('p', { class: 'muted' }, 'No readings yet.')),
        h('div', { class: 'row' },
            measure.overdue ? badge('reading overdue', 'bad') : null,
            measure.crossed ? badge(`latest is ${measure.crossed} the plan's limit`, 'bad') : null,
            measure.target_text ? h('span', { class: 'muted' }, `Target: ${measure.target_text}`) : null));
}

export async function renderChronicCare(container, context) {
    const { patient, isClinician, refresh } = context;
    if (!patient) return needPatient(container);
    const [data, catalog] = await Promise.all([
        api(`${BASE}/patients/${patient.id}/dashboard`),
        api(`${BASE}/measures`),
    ]);
    const attention = h('div', {});
    const log = form(
        [
            { name: 'measure', label: 'Measure', type: 'select', options: Object.entries(catalog).map(([key, item]) => [key, `${item.label} (${item.unit})`]) },
            { name: 'value', label: 'Reading', type: 'number', step: 'any', required: true },
            { name: 'note', label: 'Note' },
        ],
        'Save reading',
        async (values) => {
            const saved = await act(() => api(`${BASE}/patients/${patient.id}/readings`, { method: 'POST', body: values }), context, 'Reading saved');
            if (saved?.attention) {
                show(attention, h('div', { class: 'banner danger' }, saved.attention.message, h('br'), saved.attention.reminder));
            } else if (saved) {
                refresh();
            }
        },
        { inline: true },
    );
    const symptom = form(
        [
            { name: 'symptom', label: 'Symptom', required: true },
            { name: 'severity', label: 'Severity, 0 to 10', type: 'number', min: 0, max: 10, required: true },
            { name: 'duration', label: 'How long' },
            { name: 'triggers', label: 'Trigger' },
        ],
        'Log symptom',
        async (values) => {
            await act(() => api(`${BASE}/patients/${patient.id}/symptoms`, { method: 'POST', body: values }), context, 'Symptom logged');
            refresh();
        },
        { inline: true },
    );
    const adherence = form(
        [
            { name: 'medicine_name', label: 'Medicine', required: true },
            { name: 'event', label: 'What happened', type: 'select', options: [['taken', 'Taken'], ['missed', 'Missed'], ['held', 'Held by a clinician']] },
        ],
        'Log dose',
        async (values) => {
            await act(() => api(`${BASE}/patients/${patient.id}/adherence`, { method: 'POST', body: values }), context, 'Logged');
            refresh();
        },
        { inline: true },
    );
    const condition = isClinician ? form(
        [
            { name: 'condition_name', label: 'Condition', required: true },
            { name: 'onset_date', label: 'Date identified', type: 'date' },
        ],
        'Add condition',
        async (values) => {
            await act(() => api(`${BASE}/patients/${patient.id}/conditions`, { method: 'POST', body: values }), context, 'Added');
            refresh();
        },
        { inline: true },
    ) : null;
    const canWrite = patient.relationship !== 'caregiver';

    show(container,
        h('h1', {}, 'ChronicCare'),
        h('p', { class: 'lede' }, 'Follows a long-term condition between visits. It does not diagnose or change treatment. For an emergency, call 112.'),
        attention,
        ...section('Conditions', condition, table(
            [
                { label: 'Condition', cell: (row) => row.condition_name },
                { label: 'Since', cell: (row) => when(row.onset_date) },
                { label: 'Status', cell: (row) => badge(row.status) },
            ],
            data.conditions,
            'No conditions recorded.',
        )),
        ...section('Care plan', data.plans.length ? data.plans.map((plan) => planView(plan, context)) : h('p', { class: 'muted' }, 'No approved plan yet.'),
            isClinician ? h('details', {}, h('summary', {}, 'Write a care plan'), h('div', { class: 'sheet' }, planBuilder(context, catalog, data.conditions))) : null),
        canWrite ? h('div', {}, ...section('Add a reading', log)) : null,
        data.measures.length ? h('div', {}, ...section('Plan measures', h('div', { class: 'columns' }, data.measures.map(measureCard)))) : null,
        ...section('Recent readings', table(
            [
                { label: 'When', cell: (row) => when(row.effective_at) },
                { label: 'Measure', cell: (row) => row.label },
                { label: 'Reading', number: true, cell: (row) => `${row.value_number} ${row.unit}` },
                { label: 'From', cell: (row) => (row.source_type === 'patient_entered' ? 'Patient entered, not verified' : 'Clinician entered') },
            ],
            data.vitals,
            'No readings yet.',
        )),
        canWrite ? h('div', {}, ...section('Symptom diary', symptom)) : h('h2', {}, 'Symptom diary'),
        table(
            [
                { label: 'When', cell: (row) => when(row.effective_at) },
                { label: 'Symptom', cell: (row) => row.label },
                { label: 'Severity', number: true, cell: (row) => `${row.value_number}/10` },
                { label: 'Notes', cell: (row) => row.note },
            ],
            data.symptoms,
            'Nothing logged.',
        ),
        canWrite ? h('div', {}, ...section('Medicines taken', h('p', { class: 'muted' }, `What was reported in the last ${data.adherence_window_days} days. A prescription alone says nothing about this.`), adherence)) : h('h2', {}, 'Medicines taken'),
        table(
            [
                { label: 'Medicine', cell: (row) => row.medicine },
                { label: 'Taken', number: true, cell: (row) => row.taken },
                { label: 'Missed', number: true, cell: (row) => row.missed },
                { label: 'Held', number: true, cell: (row) => row.held },
            ],
            data.adherence,
            'Nothing reported.',
        ),
        ...section('Lab trends', labTrends(data.lab_trends)),
    );
    return undefined;
}
