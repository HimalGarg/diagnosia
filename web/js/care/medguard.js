// MedGuard: compare medication lists, review alerts, confirm one list.
import { act, api, ask, badge, form, h, section, table, when, show } from './ui.js';
import { needPatient } from './overview.js';

const BASE = '/api/addons/medguard';
const SOURCE_WORDS = {
    prescription: 'Prescription',
    patient_reported: 'Patient reported',
    discharge_summary: 'Discharge summary',
    imported_record: 'Imported record',
    clinician_entered: 'Clinician entry',
    reconciled: 'Reconciled list',
};

function ingredients(record) {
    const list = JSON.parse(record.ingredients);
    return record.match_status === 'matched' ? list.join(', ') : h('mark', {}, 'Not identified');
}

function identifyButton(record, context) {
    return h('button', {
        type: 'button',
        class: 'quiet',
        onclick: async () => {
            const looked = await act(() => api(`${BASE}/lookup?name=${encodeURIComponent(record.written_name)}`), context);
            const options = [
                ...(looked?.suggestions || []).map((item) => item.ingredients.join(' + ')),
                ...(looked?.rxnorm?.ingredients?.length ? [`${looked.rxnorm.ingredients.join(' + ')} (RxNorm, US)`] : []),
            ];
            const lookupNote = looked?.rxnorm?.available === false ? 'RxNorm could not be reached, so this has no outside suggestion.\n\n' : '';
            const hint = `${lookupNote}${options.length ? `Close matches, unconfirmed: ${options.join('; ')}\n\n` : ''}`;
            const answer = await ask(`${hint}Active ingredients of ${record.written_name}, separated by commas:`);
            if (!answer) return;
            const reason = await ask('How did you confirm this?') || '';
            await act(() => api(`${BASE}/medications/${record.id}`, {
                method: 'PATCH',
                body: { ingredients: answer.split(',').map((item) => item.trim()).filter(Boolean), reason },
            }), context, 'Medicine identified');
            context.refresh();
        },
    }, 'Identify');
}

function prescriptionImport(context) {
    const { patient, refresh } = context;
    const area = h('div', {});
    const reader = form(
        [
            { name: 'image', label: 'Prescription photo (JPG or PNG)', type: 'file', required: true },
            { name: 'consent', label: 'The patient agrees to sending this photo to our AI services', type: 'checkbox' },
        ],
        'Read prescription',
        async ({ image, consent }) => {
            const data = new FormData();
            data.append('image', image);
            data.append('consent', consent ? 'true' : 'false');
            const read = await act(() => api('/api/prescription/read', { method: 'POST', form: data }), context);
            if (!read) return;
            if (!read.is_prescription) {
                show(area, h('p', { class: 'error' }, 'That photo does not look like a prescription.'));
                return;
            }
            const rows = read.medicines.map((medicine) => ({ medicine, keep: h('input', { type: 'checkbox', checked: medicine.name.legibility !== 'unreadable', 'aria-label': 'Include' }) }));
            const fieldCell = (field) => (field.legibility === 'clear' ? field.value : h('mark', {}, field.value || 'Unreadable'));
            show(area,
                h('p', { class: 'muted' }, 'Check each line against the photo. Unreadable medicines are not imported.'),
                table(
                    [
                        { label: 'Include', cell: (row) => row.keep },
                        { label: 'Medicine', cell: (row) => fieldCell(row.medicine.name) },
                        { label: 'Dose', cell: (row) => fieldCell(row.medicine.dose) },
                        { label: 'How often', cell: (row) => fieldCell(row.medicine.frequency) },
                    ],
                    rows,
                ),
                h('button', {
                    type: 'button',
                    style: 'margin-top:8px',
                    onclick: async () => {
                        const medicines = rows.filter((row) => row.keep.checked).map(({ medicine }) => {
                            const { plain, ...fields } = medicine;
                            return fields;
                        });
                        const done = await act(() => api(`${BASE}/patients/${patient.id}/import-prescription`, { method: 'POST', body: { medicines } }), context, 'Imported');
                        if (done) refresh();
                    },
                }, 'Add to the prescription list'));
        },
    );
    return [reader, area];
}

