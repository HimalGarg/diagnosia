// DiagnoFast: reception sends a single-use link, and the doctor reads the report.
import { act, api, badge, form, h, show, when } from './ui.js';

const STATUS_TEXT = {
    waiting: 'Not started',
    in_progress: 'Answering now',
    preparing_report: 'Preparing report',
    done: 'Report ready',
    report_failed: 'Answers ready',
};
const POLL_MS = 10000;
let poll = null;

function keepFresh(work) {
    clearInterval(poll);
    poll = setInterval(() => {
        if (!window.location.hash.startsWith('#/diagnofast')) clearInterval(poll);
        else work().catch(() => {});
    }, POLL_MS);
}

function row(label, value) {
    return h('p', {}, h('strong', {}, `${label}: `), value || 'Not stated');
}

function bullets(title, items) {
    return h('div', {}, h('strong', {}, title), h('ul', {}, (items.length ? items : ['None stated']).map((item) => h('li', {}, item))));
}

function reportTable(headers, rows) {
    return h('div', { class: 'requisition-table-wrap' }, h('table', { class: 'requisition-table' },
        h('thead', {}, h('tr', {}, headers.map((title) => h('th', { scope: 'col' }, title)))),
        h('tbody', {}, rows.map((cells) => h('tr', {}, cells.map((cell) => h('td', {}, cell)))))));
}

function barcode(id) {
    // Code 39, digits plus start/stop. Encodes only the local intake number, no patient data.
    const patterns = ['nnnwwnwnn', 'wnnwnnnnw', 'nnwwnnnnw', 'wnwwnnnnn', 'nnnwwnnnw',
        'wnnwwnnnn', 'nnwwwnnnn', 'nnnwnnwnw', 'wnnwnnwnn', 'nnwwnnwnn'];
    const digits = String(id).replace(/[^0-9]/g, '');
    const codes = ['nwnnwnwnn', ...[...digits].map((digit) => patterns[Number(digit)]), 'nwnnwnwnn'];
    const bars = h('div', { class: 'intake-barcode', 'aria-hidden': 'true' });
    codes.forEach((code) => {
        [...code].forEach((width, index) => bars.append(h('i', {
            class: `${index % 2 === 0 ? 'bar' : 'space'} ${width === 'w' ? 'wide' : ''}`,
        })));
        bars.append(h('i', { class: 'space' }));
    });
    return h('div', { class: 'barcode-label' }, bars, h('small', {}, `Intake #${digits}`));
}

