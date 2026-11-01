// The care workspace shell: who is signed in, which patient is open, and which tool shows.
import { act, api, form, h, loading, show, toast } from './ui.js';
import { STAGES, SWITCHED_TOOLS, canUse } from './catalogue.js';
import { renderAccount, renderHome } from './home.js';
import { renderInbox, renderOverview, renderTasks } from './overview.js';
import { leaveRecording, renderClinScribe } from './clinscribe.js';
import { renderLabLens } from './lablens.js';
import { renderCareFlow } from './careflow.js';
import { renderDiagnoFast } from './diagnofast.js';
import { renderPrescription } from './prescription.js';
import { renderSkin } from './skin.js';
import { renderReferrals } from './referrals.js';
import { renderDischarge } from './discharge.js';
import { renderGovernance } from './governance.js';
import { renderReminders } from './reminders.js';
import { renderSettings } from './settings.js';

const RENDERERS = {
    home: renderHome,
    overview: renderOverview,
    tasks: renderTasks,
    reminders: renderReminders,
    inbox: renderInbox,
    account: renderAccount,
    careflow: renderCareFlow,
    governance: renderGovernance,
    settings: renderSettings,
    diagnofast: renderDiagnoFast,
    clinscribe: renderClinScribe,
    lablens: renderLabLens,
    skin: renderSkin,
    prescription: renderPrescription,
    referrals: renderReferrals,
    discharge: renderDischarge,
};
const LABELS = { inbox: 'Inbox', account: 'Account' };
const GROUPS = [
    { tools: [{ id: 'home', label: 'Home' }] },
    {
        title: 'This patient',
        tools: [
            { id: 'overview', label: 'Patient record' },
            { id: 'tasks', label: 'Tasks' },
            { id: 'reminders', label: 'Reminders' },
            { id: 'careflow', label: 'CareFlow' },
        ],
    },
    ...STAGES,
    {
        title: 'Manage',
        tools: [
            { id: 'governance', label: 'Governance', audience: 'staff' },
            { id: 'settings', label: 'Settings', audience: 'staff' },
        ],
    },
];
const TOOLS = Object.fromEntries(GROUPS.flatMap((group) => group.tools).map((tool) => [tool.id, tool]));

const state = { me: null, patients: [], patient: null, config: { tools: [], defaults: {} } };
const main = document.getElementById('main');

function currentRoute() {
    const [name, detail] = window.location.hash.replace('#/', '').split('/');
    return { name: RENDERERS[name] ? name : 'home', detail };
}

function renderRail() {
    const active = currentRoute().name;
    const rail = document.getElementById('rail');
    show(rail,
        GROUPS.map((group) => {
            const links = group.tools
                .filter((tool) => canUse(tool, state))
                .map((tool) => h('a', { href: `#/${tool.id}`, 'aria-current': tool.id === active ? 'page' : null }, tool.label));
            return links.length ? h('div', {}, group.title && h('h2', {}, group.title), links) : null;
        }),
        h('div', {}, h('h2', {}, 'Access'), h('a', { href: 'optikinesis_hiw.html' }, 'OptiKinesis')));
}

async function renderWristband() {
    const band = document.getElementById('wristband');
    const patient = state.patient;
    const picker = h('select', {
        'aria-label': 'Choose a patient',
        onchange: (event) => openPatient(event.target.value),
    },
    h('option', { value: '' }, state.patients.length ? 'Choose a patient' : 'No patients yet'),
    state.patients.map((item) => h('option', { value: item.id, selected: item.id === patient?.id }, `${item.first_name} ${item.last_name}`)));

    if (!patient) {
        show(band, h('span', { class: 'facts' }, 'No patient open'), picker);
        return;
    }
    const allergies = await api(`/api/patients/${patient.id}/allergies`).catch(() => []);
    const active = allergies.filter((item) => item.status === 'active');
    const sex = patient.sex && patient.sex !== 'unknown' ? patient.sex : null;
    const facts = [sex, patient.date_of_birth && `Born ${patient.date_of_birth}`, `ID ${patient.id.slice(0, 8)}`]
        .filter(Boolean).join(', ');
    show(band,
        h('span', { class: 'name' }, `${patient.first_name} ${patient.last_name}`),
        h('span', { class: 'facts' }, facts),
        active.length
            ? h('span', { class: 'allergy-band' }, `Allergies: ${active.map((item) => item.substance).join(', ')}`)
            : h('span', { class: 'facts' }, 'No allergies recorded'),
        picker,
    );
}

function askConsent(detail) {
    const notice = h('div', { class: 'sheet alert-card' },
        h('h2', { style: 'margin-top:0' }, 'Consent needed'),
        h('p', {}, detail.message),
        h('p', { class: 'muted' }, 'Record this only after the patient has agreed.'),
        h('div', { class: 'row' },
            h('button', {
                type: 'button',
                onclick: async () => {
                    const saved = await act(() => api(`/api/patients/${state.patient.id}/consents`, { method: 'POST', body: { purpose: detail.purpose } }), null, 'Consent recorded');
                    if (saved) render();
                },
            }, 'The patient agreed: record consent'),
            h('button', { type: 'button', class: 'quiet', onclick: () => notice.remove() }, 'Not now')));
    main.prepend(notice);
    notice.scrollIntoView({ block: 'center' });
}