const COVERAGE_STYLE = { approved_rule_alert: 'banner danger', information_missing: 'banner danger' };

function coveragePanel(coverage) {
    if (!coverage) return null;
    return coverage.statements.map((statement) => h('div', { class: COVERAGE_STYLE[statement.state] || 'banner' }, statement.text));
}

function unverifiedCard(alert) {
    return h('div', { class: 'sheet' },
        h('div', { class: 'row' }, h('mark', {}, 'Unverified, not a clinical alert'), h('h3', { style: 'margin:0' }, alert.title)),
        h('p', {}, alert.explanation));
}

function alertCard(alert, context, rerun) {
    if (alert.authority === 'unverified_rule') return unverifiedCard(alert);
    const reason = h('input', { 'aria-label': 'Reason for your decision', placeholder: 'Your reasoning' });
    const decide = (disposition) => async () => {
        const saved = await act(() => api(`${BASE}/alerts/${alert.id}/review`, { method: 'POST', body: { disposition, reason: reason.value || disposition.replaceAll('_', ' ') } }), context);
        if (saved) rerun();
    };
    return h('div', { class: `sheet alert-card ${alert.severity}` },
        h('div', { class: 'row' }, badge(`${alert.severity} priority`, alert.severity === 'high' ? 'bad' : ''), h('h3', { style: 'margin:0' }, alert.title)),
        h('p', {}, alert.explanation),
        h('p', { class: 'muted' }, alert.evidence.map((item) => `${item.written_name} (${item.source})`).join(', ')),
        alert.disposition === 'pending'
            ? h('div', { class: 'row' }, reason,
                h('button', { type: 'button', class: 'quiet', onclick: decide('acknowledged') }, 'Noted'),
                h('button', { type: 'button', class: 'quiet', onclick: decide('action_planned') }, 'Action planned'),
                h('button', { type: 'button', class: 'quiet', onclick: decide('not_applicable') }, 'Not applicable'))
            : h('p', { class: 'muted' }, `${alert.disposition.replaceAll('_', ' ')}: ${alert.disposition_reason}`));
}

function reconciliationPanel(result, medicines, context, rerun) {
    const choices = medicines
        .filter((record) => record.status === 'active' && record.source !== 'reconciled' && !['superseded', 'rejected'].includes(record.review_status))
        .map((record) => ({
            record,
            decision: h('select', { 'aria-label': `Decision for ${record.written_name}` },
                [['unresolved', 'Unresolved'], ['continue', 'Continue'], ['hold', 'Hold'], ['stop', 'Stop']].map(([value, label]) => h('option', { value }, label))),
            note: h('input', { 'aria-label': 'Note', maxLength: 300 }),
        }));
    return [
        coveragePanel(result.rule_coverage),
        h('p', { class: 'muted' }, `${result.knowledge.note} Drug list: ${result.knowledge.drug_list_review_status}.`),
        result.alerts.map((alert) => alertCard(alert, context, rerun)),
        ...section('Your decisions', h('p', { class: 'muted' }, 'Recorded next to each source. MedGuard changes nothing by itself.'),
            table(
                [
                    { label: 'Medicine', cell: (row) => `${row.record.written_name} ${row.record.dose}` },
                    { label: 'Source', cell: (row) => SOURCE_WORDS[row.record.source] },
                    { label: 'Decision', cell: (row) => row.decision },
                    { label: 'Note', cell: (row) => row.note },
                ],
                choices,
            ),
            h('button', {
                type: 'button',
                style: 'margin-top:8px',
                onclick: async () => {
                    const decisions = choices.map((row) => ({ medication_id: row.record.id, decision: row.decision.value, note: row.note.value }));
                    const done = await act(() => api(`${BASE}/reconciliations/${result.id}/finalise`, { method: 'POST', body: { decisions } }), context, 'Reconciliation finalised');
                    if (done) context.refresh();
                },
            }, 'Finalise reconciliation')),
    ];
}

