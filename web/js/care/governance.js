// Governance: staff verification, the medication rule register, LOINC review and readiness.
// Every control here is enforced again on the server. Nothing is decided in the browser.
import { act, api, ask, badge, form, h, plural, section, show, table, when } from './ui.js';

const RULES = '/api/addons/medguard/rules';
const MAPPINGS = '/api/addons/lablens/mappings';
const PROFESSIONS = ['doctor', 'nurse', 'midwife', 'pharmacist', 'coordinator', 'community_health_worker', 'microbiologist', 'laboratory_professional'];

function staffPanel(organization, members, context) {
    const url = `/api/organizations/${organization.id}`;
    const decide = (member, action, label) => h('button', {
        type: 'button',
        class: 'quiet',
        onclick: async () => {
            const reason = await ask(`Why ${label.toLowerCase()} ${member.first_name} ${member.last_name}?`);
            if (!reason) return;
            await act(() => api(`${url}/staff/${member.user_id}/${action}`, { method: 'POST', body: { reason } }), context, 'Recorded');
            context.refresh();
        },
    }, label);
    const verifier = (member) => form(
        [
            { name: 'verified_profession', label: 'Profession you checked', type: 'select', options: PROFESSIONS.map((item) => [item, item.replaceAll('_', ' ')]), value: member.claimed_profession },
            { name: 'evidence_type', label: 'What you checked', required: true, placeholder: 'For example, state council register' },
            { name: 'evidence_reference', label: 'Reference', required: true },
        ],
        'Record verification',
        async (values) => {
            await act(() => api(`${url}/staff/${member.user_id}/verify`, { method: 'POST', body: values }), context, 'Verified');
            context.refresh();
        },
        { inline: true },
    );
    const invite = form(
        [
            { name: 'email', label: 'Staff email', type: 'email', required: true },
            { name: 'role', label: 'Role here', type: 'select', options: ['staff', 'coordinator', 'steward', 'microbiologist', 'admin'] },
        ],
        'Create invitation',
        async (values, element) => {
            const made = await act(() => api(`${url}/invitations`, { method: 'POST', body: values }), context);
            if (made) element.after(h('p', { class: 'muted' }, 'Send this code to them once. It lasts 7 days: ', h('strong', {}, made.code)));
        },
        { inline: true },
    );
    return h('div', { class: 'sheet' },
        h('h3', { style: 'margin-top:0' }, organization.name),
        invite,
        table(
            [
                { label: 'Name', cell: (row) => `${row.first_name} ${row.last_name}` },
                { label: 'Says they are', cell: (row) => [row.claimed_profession || 'Not stated', row.registration_number ? h('div', { class: 'muted' }, row.registration_number) : null] },
                { label: 'Status', cell: (row) => (row.account_status === 'pending_verification' ? h('mark', {}, 'Pending') : badge(row.account_status, row.account_status === 'verified' ? 'good' : 'bad')) },
                {
                    label: '',
                    cell: (row) => {
                        if (row.user_id === context.me.id) return 'You';
                        if (row.account_status === 'pending_verification') return [verifier(row), decide(row, 'reject', 'Reject')];
                        if (row.account_status === 'verified') return decide(row, 'suspend', 'Suspend');
                        if (row.account_status === 'suspended') return decide(row, 'reinstate', 'Reinstate');
                        return '';
                    },
                },
            ],
            members,
        ));
}

function ruleRow(rule, canReview, context) {
    const post = (action, body, message) => async () => {
        await act(() => api(`${RULES}/${rule.id}/${action}`, { method: 'POST', body }), context, message);
        context.refresh();
    };
    const withNote = (action, label) => h('button', {
        type: 'button',
        class: 'quiet',
        onclick: async () => {
            const note = await ask(`${label}: your reasoning`);
            if (note) post(action, { note }, `${label} recorded`)();
        },
    }, label);
    const actions = [];
    if (canReview && ['draft', 'rejected'].includes(rule.status)) {
        actions.push(h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                const source = await ask('Evidence source');
                const version = source && await ask('Evidence version or edition');
                const date = version && await ask('Publication or review date, like 2026-10-31');
                if (!date) return;
                await act(() => api(`${RULES}/${rule.id}/evidence`, {
                    method: 'PATCH',
                    body: { evidence_source: source, evidence_version: version, evidence_date: date },
                }), context, 'Evidence saved');
                context.refresh();
            },
        }, 'Add evidence'));
        if (rule.evidence_complete) actions.push(h('button', { type: 'button', class: 'quiet', onclick: post('submit', undefined, 'Submitted') }, 'Submit for review'));
    }
    if (canReview && rule.status === 'pending_pharmacist_review') actions.push(withNote('approve', 'Approve'), withNote('reject', 'Reject'));
    if (canReview && rule.status === 'approved') actions.push(withNote('retire', 'Retire'));
    return { rule, actions };
}

