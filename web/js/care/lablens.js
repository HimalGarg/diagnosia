// LabLens: upload a report, compare with the original, confirm, see trends.
import { act, api, ask, badge, form, h, plural, RANGE_WORDS, section, show, table, trendChart, when } from './ui.js';
import { needPatient } from './overview.js';

const BASE = '/api/addons/lablens';
const POLL_MS = 1500;
const READ_BY = { 'pdf-text': 'Read from the text in the PDF', 'ai-vision': 'Read by AI', manual: 'Entered by hand', none: 'Not read yet' };

export function labTrends(trends) {
    if (!trends.series.length) return h('p', { class: 'empty' }, 'Confirmed lab results will show here over time.');
    return [
        trends.notes.map((note) => h('div', { class: 'banner' }, note)),
        h('div', { class: 'columns' }, trends.series.map((line) => {
            const latest = line.points.at(-1);
            const range = latest.reference_text;
            return h('div', { class: 'sheet' }, trendChart({
                label: line.label,
                unit: line.unit,
                points: line.points,
                low: latest.reference_low,
                high: latest.reference_high,
                describe: (point) => `${RANGE_WORDS[point.range_status]}${point.laboratory ? `, ${point.laboratory}` : ''}`,
            }), h('p', { class: 'muted' }, range ? `Shaded: the printed range on the latest report, ${range}.` : ''));
        })),
    ];
}

function reviewRow(result, report, context, reload) {
    const needsEye = result.confidence === 'review' && result.review_status === 'pending';
    const locked = report.status === 'confirmed';
    const set = (body) => async () => {
        const saved = await act(() => api(`${BASE}/results/${result.id}`, { method: 'PATCH', body }), context);
        if (saved) reload();
    };
    const correct = async () => {
        const value = await ask(`Correct value for ${result.original_name}`, result.value_text);
        if (value === null) return;
        const unit = await ask('Unit', result.unit);
        if (unit === null) return;
        set({ action: 'correct', value_text: value, unit })();
    };
    return {
        name: [
            result.original_name,
            result.standard_name && result.standard_name.toLowerCase() !== result.original_name.toLowerCase()
                ? h('div', { class: 'muted' }, result.standard_name) : null,
        ],
        value: [
            needsEye ? h('mark', {}, `${result.value_text} ${result.unit}`) : `${result.value_text} ${result.unit}`,
            result.issues.length ? h('ul', { class: 'reasons' }, result.issues.map((issue) => h('li', {}, issue))) : null,
        ],
        range: [result.reference_text, result.range_status !== 'no_range' ? h('div', { class: 'muted' }, RANGE_WORDS[result.range_status]) : null],
        actions: locked ? badge(result.review_status) : h('div', { class: 'row' },
            result.review_status !== 'pending' ? badge(result.review_status, result.review_status === 'rejected' ? 'bad' : 'good') : null,
            h('button', { type: 'button', class: 'quiet', onclick: set({ action: 'accept' }) }, 'Correct as read'),
            h('button', { type: 'button', class: 'quiet', onclick: correct }, 'Fix'),
            h('button', { type: 'button', class: 'quiet', onclick: set({ action: 'reject' }) }, 'Remove')),
    };
}

function originalView(report) {
    const src = `/api/documents/${report.document_id}/file?inline=1`;
    return report.filename?.match(/\.(jpe?g|png)$/i)
        ? h('img', { class: 'original', src, alt: 'The original report', style: 'object-fit:contain' })
        : h('iframe', { class: 'original', src, title: 'The original report' });
}

