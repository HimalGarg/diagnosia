// CareFlow: what happened across the tools, in order. A read-only activity view, not a clinical record.
import { api, badge, h, loading, show, when } from './ui.js';

const REFRESH_MS = 15000;
const STATUS_LABEL = { started: 'Started', completed: 'Completed', review_required: 'Needs review', failed: 'Failed' };
const STATUS_KIND = { completed: 'good', failed: 'bad', review_required: 'bad' };
const SOURCE_LABEL = {
    mongodb_atlas: 'Timeline from MongoDB Atlas.',
    mongodb_local: 'Timeline from a local MongoDB test server, not Atlas.',
    local_outbox: 'Timeline from this server\'s local copy. MongoDB Atlas isn\'t connected.',
};
const STATE_LABEL = { CONNECTED: ['Connected', 'good'], DISABLED: ['Off', ''], BLOCKED: ['Blocked', 'bad'], FAILED: ['Failed', 'bad'] };
const CHANNEL = { MONGODB_ATLAS: 'mongodb', YUGABYTEDB: 'yugabyte', MEKO: 'meko' };
const SERVICE_NAME = { MONGODB_ATLAS: 'MongoDB Atlas', YUGABYTEDB: 'YugabyteDB', MEKO: 'Meko' };
let timer = null;
let stream = null;

function words(eventType) {
    const text = eventType.replaceAll('.', ' ').replaceAll('_', ' ');
    return text.charAt(0).toUpperCase() + text.slice(1);
}

function row(event) {
    return h('li', {},
        h('strong', {}, words(event.event_type)),
        ' ',
        badge(STATUS_LABEL[event.status] ?? event.status, STATUS_KIND[event.status] ?? ''),
        event.review_required && event.status !== 'review_required' ? [' ', badge('Needs review', 'bad')] : null,
        h('div', { class: 'muted' },
            `${event.module}${event.target_module ? ` to ${event.target_module}` : ''}, ${when(event.occurred_at)}`));
}

function counts(events, pending) {
    const total = (status) => events.filter((event) => event.status === status).length;
    const waiting = pending === undefined ? '' : ` ${pending} still waiting for a person.`;
    return h('p', { class: 'muted' },
        `${events.length} recorded. ${total('completed')} completed, ${total('review_required')} asked for review, ${total('failed')} failed.${waiting}`);
}

function handoffList(handoffs) {
    if (!handoffs.length) return h('p', { class: 'muted' }, 'No handoffs between tools in this workflow.');
    return h('ul', { class: 'plain-list' }, handoffs.map((item) => h('li', {},
        h('strong', {}, `${item.source_module} to ${item.target_module}`),
        h('div', { class: 'muted' }, `${when(item.occurred_at)}. The receiving tool re-reads the source record.`),
        h('div', { class: 'muted' }, Object.entries(item.records).map(([channel, record]) => `${channel}: ${record.state}`).join(', ')))));
}

function sourceNote(reply) {
    return h('p', { class: 'muted' },
        SOURCE_LABEL[reply.source] ?? reply.source,
        reply.degraded ? ' Atlas didn\'t answer, so this is the copy held here.' : '');
}

function healthPanel(health) {
    const stream = health.change_stream;
    return [
        h('div', { class: 'table-wrap' }, h('table', {},
            h('thead', {}, h('tr', {}, ['Service', 'State', 'Detail', 'Waiting', 'Delivered', 'Failed'].map((label) => h('th', {}, label)))),
            h('tbody', {}, health.integrations.map((item) => {
                const queue = health.deliveries[CHANNEL[item.name]];
                const [label, kind] = STATE_LABEL[item.state] ?? [item.state, ''];
                return h('tr', {}, h('td', {}, SERVICE_NAME[item.name] ?? item.name), h('td', {}, badge(label, kind)), h('td', { class: 'muted' }, item.detail), h('td', {}, queue.pending), h('td', {}, queue.delivered), h('td', {}, queue.dead));
            })))),
        h('p', { class: 'muted' }, stream.connected
            ? `Live updates are on, through a change stream on ${stream.collection}.`
            : 'Live updates are off, so this page refreshes every 15 seconds.'),
        h('p', { class: 'muted' }, `${health.agent_runs_waiting} AI run records waiting to be copied.`),
    ];
}