export async function renderMedGuard(container, context) {
    const { patient, isClinician, refresh } = context;
    if (!patient) return needPatient(container);
    const medicines = await api(`/api/patients/${patient.id}/medications`);
    const history = isClinician ? await api(`${BASE}/patients/${patient.id}/reconciliations`) : [];
    const workspace = h('div', {});
    const showReview = (result) => show(workspace, ...reconciliationPanel(result, medicines, context, run).flat());
    async function run() {
        const result = await act(() => api(`${BASE}/reconcile`, { method: 'POST', body: { patient_id: patient.id } }), context);
        if (result) showReview(result);
    }

    const current = medicines.filter((record) => record.source === 'reconciled' && record.review_status === 'accepted');
    const sourcesList = medicines.filter((record) => record.source !== 'reconciled');
    const draft = history.find((item) => item.status === 'draft');
    if (draft) showReview(draft);

    const addForm = patient.relationship === 'caregiver' ? null : form(
        [
            { name: 'written_name', label: 'Medicine, as written', required: true },
            { name: 'strength', label: 'Strength' },
            { name: 'dose', label: 'Dose' },
            { name: 'frequency', label: 'How often, for example 1-0-1' },
            ...(isClinician ? [{ name: 'source', label: 'Source', type: 'select', options: Object.entries(SOURCE_WORDS).filter(([key]) => key !== 'reconciled') }] : []),
        ],
        isClinician ? 'Add medicine' : 'Add a medicine I take',
        async (values) => {
            await act(() => api(`${BASE}/patients/${patient.id}/medications`, { method: 'POST', body: values }), context, 'Added');
            refresh();
        },
        { inline: true },
    );

    show(container,
        h('h1', {}, 'MedGuard'),
        h('p', { class: 'lede' }, isClinician
            ? 'Compares every medication source and raises questions. It never changes a medicine.'
            : 'The medicines on your record. Tell your care team about anything you take that is missing.'),
        ...section('Current reconciled list', table(
            [
                { label: 'Medicine', cell: (row) => row.written_name },
                { label: 'Dose', cell: (row) => `${row.dose} ${row.frequency}` },
                { label: 'Status', cell: (row) => badge(row.status) },
                { label: 'Confirmed', cell: (row) => when(row.reviewed_at) },
            ],
            current,
            'No reconciled list yet.',
        )),
        ...section('Sources, kept as written', addForm, h('div', { style: 'margin-top:8px' }, table(
            [
                { label: 'As written', cell: (row) => row.written_name },
                { label: 'Ingredients', cell: ingredients },
                { label: 'Dose', cell: (row) => [row.strength, row.dose, row.frequency].filter(Boolean).join(' ') },
                { label: 'Source', cell: (row) => [SOURCE_WORDS[row.source], row.source_type === 'ai_extracted' ? [' ', h('mark', {}, 'AI read')] : null] },
                { label: '', cell: (row) => (isClinician && row.match_status === 'unmatched' ? identifyButton(row, context) : '') },
            ],
            sourcesList,
            'No medicines recorded.',
        ))),
        patient.relationship !== 'caregiver' ? h('details', { style: 'margin-top:12px' }, h('summary', {}, 'Import from a prescription photo'), h('div', { class: 'sheet' }, prescriptionImport(context))) : null,
        isClinician ? h('div', {}, ...section('Reconcile',
            h('button', { type: 'button', onclick: run }, draft ? 'Run the checks again' : 'Run the checks'),
            workspace)) : null,
        isClinician ? h('div', {}, ...section('Past reconciliations', table(
            [
                { label: 'Finalised', cell: (row) => when(row.finalised_at) },
                { label: 'Confirmed', number: true, cell: (row) => row.summary?.confirmed.length ?? 0 },
                { label: 'Unresolved', number: true, cell: (row) => row.summary?.unresolved.length ?? 0 },
            ],
            history.filter((item) => item.status === 'finalised'),
            'None yet.',
        ))) : null,
        h('p', { class: 'muted', style: 'margin-top:24px' }, 'Drug names may be looked up in RxNorm, from the U.S. National Library of Medicine. NLM does not endorse this product.'),
    );
    return undefined;
}
