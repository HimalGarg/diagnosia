// DiagnoFast: reception sends a single-use link, and the doctor reads the report.
import { act, api, form, h, show, when } from './ui.js';
import { packPanel } from './pack.js';

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

function reportView(detail, context) {
    const { report, patient } = detail;
    const pack = h('div', { class: 'no-print' });
    const person = [patient.age, patient.sex].filter(Boolean).join(', ');
    return h('div', { class: 'sheet' },
        h('h2', {}, `${patient.name}: pre-consultation report`),
        person && h('p', {}, person),
        patient.abha_id && row('ABHA ID', patient.abha_id),
        detail.urgent.length > 0 && h('div', { class: 'banner danger' }, `Urgent: ${detail.urgent.join(', ')}`),
        detail.status === 'report_failed' && h('div', { class: 'banner' }, "The summary couldn't be written, but the patient's answers are below."),
        !report && detail.status !== 'report_failed' && h('p', { class: 'muted' }, STATUS_TEXT[detail.status] || detail.status),
        report && [
            h('p', {}, h('mark', {}, 'AI summary. Check it with the patient before relying on it.')),
            report.highlights.length > 0 && bullets('Highlights', report.highlights),
            row('Chief complaint', report.chief_complaint),
            row('History of present illness', report.present_illness),
            bullets('Current medicines', report.current_medicines),
            bullets('Allergies', report.allergies),
            row('Past history', report.past_history),
            report.gaps.length > 0 && bullets('Unclear or missing', report.gaps),
        ],
        h('h3', {}, `${detail.form_title} form`),
        detail.answers.map((answer) => row(answer.label, answer.value)),
        detail.records && [
            h('h3', {}, 'Past records from ABHA'),
            bullets('Conditions', detail.records.conditions),
            bullets('Prescriptions', detail.records.prescriptions),
            bullets('Lab reports', detail.records.lab_reports),
        ],
        h('h3', {}, "The patient's own words"),
        detail.transcript.map((turn, index) => h('div', { class: 'medicine-card' },
            h('p', {}, turn.question),
            h('strong', {}, turn.answer),
            turn.has_audio && h('audio', { class: 'no-print', controls: true, preload: 'none', src: `/api/diagnofast/intakes/${detail.id}/audio/${index}` }))),
        h('div', { class: 'row no-print' },
            h('a', { class: 'button quiet', href: '#/diagnofast' }, 'Back to the queue'),
            h('button', { type: 'button', class: 'quiet', onclick: () => window.print() }, 'Print or save as PDF'),
            patient.record_id && h('button', { type: 'button', class: 'quiet', onclick: async () => show(pack, await packPanel(patient.record_id, context)) }, "Open the patient's pack")),
        pack);
}

async function renderReport(container, id, context) {
    const load = async () => {
        const detail = await api(`/api/diagnofast/intakes/${id}`);
        show(container, h('h1', { class: 'no-print' }, 'DiagnoFast'), reportView(detail, context));
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
        await renderReport(container, route, context);
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
        h('p', { class: 'muted' }, h('a', { href: 'diagnofast_hiw.html' }, 'How it works')));
    keepFresh(loadQueue);
}