export function reportView(detail) {
    const { report, patient, records } = detail;
    const age = patient.age === null || patient.age === undefined ? 'Age not stated' : `${patient.age} Y`;
    const history = [];
    if (report) {
        history.push(['Past history', report.past_history || 'Not stated', 'Interview / see provenance']);
        (report.current_medicines || []).forEach((item) => history.push(['Current medicine', item, 'Patient reported']));
        (report.allergies || []).forEach((item) => history.push(['Allergy', item, 'Patient reported · verify']));
    }
    if (records) {
        const source = records.is_demo_data ? 'ABHA · DEMO DATA' : 'ABHA record';
        (records.conditions || []).forEach((item) => history.push(['Condition', item, source]));
        (records.prescriptions || []).forEach((item) => history.push(['Prescription record', item, source]));
        (records.lab_reports || []).forEach((item) => history.push(['Past result', item, source]));
    }
    return h('article', { class: 'sheet requisition' },
        h('header', { class: 'requisition-header' },
            h('div', {}, h('p', { class: 'requisition-brand' }, 'DIAGNOSIA / DIAGNOFAST'),
                h('h2', {}, 'Clinical intake requisition'),
                h('p', { class: 'muted' }, 'Pre-consultation history · For physician review')),
            barcode(detail.id)),
        h('div', { class: 'requisition-demographics' },
            row('Patient', patient.name), row('Age / sex', `${age} / ${patient.sex || 'Not stated'}`),
            row('UHID / chart ID', patient.record_id || 'Not linked'), row('Department', detail.form_title),
            row('Visit', detail.created_at ? when(detail.created_at) : 'Not recorded'),
            row('Status', report ? 'Intake complete · clinician review required' : STATUS_TEXT[detail.status] || detail.status),
            records && patient.abha_id && row('ABHA ID', patient.abha_id)),
        detail.urgent.length > 0 && h('div', { class: 'banner danger requisition-alert' },
            h('strong', {}, 'URGENT SYMPTOMS — PATIENT DIRECTED TO RECEPTION: '), detail.urgent.join(', ')),
        detail.status === 'report_failed' && h('div', { class: 'banner' }, "The summary couldn't be written. Review the original answers below."),
        !report && detail.status !== 'report_failed' && h('p', { class: 'muted' }, STATUS_TEXT[detail.status] || detail.status),
        report && [
            h('p', { class: 'requisition-disclaimer' }, 'AI draft from reported history. Confirm with the patient. This is not a diagnosis, laboratory result or test order.'),
            row('Chief complaint', report.chief_complaint),
            row('History of present illness', report.present_illness),
            report.highlights?.length > 0 && row('Highlights', report.highlights.join(' • ')),
            h('h3', {}, 'Systematic review of systems'),
            report.review_of_systems?.length
                ? reportTable(['System / parameter', 'Observed or reported finding', 'Flag', 'Reference / context'],
                    report.review_of_systems.map((item) => [
                        h('span', {}, h('strong', {}, item.system), h('br'), item.parameter), item.finding,
                        h('span', { class: `ros-flag ${['H*', 'L*', 'CRITICAL'].includes(item.flag) ? 'flag-alert' : ''}` },
                            item.flag === 'NOT_ASSESSED' ? 'Not assessed' : item.flag), item.norm,
                    ]))
                : h('p', {}, 'Structured review not available. See the original form answers.'),
            h('p', { class: 'requisition-legend' }, 'H*: above / L*: below a supplied measurement range. NORMAL: explicitly reported negative or normal finding. Not assessed: no objective classification. Unasked symptoms are not normal findings.'),
        ],
        h('h3', {}, 'Past history, medicines & ABHA records ', records?.is_demo_data && badge('DEMO DATA', 'bad')),
        records?.is_demo_data && h('p', { class: 'requisition-demo' }, 'ABHA entries below are simulated. They are not verified patient history.'),
        history.length ? reportTable(['Record type', 'Details / date if known', 'Source'], history)
            : h('p', {}, 'No history available. ABHA records were not included.'),
        report?.gaps?.length > 0 && row('Unclear or missing — verify', report.gaps.join(' • ')),
        report && [h('h3', {}, 'Suggested initial investigations · Physician review only'),
            h('p', { class: 'requisition-legend' }, 'Suggestions are not orders. The physician decides whether any investigation is indicated.'),
            report.suggested_investigations?.length ? h('div', { class: 'investigation-checklist' },
                report.suggested_investigations.map((item) => h('label', {}, h('input', { type: 'checkbox' }), item)))
                : h('p', {}, 'No investigations suggested from the available history.')],
        h('section', { class: 'physician-workspace' }, h('h3', {}, "Examining physician’s workspace"),
            h('p', {}, 'Provisional / differential diagnosis: ', h('span', { class: 'writing-line' })),
            h('p', { class: 'plan-line' }, 'Prescription / plan: ', h('span', { class: 'writing-line' })),
            h('div', { class: 'signoff' }, h('p', {}, 'Doctor signature: ____________________'), h('p', {}, 'Date: ____________________'))),
        h('details', { class: 'no-print requisition-original', open: !report },
            h('summary', {}, 'Original form answers & patient transcript'),
            h('h3', {}, `${detail.form_title} form`),
            detail.answers.map((answer) => row(answer.label, answer.value)),
            h('h3', {}, "The patient’s own words"),
            detail.transcript.map((turn, index) => h('div', { class: 'medicine-card' },
                h('p', {}, turn.question), h('strong', {}, turn.answer),
                turn.has_audio && h('audio', { class: 'no-print', controls: true, preload: 'none', src: `/api/diagnofast/intakes/${detail.id}/audio/${index}` })))),
        h('p', { class: 'requisition-legend' }, `DiagnoFast · Intake #${detail.id} · Clinical decisions and sign-off remain with the examining physician.`),
        h('div', { class: 'row no-print' },
            h('a', { class: 'button quiet', href: '#/diagnofast' }, 'Back to the queue'),
            h('button', { type: 'button', class: 'quiet', onclick: () => window.print() }, 'Print or save as PDF')));
}

