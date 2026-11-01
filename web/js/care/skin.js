// Skin Cancer Detection: a research screening aid. The labels here are deliberate.
import { api, h, show, toast } from './ui.js';

const PRIORITY_TEXT = {
    refer: 'Suggested priority: refer for biopsy soon',
    monitor: 'Suggested priority: monitor and review',
    uncertain: "Uncertain: this image doesn't look like what the model was trained on",
};

function resultView(analysis, photoUrl) {
    const uncertain = analysis.priority === 'uncertain';
    return h('div', { class: 'sheet', 'aria-live': 'polite' },
        h('h2', {}, 'Screening result'),
        h('div', { class: `priority ${analysis.priority}` }, PRIORITY_TEXT[analysis.priority]),
        analysis.warning && h('div', { class: 'banner' }, analysis.warning),
        !uncertain && analysis.conditions.map((condition) => {
            const percent = Math.round(condition.probability * 100);
            return h('div', { class: 'result-row' },
                h('span', {}, `${condition.name} (${condition.kind})`),
                h('div', { class: 'result-bar' }, h('span', { style: `width:${condition.probability * 100}%` })),
                h('span', {}, `${percent}%`));
        }),
        analysis.heatmap && h('div', { class: 'heatmap-pair' },
            [[photoUrl, 'Uploaded image'], [analysis.heatmap, 'Red areas raised the concern score']].map(([src, caption]) => h('figure', {},
                h('img', { src, alt: caption }),
                h('figcaption', { class: 'muted' }, caption)))),
        h('p', { class: 'muted' }, "This is not a diagnosis. The decision stays with the doctor."));
}

export function renderSkin(container, context) {
    if (!context.isStaff) {
        show(container, h('h1', {}, 'Skin Cancer Detection'), h('p', { class: 'empty' }, 'This research tool is for clinic staff.'));
        return;
    }
    const output = h('div', {});
    const photo = h('input', { type: 'file', name: 'image', accept: 'image/jpeg,image/png', required: true });
    const kind = h('select', { name: 'image_type' },
        h('option', { value: 'dermoscopic' }, 'Dermoscopic image'),
        h('option', { value: 'phone' }, 'Phone photo'));
    const submit = h('button', { type: 'submit' }, 'Analyze image');
    const upload = h('form', {
        class: 'stack',
        onsubmit: async (event) => {
            event.preventDefault();
            const body = new FormData();
            body.append('image', photo.files[0]);
            body.append('image_type', kind.value);
            submit.disabled = true;
            submit.textContent = 'Analyzing...';
            show(output);
            try {
                const analysis = await api('/api/skin/analyze', { method: 'POST', form: body });
                show(output, resultView(analysis, URL.createObjectURL(photo.files[0])));
            } catch (error) {
                toast(error.message, true);
                show(output, h('p', { class: 'error', role: 'alert' }, error.message));
            }
            submit.disabled = false;
            submit.textContent = 'Analyze image';
        },
    },
    h('label', {}, 'Lesion image (JPG or PNG, under 10 MB)', photo),
    h('label', {}, 'Image type', kind),
    submit);
    show(container,
        h('h1', {}, 'Skin Cancer Detection'),
        h('p', { class: 'lede' }, 'A screening aid that gives doctors an early second opinion.'),
        h('div', { class: 'banner' }, "This is not a diagnosis, and it isn't an approved medical device. A doctor's judgement and a biopsy are still needed."),
        h('div', { class: 'sheet' }, upload),
        output,
        h('p', { class: 'muted' }, 'Built on PanDerm (Yan et al., Nature Medicine, 2025), used under CC-BY-NC-ND 4.0 for non-commercial research. Trained on the ISIC 2019 dataset (BCN20000, HAM10000, MSK), CC-BY-NC. ',
            h('a', { href: 'skin_cancer_hiw.html' }, 'How it works')));
}
