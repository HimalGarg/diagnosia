// Small helpers for the care workspace. Everything shown on screen goes through
// h(), which uses text nodes, so patient or AI text can never become markup.

export class ApiError extends Error {
    constructor(status, detail) {
        const message = typeof detail === 'string' ? detail : detail?.message;
        super(message || 'Something went wrong. Please try again.');
        this.status = status;
        this.detail = detail;
    }
}

export async function api(path, options = {}) {
    const request = { method: options.method || 'GET', headers: {} };
    if (options.form) {
        request.body = options.form;
    } else if (options.body !== undefined) {
        request.headers['Content-Type'] = 'application/json';
        request.body = JSON.stringify(options.body);
    }
    let response;
    try {
        response = await fetch(path, request);
    } catch (networkError) {
        throw new ApiError(0, "We couldn't reach the server. Check the connection and try again.");
    }
    if (response.status === 401) {
        const place = encodeURIComponent(`care.html${window.location.hash}`);
        window.location.href = `login.html?next=${place}`;
        throw new ApiError(401, 'Please sign in.');
    }
    const text = await response.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch (notJson) {
        data = text;
    }
    if (!response.ok) {
        const detail = Array.isArray(data?.detail) ? 'Please check the details you entered.' : data?.detail;
        throw new ApiError(response.status, detail);
    }
    return data;
}

export function h(tag, attributes, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') element.className = value;
        else if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
        else if (key in element && key !== 'list') element[key] = value;
        else element.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat(Infinity)) {
        if (child === null || child === undefined || child === false) continue;
        element.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return element;
}

// Puts children in a container. Unlike replaceChildren, it skips empty parts and flattens lists.
export function show(container, ...children) {
    const parts = children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false);
    container.replaceChildren(...parts);
}

// A question inside the page, in place of the browser's bare prompt box.
export function ask(question, { value = '', required = true } = {}) {
    return new Promise((resolve) => {
        const input = h('textarea', { value, 'aria-label': question, rows: 3 });
        const dialog = h('dialog', { class: 'ask' });
        const finish = (answer) => {
            dialog.close();
            dialog.remove();
            resolve(answer);
        };
        const submit = () => {
            const answer = input.value.trim();
            if (answer || !required) finish(answer);
            else input.focus();
        };
        dialog.append(
            h('p', {}, question),
            input,
            h('div', { class: 'row' },
                h('button', { type: 'button', onclick: submit }, 'Save'),
                h('button', { type: 'button', class: 'quiet', onclick: () => finish(null) }, 'Cancel')),
        );
        dialog.addEventListener('cancel', (event) => {
            event.preventDefault();
            finish(null);
        });
        document.body.append(dialog);
        dialog.showModal();
        input.focus();
    });
}

function browserVoice(text, language) {
    if (!window.speechSynthesis) return false;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = language;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    return true;
}

// Groq reads English. Hindi, and English when Groq can't, use the browser's own voice. Gemini has no speech.
export async function readAloud(text, language) {
    if (language !== 'en-IN') return browserVoice(text, language);
    try {
        const response = await fetch('/api/prescription/speak', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, language }),
        });
        if (!response.ok) throw new Error('no audio');
        await new Audio(URL.createObjectURL(await response.blob())).play();
        return true;
    } catch (unavailable) {
        return browserVoice(text, language);
    }
}

export function plural(count, word) {
    return `${count} ${word}${count === 1 ? '' : 's'}`;
}

export function toast(message, bad = false) {
    const element = document.getElementById('toast');
    element.textContent = message;
    element.classList.toggle('bad', bad);
    element.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove('show'), 4000);
}

// Runs an action, shows any failure, and offers to record consent when that is what's missing.
export async function act(work, context, success) {
    try {
        const result = await work();
        if (success) toast(success);
        return result;
    } catch (error) {
        if (error.detail?.code === 'consent_required' && context?.askConsent) {
            context.askConsent(error.detail);
        } else {
            toast(error.message, true);
        }
        return undefined;
    }
}