// Build a separate A4 page from whole entries. Measure at print width before printing,
// so an oversized entry is never cut in half or silently lost.
export function preparePrintReport(container, detail) {
    const old = document.querySelector('.intake-print-page');
    if (old) old.remove();
    const { report, patient, records } = detail;
    const page = h('article', { class: 'intake-print-page' });
    const content = h('div', { class: 'intake-print-content' });
    const warning = h('p', { class: 'intake-print-warning' });
    const workspace = h('div', { class: 'intake-print-workspace' },
        h('strong', {}, 'PHYSICIAN WORKSPACE'),
        h('p', {}, 'Provisional / differential diagnosis: __________________________________________'),
        h('p', {}, 'Plan: ___________________________________________________________________'),
        h('p', {}, 'Signature: ______________________________    Date: __________________'));
    page.append(content, warning, workspace);
    document.body.append(page);
    const entries = [];
    const add = (label, value) => {
        if (value !== null && value !== undefined && String(value).trim())
            entries.push(h('p', {}, h('strong', {}, `${label}: `), String(value)));
    };
    const section = (title) => entries.push(h('h3', {}, title));
    entries.push(h('div', { class: 'intake-print-header' },
        h('div', {}, h('strong', {}, 'DIAGNOSIA / DIAGNOFAST'), h('h2', {}, 'Clinical intake summary'),
            h('span', {}, 'AI-generated intake summary · clinician review required')), barcode(detail.id)));
    add('Patient', patient.name);
    add('Age / sex', `${patient.age ?? 'Not stated'} / ${patient.sex || 'Not stated'}`);
    add('Specialty / visit', `${detail.form_title} / ${detail.created_at ? when(detail.created_at) : 'Not recorded'}`);
    add('Chart ID', patient.record_id || 'Not linked');
    if (records && patient.abha_id) add('ABHA ID', patient.abha_id);
    if (detail.urgent?.length) entries.push(h('p', { class: 'intake-print-urgent' },
        h('strong', {}, 'URGENT — PATIENT DIRECTED TO RECEPTION: '), detail.urgent.join(' • ')));
    if (!report) add('Summary unavailable', detail.status === 'report_failed' ? 'Report generation failed. Review original answers and transcript on screen.' : 'Interview or report is not complete. Review original answers on screen.');
    if (report) {
        section('Chief complaint & history of present illness');
        add('Chief complaint', report.chief_complaint);
        add('HPI', report.present_illness);
        (report.highlights || []).forEach((x) => add('Important', x));
        section('Clinical findings · patient reported unless otherwise stated');
        (report.review_of_systems || []).forEach((x) => add(`${x.system} / ${x.parameter}`, `${x.finding} · ${x.flag === 'NOT_ASSESSED' ? 'Not assessed' : x.flag} · ${x.norm}`));
        section('History & medicines · patient reported');
        add('Past history', report.past_history);
        (report.current_medicines || []).forEach((x) => add('Medicine', x));
        (report.allergies || []).forEach((x) => add('Allergy', x));
    }
    if (records) {
        section(records.is_demo_data ? 'ABHA · DEMO DATA · simulated, not verified' : 'ABHA records');
        for (const [key, label] of [['conditions', 'Condition'], ['prescriptions', 'Prescription record'], ['lab_reports', 'Past result']])
            (records[key] || []).forEach((x) => add(label, x));
    }
    if (report?.gaps?.length) { section('Unclear or missing · verify'); report.gaps.forEach((x) => add('Verify', x)); }
    if (report?.suggested_investigations?.length) {
        section('Suggested investigations · for physician review only · not orders');
        report.suggested_investigations.forEach((x) => add('Consider', x));
    }
    section('Specialty form answers · patient reported');
    (detail.answers || []).filter((x) => x.value?.trim()).forEach((x) => add(x.label, x.value));
    // Reserve room for the warning even when it is not needed. Stop at the first
    // omitted entry so the printed page never implies that later content is complete.
    let omitted = false;
    for (const entry of entries) {
        content.append(entry);
        if (content.scrollHeight > content.clientHeight) {
            entry.remove();
            if (content.lastElementChild?.tagName === 'H3') content.lastElementChild.remove();
            omitted = true;
            break;
        }
    }
    warning.textContent = omitted
        ? 'INCOMPLETE PRINT SUMMARY: additional intake information did not fit. Review the full report, original form answers and transcript on screen.'
        : 'Review the original form answers and patient transcript on screen; AI summaries may contain errors or omissions.';
    const screenNotice = h('p', { class: 'intake-fit-notice no-print' }, omitted
        ? 'The A4 printout is incomplete: additional intake information does not fit. Review the complete report, original answers and transcript below.'
        : 'The A4 printout fits. Review the original answers and transcript below for any AI omissions.');
    container.querySelector('.requisition').prepend(screenNotice);
}

