// Where everyone lands after signing in: their patients, who is waiting, and every tool.
import { api, h, plural, show, when } from './ui.js';
import { STAGES, canUse } from './catalogue.js';

export function toolList(context) {
    return h('div', { class: 'stages' }, STAGES.map((stage) => {
        const tools = stage.tools.filter((tool) => canUse(tool, context));
        return tools.length ? h('section', { class: 'stage' },
            h('h3', {}, stage.title),
            tools.map((tool) => h('a', { class: 'tool-link', href: `#/${tool.id}` },
                h('strong', {}, tool.label),
                h('span', {}, tool.blurb)))) : null;
    }));
}

// A short route to the next tool, shown at the top of a patient's record.
export function visitStrip(context) {
    return h('nav', { class: 'visit-strip', 'aria-label': 'Tools for this patient' }, STAGES.map((stage) => {
        const tools = stage.tools.filter((tool) => canUse(tool, context));
        return tools.length ? h('div', {},
            h('span', { class: 'muted' }, stage.title),
            h('div', { class: 'row' }, tools.map((tool) => h('a', { href: `#/${tool.id}` }, tool.label)))) : null;
    }));
}

function patientFinder(context) {
    const { patients, openPatient, me } = context;
    const open = async (patient) => {
        await openPatient(patient.id);
        window.location.hash = '#/overview';
    };
    const list = h('ul', { class: 'patient-list' });
    const fill = (words) => {
        const found = patients
            .filter((patient) => `${patient.first_name} ${patient.last_name}`.toLowerCase().includes(words))
            .slice(0, 8);
        show(list, found.length
            ? found.map((patient) => h('li', {}, h('button', { type: 'button', class: 'patient-row', onclick: () => open(patient) },
                h('strong', {}, `${patient.first_name} ${patient.last_name}`),
                h('span', { class: 'muted' }, patient.date_of_birth ? `Born ${patient.date_of_birth}` : `ID ${patient.id.slice(0, 8)}`))))
            : h('li', { class: 'muted' }, patients.length ? 'No patient has that name.' : 'No patients yet. Add your first one to begin.'));
    };
    fill('');
    return [
        patients.length > 3 && h('label', {}, 'Find a patient',
            h('input', { type: 'search', oninput: (event) => fill(event.target.value.trim().toLowerCase()) })),
        list,
        h('p', {}, h('button', { type: 'button', class: 'quiet', onclick: () => context.startNewPatient() }, me.role === 'doctor' ? 'Add a patient' : 'Create or join a record')),
    ];
}

async function waitingRoom(context) {
    if (!canUse({ id: 'diagnofast', audience: 'staff' }, context)) return null;
    const intakes = await api('/api/diagnofast/intakes').catch(() => []);
    const waiting = intakes.slice(0, 5);
    return h('div', { class: 'sheet' },
        h('h2', {}, 'Waiting room'),
        waiting.length
            ? h('ul', { class: 'patient-list' }, waiting.map((intake) => h('li', {}, h('a', { class: 'patient-row', href: `#/diagnofast/${intake.id}` },
                h('strong', {}, `${intake.urgent.length ? 'Urgent: ' : ''}${intake.patient_name}`),
                h('span', { class: 'muted' }, when(intake.created_at))))))
            : h('p', { class: 'muted' }, 'Nobody is waiting. Send a patient a DiagnoFast link to start their interview.'),
        h('p', {}, h('a', { href: '#/diagnofast' }, intakes.length > 5 ? `See all ${plural(intakes.length, 'patient')}` : 'Open DiagnoFast')));
}

export async function renderHome(container, context) {
    const { me } = context;
    const waiting = await waitingRoom(context);
    show(container,
        h('h1', {}, `Hello, ${me.first_name}`),
        h('p', { class: 'lede' }, me.role === 'doctor'
            ? 'Open a patient, and every tool stays with them as you move through the visit.'
            : 'Your record, your medicines and your care, in one place.'),
        h('div', { class: 'columns' },
            h('div', { class: 'sheet' }, h('h2', {}, me.role === 'doctor' ? 'Your patients' : 'Your records'), patientFinder(context)),
            waiting),
        h('h2', {}, 'All tools'),
        toolList(context));
}

export function renderAccount(container, context) {
    const { me } = context;
    show(container,
        h('h1', {}, 'Account'),
        h('div', { class: 'sheet' },
            h('dl', { class: 'facts-list' },
                h('dt', {}, 'Name'), h('dd', {}, `${me.first_name} ${me.last_name}`),
                h('dt', {}, 'Email'), h('dd', {}, me.email),
                h('dt', {}, 'Account type'), h('dd', {}, me.role === 'doctor' ? 'Clinic staff' : 'Patient'))));
}