// Builds a form from a list of fields. onSubmit gets the values by field name.
export function form(fields, submitLabel, onSubmit, { inline = false } = {}) {
    const controls = {};
    const element = h('form', { class: inline ? 'inline' : 'stack' });
    for (const field of fields) {
        const { name, label, type = 'text', options, value, required, placeholder, min, max, step, maxlength } = field;
        let control;
        if (type === 'select') {
            control = h('select', { name, required },
                options.map((option) => {
                    const [optionValue, text] = Array.isArray(option) ? option : [option, option];
                    return h('option', { value: optionValue, selected: optionValue === value }, text);
                }));
        } else if (type === 'textarea') {
            control = h('textarea', { name, required, placeholder, maxLength: maxlength }, value ?? '');
        } else if (type === 'checkbox') {
            control = h('input', { name, type, checked: !!value });
            element.append(h('label', { class: 'tick' }, control, label));
            controls[name] = control;
            continue;
        } else {
            control = h('input', { name, type, required, placeholder, value: value ?? '', min, max, step, maxLength: maxlength });
        }
        controls[name] = control;
        element.append(h('label', {}, label, control));
    }
    const submit = h('button', { type: 'submit' }, submitLabel);
    element.append(submit);
    element.addEventListener('submit', async (event) => {
        event.preventDefault();
        const values = {};
        for (const [name, control] of Object.entries(controls)) {
            if (control.type === 'checkbox') values[name] = control.checked;
            else if (control.type === 'file') values[name] = control.files[0];
            else if (control.value !== '') values[name] = control.type === 'number' ? Number(control.value) : control.value;
        }
        submit.disabled = true;
        try {
            await onSubmit(values, element);
        } finally {
            submit.disabled = false;
        }
    });
    return element;
}

export function table(columns, rows, emptyMessage = 'Nothing here yet.') {
    if (!rows.length) return h('p', { class: 'muted' }, emptyMessage);
    return h('div', { class: 'table-wrap' },
        h('table', {},
            h('thead', {}, h('tr', {}, columns.map((column) => h('th', { scope: 'col' }, column.label)))),
            h('tbody', {}, rows.map((row) => h('tr', {},
                columns.map((column) => h('td', { class: column.number ? 'number' : null }, column.cell(row))))))));
}

export function badge(text, kind = '') {
    return h('span', { class: `status ${kind}` }, text.replaceAll('_', ' '));
}

export function when(value) {
    if (!value) return '';
    const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
    if (Number.isNaN(date.getTime())) return value;
    const options = { day: 'numeric', month: 'short', year: 'numeric' };
    if (value.length > 10) Object.assign(options, { hour: 'numeric', minute: '2-digit' });
    return date.toLocaleString('en-IN', options);
}

function day(value) {
    return when(value).split(',')[0];
}

export function section(title, ...children) {
    return [h('h2', {}, title), ...children];
}

export function loading(container) {
    container.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
}

