// AMR Guard: a hospital stewardship team's view of its own data. Not for patient care decisions.
import { act, api, badge, form, h, section, table, when, show } from './ui.js';

const BASE = '/api/addons/amrguard';

function antibiogramView(run, context, reload) {
    return h('div', { class: 'sheet' },
        h('div', { class: 'row' },
            h('h3', { style: 'margin:0' }, `${when(run.period_start)} to ${when(run.period_end)}`),
            run.status === 'draft' ? h('mark', {}, 'Draft: needs a second reviewer') : badge('approved', 'good')),
        h('p', { class: 'muted' }, run.method),
        run.data_quality.warnings.map((warning) => h('div', { class: 'banner' }, warning)),
        h('p', { class: 'muted' }, `${run.data_quality.isolates} isolates, ${run.data_quality.repeat_isolates_excluded} repeats left out. ${run.data_quality.standards.join(', ')}.`),
        run.output.filter((entry) => entry.reported).map((entry) => h('div', {},
            h('h3', {}, `${entry.organism} (${entry.isolates} isolates)`),
            table(
                [
                    { label: 'Antibiotic', cell: (row) => row.name },
                    { label: 'Isolates tested', number: true, cell: (row) => row.tested },
                    { label: '% susceptible', number: true, cell: (row) => row.percent_susceptible ?? row.note },
                ],
                entry.antibiotics,
            ))),
        run.status === 'draft' ? h('button', {
            type: 'button',
            onclick: async () => {
                await act(() => api(`${BASE}/antibiograms/${run.id}/approve`, { method: 'POST' }), context, 'Approved');
                reload();
            },
        }, 'Approve as second reviewer') : null);
}

async function renderOrganization(area, organization, context) {
    const id = organization.id;
    const reload = () => renderOrganization(area, organization, context);
    const url = `${BASE}/organizations/${id}`;
    const [runs, queue, reviews, guidelines, catalog] = await Promise.all([
        api(`${url}/antibiograms`),
        api(`${url}/culture-queue`),
        api(`${url}/stewardship-reviews`),
        api(`${url}/guidelines`),
        api(`${BASE}/catalog`),
    ]);
    const report = h('div', {});
    const upload = form(
        [{ name: 'file', label: 'Results CSV', type: 'file', required: true }],
        'Import results',
        async ({ file }) => {
            const data = new FormData();
            data.append('file', file);
            const done = await act(() => api(`${url}/imports`, { method: 'POST', form: data }), context);
            if (!done) return;
            show(report,
                h('p', {}, `${done.accepted} of ${done.total} rows imported.`),
                table(
                    [
                        { label: 'Line', number: true, cell: (row) => row.line ?? '' },
                        { label: 'Problem', cell: (row) => row.problems.join('; ') },
                    ],
                    done.rejected,
                    'No rows rejected.',
                ));
        },
        { inline: true },
    );
    const build = form(
        [
            { name: 'period_start', label: 'From', type: 'date', required: true },
            { name: 'period_end', label: 'To', type: 'date', required: true },
        ],
        'Build antibiogram',
        async (values) => {
            await act(() => api(`${url}/antibiograms`, { method: 'POST', body: values }), context, 'Draft built');
            reload();
        },
        { inline: true },
    );
    const review = form(
        [
            { name: 'patient_ref', label: 'Hospital patient reference', required: true },
            { name: 'intervention', label: 'Intervention', type: 'select', options: [['review_requested', 'Review requested'], ['de_escalation_considered', 'De-escalation considered'], ['duration_review', 'Duration review'], ['guideline_discussion', 'Guideline discussion'], ['other', 'Other']] },
            { name: 'finding', label: 'Finding', type: 'textarea', required: true },
            { name: 'recommendation', label: 'Your recommendation', type: 'textarea' },
        ],
        'Record review',
        async (values) => {
            await act(() => api(`${url}/stewardship-reviews`, { method: 'POST', body: values }), context, 'Recorded');
            reload();
        },
    );
    const guideline = form(
        [
            { name: 'title', label: 'Title', required: true },
            { name: 'issuer', label: 'Issued by', required: true },
            { name: 'version', label: 'Version', required: true },
            { name: 'effective_date', label: 'In effect from', type: 'date', required: true },
            { name: 'clinical_owner', label: 'Clinical owner', required: true },
        ],
        'Add guideline',
        async (values) => {
            await act(() => api(`${url}/guidelines`, { method: 'POST', body: values }), context, 'Added');
            reload();
        },
    );
    show(area,
        ...section('Import microbiology results',
            h('p', { class: 'muted' }, `Columns: ${catalog.columns.join(', ')}. Dates as 2026-10-31. Organism and antibiotic lists: ${catalog.review_status}.`),
            upload, report),
        ...section('Antibiograms', build, runs.map((run) => antibiogramView(run, context, reload))),
        ...section('Culture follow-up', table(
            [
                { label: 'Collected', cell: (row) => when(row.collected_on) },
                { label: 'Reference', cell: (row) => row.patient_ref },
                { label: 'Organism', cell: (row) => row.organism },
                { label: 'Specimen', cell: (row) => row.specimen },
                { label: 'State', cell: (row) => badge(row.state) },
                {
                    label: '',
                    cell: (row) => (row.state !== 'actioned' ? h('button', {
                        type: 'button',
                        class: 'quiet',
                        onclick: async () => {
                            await act(() => api(`${BASE}/culture-reviews/${row.id}/advance`, { method: 'POST' }), context);
                            reload();
                        },
                    }, 'Move to next step') : ''),
                },
            ],
            queue,
            'No cultures waiting.',
        )),
        ...section('Stewardship reviews', h('details', {}, h('summary', {}, 'Record a review'), h('div', { class: 'sheet' }, review)), table(
            [
                { label: 'Date', cell: (row) => when(row.created_at) },
                { label: 'Reference', cell: (row) => row.patient_ref },
                { label: 'Intervention', cell: (row) => row.intervention.replaceAll('_', ' ') },
                { label: 'Finding', cell: (row) => row.finding },
                { label: 'Outcome', cell: (row) => badge(row.disposition) },
            ],
            reviews,
            'None yet.',
        )),
        ...section('Guidelines', h('details', {}, h('summary', {}, 'Add a guideline'), h('div', { class: 'sheet' }, guideline)), table(
            [
                { label: 'Title', cell: (row) => row.title },
                { label: 'Issuer', cell: (row) => row.issuer },
                { label: 'Version', cell: (row) => row.version },
                { label: 'Owner', cell: (row) => row.clinical_owner },
            ],
            guidelines,
            'None yet.',
        )),
    );
}