async function render() {
    renderRail();
    const { name, detail } = currentRoute();
    const label = TOOLS[name]?.label ?? LABELS[name];
    document.title = name === 'home' ? 'Diagnosia' : `${label}: Diagnosia`;
    loading(main);
    if (SWITCHED_TOOLS.includes(name) && !state.config.tools.includes(name)) {
        show(main, h('h1', {}, label), h('p', { class: 'empty' }, 'This tool is switched off. An administrator can turn it on in Settings.'));
        return;
    }
    try {
        await RENDERERS[name](main, {
            ...state,
            route: detail,
            reloadConfig,
            isClinician: state.patient?.relationship === 'clinician',
            isStaff: state.me.role === 'doctor',
            askConsent,
            refresh: render,
            reloadPatients,
            openPatient,
            startNewPatient,
        });
    } catch (error) {
        show(main, h('p', { class: 'error' }, error.message));
    }
}

// The add-a-patient form is the patient record's empty state.
async function startNewPatient() {
    window.history.replaceState(null, '', '#/overview');
    await openPatient('');
}

async function reloadConfig() {
    state.config = await api('/api/platform/config');
}

async function reloadPatients() {
    state.patients = await api('/api/patients');
}

async function openPatient(patientId) {
    state.patient = state.patients.find((item) => item.id === patientId) || null;
    try {
        if (patientId) sessionStorage.setItem('care-patient', patientId);
        else sessionStorage.removeItem('care-patient');
    } catch (storageBlocked) { /* the page works without it */ }
    await renderWristband();
    await render();
}

async function refreshInbox() {
    const items = await api('/api/notifications').catch(() => []);
    const unread = items.filter((item) => !item.read_at).length;
    document.getElementById('inbox-count').textContent = unread || '';
}

document.getElementById('sign-out').addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    try {
        sessionStorage.clear();
    } catch (storageBlocked) { /* nothing to clear */ }
    window.location.href = 'index.html';
});

document.getElementById('open-nav').addEventListener('click', (event) => {
    const open = document.getElementById('rail').classList.toggle('open');
    event.target.setAttribute('aria-expanded', open);
});

window.addEventListener('hashchange', async () => {
    document.getElementById('rail').classList.remove('open');
    await leaveRecording(null);
    render();
    refreshInbox();
});

const STATUS_WORDS = {
    pending_verification: 'Your account is waiting for verification. Staff tools stay closed until an organization administrator confirms your credentials.',
    rejected: 'Your account was not verified. Contact your organization administrator.',
};

// Unverified staff see only this: the server refuses everything else anyway.
async function renderOnboarding() {
    const profile = await api('/api/practitioner');
    show(main,
        h('h1', {}, 'Account verification'),
        h('div', { class: 'banner' }, STATUS_WORDS[state.me.account_status]),
        h('div', { class: 'columns' },
            h('div', { class: 'sheet' },
                h('h2', { style: 'margin-top:0' }, 'I have an invitation'),
                form([{ name: 'code', label: 'Invitation code', required: true }], 'Join organization', async ({ code }) => {
                    await act(() => api('/api/staff/invitations/accept', { method: 'POST', body: { code } }), null, 'Invitation accepted. Your administrator still needs to verify you.');
                })),
            h('div', { class: 'sheet' },
                h('h2', { style: 'margin-top:0' }, 'About you'),
                h('p', { class: 'muted' }, 'Your administrator checks this against evidence. Saying it here proves nothing.'),
                form(
                    [
                        { name: 'profession', label: 'Profession', type: 'select', value: profile.profession, options: ['doctor', 'nurse', 'midwife', 'pharmacist', 'coordinator', 'community_health_worker', 'laboratory_professional'].map((item) => [item, item.replaceAll('_', ' ')]) },
                        { name: 'speciality', label: 'Speciality', value: profile.speciality },
                        { name: 'registration_number', label: 'Registration number', value: profile.registration_number },
                    ],
                    'Save details',
                    async (values) => {
                        await act(() => api('/api/practitioner', { method: 'PUT', body: values }), null, 'Details saved');
                    },
                ))));
}

async function start() {
    state.me = await api('/api/me');
    document.getElementById('who').textContent = `${state.me.first_name} ${state.me.last_name}`;
    if (state.me.role === 'doctor' && state.me.account_status !== 'verified') {
        await renderOnboarding();
        return;
    }
    await reloadConfig();
    await reloadPatients();
    let remembered = null;
    try {
        remembered = sessionStorage.getItem('care-patient');
    } catch (storageBlocked) { /* the page works without it */ }
    const first = state.me.role === 'patient' ? state.patients.find((item) => item.relationship === 'self') : null;
    await openPatient(state.patients.some((item) => item.id === remembered) ? remembered : first?.id ?? '');
    refreshInbox();
}

start().catch((error) => {
    if (error.status !== 401) toast(error.message, true);
});