// One test or measure over time. Values also stay readable as a table.
export function trendChart({ label, unit, points, low, high, describe }) {
    const width = 340;
    const height = 150;
    const margin = { top: 10, right: 12, bottom: 22, left: 44 };
    const values = points.map((point) => point.value);
    const candidates = [...values, ...(low != null ? [low] : []), ...(high != null ? [high] : [])];
    let bottom = Math.min(...candidates);
    let top = Math.max(...candidates);
    if (bottom === top) {
        const spread = Math.abs(bottom) * 0.1 || 1;
        bottom -= spread;
        top += spread;
    }
    const pad = (top - bottom) * 0.1;
    bottom -= pad;
    top += pad;
    const times = points.map((point) => new Date(point.date).getTime());
    const start = Math.min(...times);
    const end = Math.max(...times);
    const plotWidth = width - margin.left - margin.right;
    // Readings within a day would stack into a vertical line, so those are spaced evenly.
    const evenly = end - start < 86400000;
    const positions = times.map((time, index) => {
        if (points.length === 1) return margin.left + plotWidth / 2;
        const share = evenly ? index / (points.length - 1) : (time - start) / (end - start);
        return margin.left + share * plotWidth;
    });
    const y = (value) => margin.top + ((top - value) / (top - bottom)) * (height - margin.top - margin.bottom);

    const svgNS = 'http://www.w3.org/2000/svg';
    const make = (tag, attributes, text) => {
        const element = document.createElementNS(svgNS, tag);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        if (text !== undefined) element.textContent = text;
        return element;
    };
    const svg = make('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${label} over time, in ${unit}. A table of values follows.` });
    if (low != null || high != null) {
        const bandTop = y(high ?? top);
        svg.append(make('rect', { class: 'range', x: margin.left, y: bandTop, width: width - margin.left - margin.right, height: y(low ?? bottom) - bandTop }));
    }
    for (const value of [bottom + pad, top - pad]) {
        svg.append(make('line', { class: 'grid', x1: margin.left, x2: width - margin.right, y1: y(value), y2: y(value) }));
        svg.append(make('text', { class: 'axis', x: margin.left - 6, y: y(value) + 4, 'text-anchor': 'end' }, Number(value.toFixed(2)).toString()));
    }
    svg.append(make('text', { class: 'axis', x: margin.left, y: height - 6 }, day(points[0].date)));
    if (points.length > 1) {
        svg.append(make('text', { class: 'axis', x: width - margin.right, y: height - 6, 'text-anchor': 'end' }, day(points.at(-1).date)));
        const path = points.map((point, index) => `${index ? 'L' : 'M'}${positions[index].toFixed(1)} ${y(point.value).toFixed(1)}`).join(' ');
        svg.append(make('path', { class: 'series', d: path }));
    }
    points.forEach((point, index) => svg.append(make('circle', { class: 'point', cx: positions[index], cy: y(point.value), r: 4 })));

    const guide = make('line', { class: 'guide', y1: margin.top, y2: height - margin.bottom, visibility: 'hidden' });
    svg.append(guide);
    const tip = h('div', { class: 'tooltip', hidden: true });
    const wrapper = h('div', { class: 'trend' }, svg, tip);
    const hit = make('rect', { class: 'hit', x: margin.left, y: 0, width: plotWidth, height });
    hit.addEventListener('pointermove', (event) => {
        const box = svg.getBoundingClientRect();
        const pointer = ((event.clientX - box.left) / box.width) * width;
        let nearest = 0;
        positions.forEach((position, index) => {
            if (Math.abs(position - pointer) < Math.abs(positions[nearest] - pointer)) nearest = index;
        });
        const point = points[nearest];
        guide.setAttribute('x1', positions[nearest]);
        guide.setAttribute('x2', positions[nearest]);
        guide.setAttribute('visibility', 'visible');
        tip.hidden = false;
        tip.textContent = `${day(point.date)}: ${point.value} ${unit}`;
        tip.style.left = `${(positions[nearest] / width) * 100}%`;
        tip.style.top = `${(y(point.value) / height) * 100}%`;
    });
    hit.addEventListener('pointerleave', () => {
        guide.setAttribute('visibility', 'hidden');
        tip.hidden = true;
    });
    svg.append(hit);

    return h('div', {},
        h('h3', { style: 'margin-top:0' }, `${label} (${unit})`),
        wrapper,
        h('details', {}, h('summary', {}, 'Show values'),
            table(
                [
                    { label: 'Date', cell: (point) => when(point.date) },
                    { label: 'Value', number: true, cell: (point) => `${point.value} ${unit}` },
                    { label: 'Note', cell: (point) => describe?.(point) ?? '' },
                ],
                [...points].reverse(),
            )));
}

export const RANGE_WORDS = {
    below_lab_range: "Below the lab's range",
    above_lab_range: "Above the lab's range",
    within_lab_range: "Within the lab's range",
    no_range: 'No range on the report',
};
