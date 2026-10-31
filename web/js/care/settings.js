// Settings: what an administrator can change without touching code.
import { act, api, badge, form, h, section, show } from './ui.js';

function control(setting, canEdit, context) {
    const save = async (value) => {
        await act(() => api(`/api/platform/settings/${encodeURIComponent(setting.key)}`, { method: 'PUT', body: { value } }), context, 'Saved');
        await context.reloadConfig();
        context.refresh();
    };
    const common = { disabled: !canEdit, 'aria-label': setting.label };
    if (setting.type === 'bool') {
        return h('input', { ...common, type: 'checkbox', checked: setting.value, onchange: (event) => save(event.target.checked) });
    }
    if (setting.type === 'choice') {
        return h('select', { ...common, onchange: (event) => save(event.target.value) },
            setting.choices.map((choice) => h('option', { value: choice, selected: choice === setting.value }, choice)));
    }
    if (setting.type === 'model') {
        const known = setting.choices.includes(setting.value) ? setting.choices : [setting.value, ...setting.choices];
        return h('select', { ...common, onchange: (event) => save(event.target.value) },
            known.map((choice) => h('option', { value: choice, selected: choice === setting.value }, choice)));
    }
    if (setting.type === 'int') {
        return h('input', { ...common, type: 'number', min: setting.min, max: setting.max, value: setting.value, onchange: (event) => save(Number(event.target.value)) });
    }
    if (setting.type === 'list') {
        return h('textarea', {
            ...common,
            placeholder: 'One heading per line',
            value: setting.value.join('\n'),
            onchange: (event) => save(event.target.value.split('\n').map((item) => item.trim()).filter(Boolean)),
        });
    }
    return h('textarea', { ...common, value: setting.value, onchange: (event) => save(event.target.value) });
}

const KEY_HELP = {
    groq: 'A free key from console.groq.com. It also runs speech to text and the English voice.',
    gemini: 'A free key from aistudio.google.com. It answers when Groq fails, or takes turns with it. It has no speech.',
};

function keyPanel(provider, key, canEdit, context) {
    const status = key.configured
        ? badge(key.source === 'environment' ? 'Set in the server environment' : `Saved, ending ${key.last_four}`, 'good')
        : badge('Not set', 'bad');
    const endpoint = `/api/platform/ai-key?provider=${provider}`;
    const entry = canEdit && key.source !== 'environment' ? form(
        [{ name: 'key', label: `${key.label} API key`, type: 'password', required: true }],
        'Save key',
        async (values, element) => {
            const saved = await act(() => api(endpoint, { method: 'PUT', body: values }), context, 'Key saved');
            if (saved) {
                element.reset();
                await context.reloadConfig();
                context.refresh();
            }
        },
        { inline: true },
    ) : null;
    return h('div', { class: 'sheet' },
        h('h3', { style: 'margin-top:0' }, key.label),
        h('p', {}, 'Status: ', status),
        h('p', { class: 'muted' }, `${KEY_HELP[provider]} It is stored encrypted and never shown again.`),
        entry,
        key.source === 'settings' && canEdit ? h('button', {
            type: 'button',
            class: 'quiet',
            onclick: async () => {
                await act(() => api(endpoint, { method: 'DELETE' }), context, 'Key removed');
                await context.reloadConfig();
                context.refresh();
            },
        }, 'Remove key') : null);
}

export async function renderSettings(container, context) {
    const data = await api('/api/platform/settings');
    const sections = {};
    data.settings.forEach((setting) => { (sections[setting.section] ||= []).push(setting); });
    show(container,
        h('h1', {}, 'Settings'),
        h('p', { class: 'lede' }, data.can_edit ? 'Changes apply straight away, and each is logged.' : 'Only an administrator of a verified organization can change these.'),
        section('Getting started', h('div', { class: 'table-wrap' }, h('table', {}, h('tbody', {}, data.setup.map((step) => h('tr', {},
            h('td', {}, step.name),
            h('td', {}, step.done ? badge('Done', 'good') : badge('To do', 'bad')),
            h('td', { class: 'muted' }, step.done ? '' : step.help))))))),
        section('AI keys', h('div', { class: 'settings-grid' }, Object.entries(data.ai_keys).map(([provider, key]) => keyPanel(provider, key, data.can_edit, context)))),
        Object.entries(sections).map(([title, settings]) => section(title, h('div', { class: 'settings-grid' }, settings.map((setting) => h('label', {}, setting.label, control(setting, data.can_edit, context)))))),
    );
    return undefined;
}
