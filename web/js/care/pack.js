// Patient pack: one patient's finished records in one place, with an AI brief for doctors.
// Every AI statement shows its sources, and "not found" must never read as "none".
import { act, api, badge, form, h, show, when } from './ui.js';

const STATE_WORDS = {
    ready: 'In the pack',
    queued: 'Waiting to sync',
    indexing: 'Syncing',
    held: 'Held for a person to check',
    failed: "Couldn't be added",
};

function recordText(patientId, source) {
    const body = h('div', {}, h('p', { class: 'muted' }, 'Loading…'));
    const detail = h('details', { class: 'pack-source' }, h('summary', {}, `${source.label}, ${when(source.date)}`), body);
    let loaded = false;
    detail.addEventListener('toggle', async () => {
        if (!detail.open || loaded) return;
        loaded = true;
        try {
            const record = await api(`/api/patients/${patientId}/pack/items/${source.item_id}`);
            show(body,
                h('div', { class: 'pack-record' }, record.text),
                record.source_type === 'document' && h('a', { href: `/api/documents/${record.source_id}/file` }, 'Open the file'));
        } catch (error) {
            loaded = false;
            show(body, h('p', { class: 'muted' }, error.message));
        }
    });
    return detail;
}

function statement(patientId, item) {
    return h('div', { class: 'medicine-card' },
        h('p', {}, item.text),
        item.verdict !== 'supported' && badge('Check this against the source', 'bad'),
        item.sources.map((source) => recordText(patientId, source)));
}

function factsBlock(facts) {
    return h('div', { class: 'sheet' },
        h('h3', { style: 'margin-top:0' }, 'Allergies'),
        facts.allergies.length
            ? h('ul', {}, facts.allergies.map((item) => h('li', {}, [item.substance, item.reaction, item.severity !== 'unknown' ? item.severity : ''].filter(Boolean).join(', '))))
            : h('p', { class: 'muted' }, "None recorded in Diagnosia. That doesn't mean there are none."),
        h('h3', {}, 'Medicines the patient saved'),
        facts.medicines.length
            ? [h('ul', {}, facts.medicines.map((item) => h('li', {}, item))),
                h('p', { class: 'muted' }, `Saved ${when(facts.medicines_saved_on)}. Confirmed by the patient, not checked by a clinician.`)]
            : h('p', { class: 'muted' }, "None saved. That doesn't mean the patient takes none."));
}

function caveats(found) {
    return [
        found.still_syncing > 0 && h('div', { class: 'banner' }, `${found.still_syncing} more still syncing, so this may be incomplete.`),
        found.records_left_out > 0 && h('div', { class: 'banner' }, `${found.records_left_out} of the oldest records didn't fit in this brief.`),
        found.statements_dropped > 0 && h('div', { class: 'banner' }, `${found.statements_dropped} AI statements were removed because they couldn't be traced to a record.`),
    ];
}

function briefView(patientId, found) {
    return [
        h('p', {}, h('mark', {}, 'AI summary of existing records. Check each statement against its source. It is not a diagnosis or advice.')),
        ...caveats(found),
        factsBlock(found.facts),
        found.sections.length
            ? found.sections.map((part) => h('div', {}, h('h3', {}, part.heading), part.statements.map((item) => statement(patientId, item))))
            : h('p', { class: 'muted' }, found.records_used ? 'The AI found nothing it could trace to a record.' : 'No records have reached this pack yet.'),
    ];
}

function askBox(patientId, context) {
    const answer = h('div', {});
    const box = form(
        [{ name: 'question', label: 'Ask about this patient\'s records', required: true, maxlength: 500, placeholder: 'Any past reactions to antibiotics?' }],
        'Ask',
        async ({ question }) => {
            const found = await act(() => api(`/api/patients/${patientId}/pack/ask`, { method: 'POST', body: { question } }), context);
            if (!found) return;
            show(answer,
                ...caveats(found),
                found.found_in_records
                    ? found.statements.map((item) => statement(patientId, item))
                    : h('p', { class: 'muted' }, `Nothing matched in ${found.records_searched} records. Search can miss things, so this doesn't mean it never happened.`));
        },
    );
    return h('div', {}, box, answer);
}

export async function packPanel(patientId, context) {
    let pack;
    try {
        pack = await api(`/api/patients/${patientId}/pack`);
    } catch (error) {
        return h('p', { class: 'muted' }, error.message);
    }
    if (!pack.enabled) return h('p', { class: 'muted' }, "Patient packs aren't switched on.");
    const isDoctor = context?.me?.role === 'doctor';
    const list = h('details', {}, h('summary', {}, `What's in this pack (${pack.items.length})`),
        pack.items.length
            ? h('ul', {}, pack.items.map((item) => h('li', {}, `${item.label}, ${when(item.created_at)}: ${STATE_WORDS[item.state]}`)))
            : h('p', { class: 'muted' }, 'Nothing yet.'));
    if (!pack.stored) {
        return h('div', {}, h('p', { class: 'muted' }, 'Records are kept in Diagnosia only. A pack needs the patient\'s agreement, under Consent.'), list);
    }
    if (!isDoctor) return h('div', {}, list);
    const output = h('div', {});
    const write = h('button', { type: 'button' }, 'Write a brief');
    write.addEventListener('click', async () => {
        write.disabled = true;
        write.textContent = 'Writing…';
        const found = await act(() => api(`/api/patients/${patientId}/pack/brief`, { method: 'POST' }), context);
        write.disabled = false;
        write.textContent = 'Write a brief';
        if (found) show(output, briefView(patientId, found));
    });
    return h('div', {}, list, h('div', { class: 'row', style: 'margin:8px 0' }, write), output, askBox(patientId, context));
}