function mappingActions(mapping, canReview, context) {
    if (!canReview) return '';
    const post = (action, body) => async () => {
        await act(() => api(`${MAPPINGS}/${encodeURIComponent(mapping.id)}/${action}`, { method: 'POST', body }), context, 'Recorded');
        context.refresh();
    };
    return h('div', { class: 'row' },
        mapping.status === 'proposed' ? h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                const source_version = await ask('LOINC release you checked this against');
                if (source_version) post('approve', { source_version })();
            },
        }, 'Approve') : null,
        h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                const code = await ask('Correct LOINC code');
                const source_version = code && await ask('LOINC release');
                const note = source_version && await ask('Why?');
                if (note) post('correct', { code, source_version, note })();
            },
        }, 'Correct'),
        mapping.status !== 'rejected' ? h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                const note = await ask('Why reject this mapping?');
                if (note) post('reject', { note })();
            },
        }, 'Reject') : null);
}

export async function renderGovernance(container, context) {
    const [mine, profile, organizations, rules, mappings] = await Promise.all([
        api('/api/staff/me'),
        api('/api/practitioner'),
        api('/api/organizations?mine=true'),
        api(RULES),
        api(MAPPINGS),
    ]);
    const adminOf = organizations.filter((item) => item.my_role === 'admin' && item.verification_status === 'verified');
    const panels = await Promise.all(adminOf.map(async (item) => staffPanel(item, await api(`/api/organizations/${item.id}/staff`), context)));
    const isPharmacist = profile.verified_profession === 'pharmacist';
    const isLab = profile.verified_profession === 'laboratory_professional';
    const ruleRows = rules.map((rule) => ruleRow(rule, isPharmacist, context));
    const readiness = adminOf.length ? await api('/api/system/readiness') : null;
    const approved = rules.filter((rule) => rule.status === 'approved').length;
    const reviewedMappings = mappings.filter((item) => item.status === 'approved').length;

    show(container,
        h('h1', {}, 'Governance'),
        h('p', { class: 'lede' }, `Signed in as ${(mine.verified_profession || 'unverified').replaceAll('_', ' ')}. These records hold people's decisions. The software doesn't make them.`),
        readiness ? section('Readiness for real patients',
            h('div', { class: 'banner danger' }, `${readiness.passed} of ${readiness.total} checks pass. ${readiness.ready_for_real_patients ? 'All checks pass.' : 'Real patient use is blocked.'}`),
            h('details', {}, h('summary', {}, 'Show every check'), table(
                [
                    { label: 'Check', cell: (row) => row.name.replace('signoff:', '').replaceAll('_', ' ') },
                    { label: 'Result', cell: (row) => (row.passed ? badge('passed', 'good') : badge('failed', 'bad')) },
                    { label: 'Detail', cell: (row) => row.detail },
                    { label: 'Owner', cell: (row) => row.owner },
                ],
                readiness.checks,
            ))) : null,
        adminOf.length ? section('Staff verification', panels) : null,
        section('Medication rules',
            h('div', { class: 'banner' }, `${plural(approved, 'rule')} approved of ${rules.length}. Unapproved rules never raise a clinical alert. ${isPharmacist ? '' : 'Only verified pharmacists can review these.'}`),
            table(
                [
                    { label: 'Rule', cell: (row) => [row.rule.id, h('div', { class: 'muted' }, row.rule.category.replaceAll('_', ' '))] },
                    { label: 'Status', cell: (row) => (row.rule.status === 'approved' ? badge('approved', 'good') : h('mark', {}, row.rule.status.replaceAll('_', ' '))) },
                    { label: 'Evidence', cell: (row) => (row.rule.evidence_complete ? `${row.rule.evidence_source}, ${row.rule.evidence_version} (${row.rule.evidence_date})` : 'None recorded') },
                    { label: 'Reviewer', cell: (row) => (row.rule.reviewer_name ? `${row.rule.reviewer_name}, ${when(row.rule.last_reviewed_at)}` : '') },
                    { label: '', cell: (row) => h('div', { class: 'row' }, row.actions) },
                ],
                ruleRows,
            )),
        section('LOINC mappings',
            h('div', { class: 'banner' }, `${plural(reviewedMappings, 'mapping')} approved of ${mappings.length}. Unreviewed codes are left out of clinical exports. ${isLab ? '' : 'Only verified laboratory professionals can review these.'}`),
            table(
                [
                    { label: 'Local test', cell: (row) => row.local_label },
                    { label: 'Proposed code', cell: (row) => row.proposed_code },
                    { label: 'Source', cell: (row) => [row.source, row.source_version ? ` ${row.source_version}` : ''] },
                    { label: 'Status', cell: (row) => (row.status === 'approved' ? badge('approved', 'good') : row.status === 'rejected' ? badge('rejected', 'bad') : h('mark', {}, 'Unreviewed')) },
                    { label: '', cell: (row) => mappingActions(row, isLab, context) },
                ],
                mappings,
            )));
    return undefined;
}