async function renderReport(container, id) {
    const load = async () => {
        const detail = await api(`/api/diagnofast/intakes/${id}`);
        show(container, h('h1', { class: 'no-print' }, 'DiagnoFast'), reportView(detail));
        preparePrintReport(container, detail);
        return detail;
    };
    const detail = await load();
    if (['waiting', 'in_progress', 'preparing_report'].includes(detail.status)) keepFresh(load);
}

function ageFrom(birthDate) {
    if (!birthDate) return undefined;
    return Math.floor((Date.now() - new Date(birthDate).getTime()) / (365.25 * 24 * 60 * 60 * 1000));
}

export async function renderDiagnoFast(container, context) {
    clearInterval(poll);
    const { patient, route } = context;
    if (!context.isStaff) {
        show(container, h('h1', {}, 'DiagnoFast'), h('p', { class: 'empty' }, 'Your clinic sends you a link for this interview. You need no account to answer it.'));
        return;
    }
    if (route) {
        await renderReport(container, route);
        return;
    }
    const forms = await api('/api/diagnofast/forms');
    const queue = h('div', {});
    const created = h('div', { 'aria-live': 'polite' });

    const loadQueue = async () => {
        const url = patient ? `/api/diagnofast/intakes?patient_id=${patient.id}` : '/api/diagnofast/intakes';
        const intakes = await api(url);
        show(queue, intakes.length
            ? intakes.map((intake) => h('a', { class: `queue-item${intake.urgent.length ? ' urgent' : ''}`, href: `#/diagnofast/${intake.id}` },
                h('span', {}, `${intake.urgent.length ? 'Urgent: ' : ''}${intake.patient_name}`),
                h('span', { class: 'muted' }, `${STATUS_TEXT[intake.status] || intake.status}, ${when(intake.created_at)}`)))
            : h('p', { class: 'muted' }, patient ? 'No interviews for this patient yet.' : 'No patients yet. Send a link to start.'));
    };

    const sendLink = form(
        [
            { name: 'form_id', label: 'Doctor form', type: 'select', options: forms.map((item) => [item.id, item.title]) },
            { name: 'patient_name', label: 'Patient name', required: true, value: patient ? `${patient.first_name} ${patient.last_name}` : '', maxlength: 120 },
            { name: 'patient_age', label: 'Age', type: 'number', min: 0, max: 130, value: ageFrom(patient?.date_of_birth) },
            { name: 'patient_sex', label: 'Sex', type: 'select', value: patient?.sex, options: [['', 'Not recorded'], ['female', 'Female'], ['male', 'Male'], ['other', 'Other']] },
            { name: 'abha_id', label: 'ABHA ID (optional, demo records only)', maxlength: 32 },
        ],
        'Create patient link',
        async (values, element) => {
            const made = await act(() => api('/api/diagnofast/intakes', {
                method: 'POST',
                body: { ...values, patient_sex: values.patient_sex || null, patient_id: patient?.id ?? null },
            }), context);
            if (!made) return;
            const url = new URL(made.link, window.location.href).href;
            show(created, h('div', { class: 'banner' },
                h('p', {}, 'Open this link on the waiting-room tablet. It works for this patient only, and stops when the interview ends.'),
                h('a', { class: 'link-box', href: url, target: '_blank', rel: 'noopener' }, url)));
            if (!patient) element.reset();
            await loadQueue();
        },
    );

    await loadQueue();
    show(container,
        h('h1', {}, 'DiagnoFast'),
        h('p', { class: 'lede' }, 'The patient answers by voice while they wait. You get a one-page report, and urgent symptoms come to the top.'),
        h('div', { class: 'columns' },
            h('div', { class: 'sheet' }, h('h2', {}, patient ? `New link for ${patient.first_name}` : 'New patient link'), sendLink, created),
            h('div', { class: 'sheet' }, h('h2', {}, patient ? `${patient.first_name}'s interviews` : 'Queue'), queue)),
        h('p', { class: 'muted' }, h('a', { href: 'intake.html', target: '_blank', rel: 'noopener' }, 'Open self-service kiosk'), ' · ', h('a', { href: 'diagnofast_hiw.html' }, 'How it works')));
    keepFresh(loadQueue);
}