function operationsPanel(reply) {
    const figures = reply.figures;
    const average = figures.average_duration_ms === null || figures.average_duration_ms === undefined ? 'none yet' : `${Math.round(figures.average_duration_ms)} ms`;
    return h('p', { class: 'muted' },
        `AI runs from ${reply.source === 'yugabytedb' ? 'YugabyteDB' : 'this server\'s local records'}: ${figures.total_runs ?? 0} total, ${figures.successful_runs ?? 0} succeeded, ${figures.failed_runs ?? 0} failed, ${figures.interrupted_runs ?? 0} interrupted, ${figures.fallback_runs ?? 0} used a fallback. Average time ${average}. ${figures.handoff_count ?? 0} handoffs.`);
}

export async function renderCareFlow(container, context) {
    clearInterval(timer);
    stream?.close();
    const { patient, isStaff } = context;
    const list = await api('/api/careflow/workflows');
    const results = h('div', { 'aria-live': 'polite' });
    const handoffPanel = h('div', {});
    const status = h('div', {});
    const own = list.workflows.find((item) => item.patient_id && item.patient_id === patient?.id);
    const choice = { workflow: own?.workflow_ref ?? '', module: '', status: '' };

    const load = async () => {
        // Not awaited, so a service that's slow to answer can't hold up the timeline.
        if (isStaff) {
            Promise.all([
                api('/api/careflow/health'),
                api('/api/careflow/operations').catch(() => null),
            ]).then(([health, figures]) => show(status, healthPanel(health), figures && operationsPanel(figures)))
                .catch(() => show(status, h('p', { class: 'muted' }, 'Integration status isn\'t available.')));
        }
        try {
            if (choice.workflow) {
                const [reply, handed] = await Promise.all([
                    api(`/api/careflow/workflows/${choice.workflow}/timeline`),
                    api(`/api/careflow/workflows/${choice.workflow}/handoffs`),
                ]);
                const shown = reply.events.filter((event) => (!choice.module || event.module === choice.module) && (!choice.status || event.status === choice.status));
                show(results, sourceNote(reply), counts(shown, reply.activity.pending_review), shown.length
                    ? h('ol', { class: 'timeline' }, shown.map(row))
                    : h('p', { class: 'empty' }, 'Nothing matches these filters.'));
                show(handoffPanel, h('h2', {}, 'Handoffs between tools'), handoffList(handed.handoffs));
                return;
            }
            show(handoffPanel);
            const query = new URLSearchParams(Object.entries({ module: choice.module, status: choice.status }).filter(([, value]) => value));
            const reply = await api(`/api/careflow/events?${query}`);
            show(results, sourceNote(reply), counts(reply.events), reply.events.length
                ? h('ol', { class: 'timeline' }, reply.events.map(row))
                : h('p', { class: 'empty' }, 'No activity yet. Using a tool on a patient adds it here.'));
        } catch (error) {
            show(results, h('p', { class: 'error', role: 'alert' }, `The timeline couldn't load. ${error.message}`));
        }
    };
    const picker = (label, key, options) => h('label', {}, label, h('select', {
        onchange: (event) => {
            choice[key] = event.target.value;
            loading(results);
            load();
        },
    }, options.map(([value, text]) => h('option', { value, selected: value === choice[key] }, text))));

    show(container,
        h('h1', {}, 'CareFlow'),
        h('p', { class: 'lede' }, 'What happened across the tools, in order. This is an activity view. The patient record stays the official source.'),
        h('div', { class: 'filters' },
            picker('Workflow', 'workflow', [['', 'All my workflows'], ...list.workflows.map((item) => [item.workflow_ref, `${item.label} (${item.event_count})`])]),
            picker('Tool', 'module', [['', 'All tools'], ...list.modules.map((name) => [name, name])]),
            picker('Status', 'status', [['', 'Any status'], ...Object.entries(STATUS_LABEL)])),
        results,
        handoffPanel,
        isStaff && [h('h2', {}, 'Integration status'), status]);
    await load();
    timer = setInterval(() => {
        if (window.location.hash !== '#/careflow') {
            clearInterval(timer);
            stream?.close();
        } else if (!document.hidden) {
            load();
        }
    }, REFRESH_MS);
    // Instant updates where the deployment supports them. Without them the timer above carries on.
    stream = new EventSource('/api/careflow/stream');
    stream.onmessage = () => load();
    stream.onerror = () => stream.close();
}