export async function renderAmr(container, context) {
    const mine = await api('/api/organizations?mine=true');
    const area = h('div', {});
    const allowed = mine.filter((item) => ['admin', 'steward', 'microbiologist'].includes(item.my_role));
    const picker = allowed.length ? h('select', {
        'aria-label': 'Organization',
        onchange: (event) => renderOrganization(area, allowed.find((item) => item.id === event.target.value), context),
    }, allowed.map((item) => h('option', { value: item.id }, item.name))) : null;
    const create = form(
        [
            { name: 'name', label: 'Hospital or clinic', required: true },
            { name: 'type', label: 'Type', type: 'select', options: ['hospital', 'clinic', 'laboratory', 'pharmacy'] },
            { name: 'specialities', label: 'Specialities' },
        ],
        'Add organization',
        async (values) => {
            await act(() => api('/api/organizations', { method: 'POST', body: values }), context, 'Added');
            context.refresh();
        },
        { inline: true },
    );
    const member = allowed.some((item) => item.my_role === 'admin') ? form(
        [
            { name: 'email', label: 'Staff email', type: 'email', required: true },
            { name: 'role', label: 'Role', type: 'select', options: ['steward', 'microbiologist', 'coordinator', 'staff', 'admin'] },
        ],
        'Add member',
        async (values) => {
            await act(() => api(`/api/organizations/${picker.value}/members`, { method: 'POST', body: values }), context, 'Member added');
        },
        { inline: true },
    ) : null;
    show(container,
        h('h1', {}, 'AMR Guard'),
        h('p', { class: 'lede' }, 'For a hospital stewardship team: local resistance data, follow-up of cultures, and a record of stewardship reviews.'),
        h('div', { class: 'banner danger' }, 'Pilot, pending validation by a microbiology and stewardship team. It never chooses or doses an antibiotic. Aggregate figures are not treatment advice for an individual patient.'),
        picker ? h('p', { class: 'row' }, 'Organization: ', picker) : h('p', { class: 'empty' }, 'Join or add an organization to begin. Stewards and microbiologists are added by its admin.'),
        h('details', {}, h('summary', {}, 'Manage organizations'), h('div', { class: 'sheet' }, create, member)),
        area,
    );
    if (allowed.length) await renderOrganization(area, allowed[0], context);
    return undefined;
}