async function renderReport(area, reportId, context) {
    const reload = () => renderReport(area, reportId, context);
    const report = await api(`${BASE}/reports/${reportId}/extraction`);
    if (report.status === 'processing') {
        show(area, h('p', { class: 'muted' }, 'Reading the report…'));
        setTimeout(() => { if (area.isConnected) reload(); }, POLL_MS);
        return;
    }
    const collected = h('input', { type: 'date', value: report.collected_at || '', 'aria-label': 'Date collected' });
    const identity = h('input', { type: 'checkbox' });
    const manual = form(
        [
            { name: 'test_name', label: 'Test', required: true },
            { name: 'value_text', label: 'Result', required: true },
            { name: 'unit', label: 'Unit' },
            { name: 'reference_text', label: "Lab's range" },
        ],
        'Add result',
        async (values) => {
            await act(() => api(`${BASE}/reports/${reportId}/results`, { method: 'POST', body: values }), context, 'Added');
            reload();
        },
        { inline: true },
    );
    const rows = report.results.map((result) => reviewRow(result, report, context, reload));
    show(area,
        h('h2', {}, 'Check against the original'),
        report.failure_reason ? h('div', { class: 'banner danger' }, report.failure_reason) : null,
        report.identity_check === 'mismatch'
            ? h('div', { class: 'banner danger' }, `The name on this report is ${report.name_on_report}, which differs from the open patient.`)
            : null,
        h('p', { class: 'muted' }, [
            report.laboratory, report.name_on_report && `Name on report: ${report.name_on_report}`,
            READ_BY[report.extraction_method] || READ_BY.none,
        ].filter(Boolean).join('. ')),
        h('div', { class: 'split' },
            h('div', {}, originalView(report)),
            h('div', {},
                table(
                    [
                        { label: 'Test', cell: (row) => row.name },
                        { label: 'Result', cell: (row) => row.value },
                        { label: "Lab's range", cell: (row) => row.range },
                        { label: '', cell: (row) => row.actions },
                    ],
                    rows,
                    'No results found.',
                ))),
        report.status !== 'confirmed' ? h('details', { open: !report.results.length, style: 'margin-top:12px' }, h('summary', {}, 'Add a result by hand'), manual) : null,
        report.status === 'failed' || report.status === 'needs_manual_entry' ? h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                await act(() => api(`${BASE}/reports/${reportId}/retry`, { method: 'POST' }), context);
                reload();
            },
        }, 'Try reading again') : null,
        report.status === 'needs_review' ? h('div', { class: 'sheet' },
            h('p', {}, report.to_review ? `Still to check: ${plural(report.to_review, 'highlighted result')}.` : 'Results not removed or fixed will be saved as read.'),
            h('label', {}, 'Date the sample was collected', collected),
            report.identity_check === 'mismatch' ? h('label', { class: 'tick' }, identity, 'I confirm this report belongs to this patient') : null,
            h('button', {
                type: 'button',
                style: 'margin-top:8px',
                onclick: async () => {
                    const done = await act(() => api(`${BASE}/reports/${reportId}/confirm`, {
                        method: 'POST',
                        body: { collected_at: collected.value || null, acknowledge_identity: identity.checked },
                    }), context, 'Report confirmed');
                    if (done) context.refresh();
                },
            }, 'Confirm and add to the record')) : null,
    );
}

function reportTable(reports, area, context) {
    return table(
        [
            { label: 'File', cell: (row) => row.filename },
            { label: 'Collected', cell: (row) => when(row.collected_at) },
            { label: 'Results', number: true, cell: (row) => row.result_count },
            { label: 'Status', cell: (row) => (row.status === 'needs_review' ? h('mark', {}, 'Needs review') : badge(row.status, row.status === 'failed' ? 'bad' : row.status === 'confirmed' ? 'good' : '')) },
            { label: '', cell: (row) => h('button', { type: 'button', class: 'quiet', onclick: () => renderReport(area, row.id, context) }, 'Open') },
        ],
        reports,
        'No reports yet.',
    );
}

export async function renderLabLens(container, context) {
    const { patient } = context;
    if (!patient) return needPatient(container);
    const [reports, trends] = await Promise.all([
        api(`${BASE}/patients/${patient.id}/reports`),
        api(`${BASE}/patients/${patient.id}/trends`),
    ]);
    const area = h('div', {});
    const list = h('div', {});
    const fillList = (rows) => show(list, reportTable(rows, area, context));
    fillList(reports);
    const upload = patient.relationship === 'caregiver' ? null : form(
        [{ name: 'file', label: 'Lab report (PDF, JPG or PNG)', type: 'file', required: true }],
        'Upload report',
        async ({ file }) => {
            const data = new FormData();
            data.append('patient_id', patient.id);
            data.append('file', file);
            const made = await act(() => api(`${BASE}/reports`, { method: 'POST', form: data }), context);
            if (!made) return;
            await renderReport(area, made.id, context);
            fillList(await api(`${BASE}/patients/${patient.id}/reports`));
        },
        { inline: true },
    );
    show(container,
        h('h1', {}, 'LabLens'),
        h('p', { class: 'lede' }, 'Reads lab reports into results you can check and follow over time. It shows where a value sits against the lab\'s range and does not diagnose. Talk to your care team about what a result means.'),
        upload,
        area,
        ...section('Reports', list),
        ...section('Trends', labTrends(trends)),
    );
    return undefined;
}
