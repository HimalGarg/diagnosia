// Prescription Analyzer. Nothing is explained until the patient has confirmed it:
// a medicine left unticked is sent as unreadable, so it never reaches the explanation.
import { api, h, plural, readAloud, show, toast } from './ui.js';

const LEGIBILITY_NOTE = {
    unsure: 'We are not sure about this one. Please check it carefully.',
    unreadable: "We couldn't read this. Please ask your pharmacist.",
};
const LANGUAGES = [['hi-IN', 'हिन्दी'], ['en-IN', 'English']];

function isReadable(medicine) {
    return Boolean(medicine.name.value.trim())
        && medicine.name.legibility !== 'unreadable'
        && medicine.frequency.legibility !== 'unreadable';
}

function medicineCheckText(match) {
    if (!match) return null;
    const contains = match.contains ? ` It contains ${match.contains}.` : '';
    if (match.status === 'found') return `Found in our medicine list.${contains}`;
    if (match.status === 'close') return `Did you mean ${match.name}?${contains}`;
    if (match.status === 'not_found') return "This name isn't in our medicine list.";
    return null;
}

function medicineCard(medicine, index) {
    const worst = [medicine.name, medicine.frequency, medicine.dose, medicine.duration]
        .some((field) => field.legibility === 'unsure') ? 'unsure' : 'clear';
    const readable = isReadable(medicine);
    const state = readable ? worst : 'unreadable';
    const written = [medicine.dose.value, medicine.frequency.value, medicine.day_pattern.value, medicine.duration.value]
        .filter(Boolean).join(', ');
    return h('div', { class: `medicine-card ${state}` },
        h('strong', {}, medicine.name.value || 'A medicine we could not read'),
        written && h('p', {}, `As written: ${written}`),
        medicine.plain && h('p', {}, medicine.plain),
        medicineCheckText(medicine.match) && h('p', {}, medicineCheckText(medicine.match)),
        LEGIBILITY_NOTE[state] && h('p', {}, h('mark', {}, LEGIBILITY_NOTE[state])),
        readable && h('label', { class: 'tick' },
            h('input', { type: 'checkbox', 'data-index': index }),
            'This matches my prescription'));
}

function confirmedMedicines(reading, list) {
    const ticked = new Set([...list.querySelectorAll('input:checked')].map((tick) => Number(tick.dataset.index)));
    return reading.medicines.map((medicine, index) => {
        const { plain, match, ...fields } = medicine;
        // Ingredients are only trusted when the name was an exact match.
        const contains = match && match.status === 'found' ? match.contains || '' : '';
        if (ticked.has(index)) return { ...fields, contains };
        return { ...fields, name: { ...fields.name, legibility: 'unreadable' } };
    });
}

function listenButton(text, language) {
    const button = h('button', { type: 'button', class: 'quiet no-print' }, 'Listen');
    button.addEventListener('click', async () => {
        button.disabled = true;
        const spoken = await readAloud(text, language);
        if (!spoken) button.textContent = 'Audio unavailable';
        button.disabled = false;
    });
    return button;
}

function listCard(title, items, language) {
    return h('div', { class: 'sheet' },
        h('h2', {}, title),
        h('ul', {}, items.map((item) => h('li', {}, item))),
        language && listenButton(items.join('. ').slice(0, 2400), language));
}

function downloadIcs(icsText) {
    const blob = new Blob([icsText], { type: 'text/calendar;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'diagnosia_reminders.ics';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

const SLOT_LABELS = { morning: 'Morning', afternoon: 'Afternoon', night: 'Night' };
const SLOT_ICONS = { morning: '🌅', afternoon: '☀️', night: '🌙' };

function calendarRemindersView(reminders, language) {
    const slots = reminders.slots || {};
    const hasSlots = Object.keys(slots).length > 0;
    if (!hasSlots && !(reminders.as_needed || []).length) return null;

    const startInput = h('input', {
        type: 'date',
        value: reminders.start_date || new Date().toISOString().slice(0, 10),
        'aria-label': 'Start date for reminders',
    });
    const timeInputs = {};
    for (const slot of ['morning', 'afternoon', 'night']) {
        timeInputs[slot] = h('input', {
            type: 'time',
            value: reminders.times[slot],
            'aria-label': `${SLOT_LABELS[slot]} reminder time`,
        });
    }

    const refreshButton = h('button', {
        type: 'button',
        class: 'calendar-btn secondary',
        onclick: async () => {
            refreshButton.disabled = true;
            refreshButton.textContent = 'Updating…';
            try {
                const customTimes = {};
                for (const [slot, input] of Object.entries(timeInputs)) {
                    if (input.value) customTimes[slot] = input.value;
                }
                const updated = await api('/api/prescription/calendar', {
                    method: 'POST',
                    body: {
                        medicines: reminders._medicines,
                        language,
                        guidance: reminders._guidance,
                        start_date: startInput.value,
                        times: customTimes,
                    },
                });
                updated._medicines = reminders._medicines;
                updated._guidance = reminders._guidance;
                const parent = refreshButton.closest('.sheet');
                if (parent) show(parent, ...calendarRemindersView(updated, language).children);
            } catch (error) {
                toast(error.message, true);
            }
            refreshButton.disabled = false;
            refreshButton.textContent = '🔄 Update times';
        },
    }, '🔄 Update times');

    const controls = h('div', { class: 'calendar-controls no-print' },
        h('label', {}, 'Start date', startInput),
        ...['morning', 'afternoon', 'night'].map((slot) =>
            h('label', {}, `${SLOT_ICONS[slot]} ${SLOT_LABELS[slot]}`, timeInputs[slot]),
        ),
        refreshButton,
    );

    const helperContainer = h('div', { class: 'calendar-helper', style: 'display: none;' });

    const addAllButton = reminders.ics_calendar
        ? h('button', {
            type: 'button',
            class: 'calendar-btn',
            style: 'font-size: 1rem; padding: 10px 18px;',
            onclick: () => {
                downloadIcs(reminders.ics_calendar);
                show(helperContainer, h('div', { class: 'calendar-import-note' },
                    h('strong', {}, '📅 Reminders file downloaded (diagnosia_reminders.ics)!'),
                    h('p', {},
                        'To import all slots into Google Calendar in one go: ',
                        h('a', {
                            href: 'https://calendar.google.com/calendar/r/settings/export',
                            target: '_blank',
                            rel: 'noopener noreferrer',
                            class: 'calendar-link',
                        }, 'Open Google Calendar Import ↗'),
                        ' and select the downloaded .ics file, or open it directly in your device\'s calendar app.',
                    ),
                ));
                helperContainer.style.display = 'block';
            },
        }, '📅 Add All Reminders to Calendar')
        : null;

    const slotCards = ['morning', 'afternoon', 'night']
        .filter((slot) => slots[slot])
        .map((slot) => {
            const data = slots[slot];
            return h('div', { class: 'calendar-card' },
                h('div', { class: 'slot-header' },
                    h('strong', {}, `${SLOT_ICONS[slot]} ${data.title}`),
                    h('span', { class: 'muted' }, `${data.time} · ${plural(data.duration_days, 'day')}`),
                ),
                h('ul', {},
                    data.medicines.map((m) => h('li', {},
                        `${m.name} (${m.amount})`,
                        m.dose ? ` ${m.dose}` : '',
                        m.purpose ? h('em', {}, ` — ${m.purpose}`) : '',
                    )),
                ),
            );
        });

    const asNeeded = reminders.as_needed || [];
    const asNeededSection = asNeeded.length
        ? h('div', { class: 'calendar-card' },
            h('strong', {}, '💊 As-needed medicines (not on timed reminders)'),
            h('ul', {},
                asNeeded.map((m) => h('li', {},
                    h('strong', {}, m.name),
                    m.dose ? ` ${m.dose}` : '',
                    ' — ',
                    m.instructions,
                    m.purpose ? h('em', {}, ` (${m.purpose})`) : '',
                )),
            ),
        )
        : null;

    return h('div', { class: 'sheet' },
        h('h2', {}, '📅 Medicine Reminders'),
        h('p', { class: 'lede' }, 'Set daily alarms so you never miss a dose. Pick your preferred times and add all reminders to your calendar in one click.'),
        controls,
        addAllButton && h('div', { class: 'calendar-actions', style: 'margin: 14px 0 10px' }, addAllButton),
        helperContainer,
        ...slotCards,
        asNeededSection,
    );
}

function resultsView(data, language, reading, confirmedMeds) {
    const guidance = data.guidance;
    const hasSchedule = Object.values(data.schedule).some((entries) => entries.length);
    const reminders = data.reminders || {};
    // Attach originals so the refresh button can re-request with updated times.
    reminders._medicines = confirmedMeds;
    reminders._guidance = guidance;
    const remindersSection = calendarRemindersView(reminders, language);
    return [
        guidance && h('div', { class: 'sheet' },
            h('h2', {}, 'Your medicines'),
            guidance.medicines.map((medicine) => h('div', { class: 'medicine-card' },
                h('strong', {}, medicine.name),
                medicine.purpose && h('p', {}, medicine.purpose),
                h('p', {}, medicine.how_to_take),
                listenButton(`${medicine.name}. ${medicine.purpose} ${medicine.how_to_take}`, language)))),
        hasSchedule && h('div', { class: 'sheet' },
            h('h2', {}, 'Daily schedule'),
            h('div', { class: 'schedule' }, [['morning', 'Morning'], ['afternoon', 'Afternoon'], ['night', 'Night']].map(([slot, title]) => h('div', {},
                h('strong', {}, title),
                data.schedule[slot].map((entry) => h('p', {}, `${entry.name}: ${entry.amount}`)))))),
        remindersSection,
        reading.follow_up_date.value && listCard('Next appointment', [reading.follow_up_date.value]),
        data.ask_pharmacist.length > 0 && listCard('Please ask your pharmacist or doctor about', data.ask_pharmacist),
    ];
}

export function renderPrescription(container) {
    const stepOne = h('div', {});
    const stepTwo = h('div', {});
    const stepThree = h('div', {});
    const photos = h('input', { type: 'file', name: 'images', accept: 'image/jpeg,image/png', multiple: true, required: true });
    const consent = h('input', { type: 'checkbox', required: true });
    const readButton = h('button', { type: 'submit' }, 'Read my prescription');

    const showConfirm = (reading) => {
        const list = h('div', {}, reading.medicines.map(medicineCard));
        const language = h('select', {}, LANGUAGES.map(([value, label]) => h('option', { value }, label)));
        const explain = h('button', { type: 'button' }, 'Explain my prescription');
        explain.addEventListener('click', async () => {
            explain.disabled = true;
            explain.textContent = 'Explaining...';
            try {
                const notes = reading.doctor_notes.legibility === 'unreadable' ? '' : reading.doctor_notes.value;
                const confirmed = confirmedMedicines(reading, list);
                const data = await api('/api/prescription/explain', {
                    method: 'POST',
                    body: { medicines: confirmed, language: language.value, doctor_notes: notes.slice(0, 2000) },
                });
                show(stepThree, resultsView(data, language.value, reading, confirmed));
                stepThree.scrollIntoView({ behavior: 'smooth' });
            } catch (error) {
                toast(error.message, true);
            }
            explain.disabled = false;
            explain.textContent = 'Explain my prescription';
        });
        show(stepTwo, h('div', { class: 'sheet' },
            h('h2', {}, 'Check what we read'),
            h('p', { class: 'lede' }, "Tick each medicine that matches your prescription. Anything you don't tick goes to your pharmacist's list and isn't explained."),
            list,
            h('label', {}, 'Explain it in', language),
            h('div', { class: 'row' }, explain)));
        stepTwo.scrollIntoView({ behavior: 'smooth' });
    };

    const upload = h('form', {
        class: 'stack',
        onsubmit: async (event) => {
            event.preventDefault();
            show(stepOne);
            show(stepTwo);
            show(stepThree);
            readButton.disabled = true;
            readButton.textContent = 'Reading...';
            const body = new FormData();
            [...photos.files].forEach((photo) => body.append('images', photo));
            body.append('consent', consent.checked);
            try {
                const reading = await api('/api/prescription/read', { method: 'POST', form: body });
                if (!reading.is_prescription) {
                    show(stepOne, h('p', { class: 'error', role: 'alert' }, "This doesn't look like a prescription. Please try another photo."));
                } else if (!reading.medicines.length) {
                    show(stepOne, h('p', { class: 'error', role: 'alert' }, "We couldn't find any medicines in this photo. Please take a clearer one."));
                } else {
                    showConfirm(reading);
                }
            } catch (error) {
                show(stepOne, h('p', { class: 'error', role: 'alert' }, error.message));
            }
            readButton.disabled = false;
            readButton.textContent = 'Read my prescription';
        },
    },
    h('label', {}, 'Photos of your prescription (JPG or PNG). Add the front and back if both have writing, up to 4 photos.', photos),
    h('label', { class: 'tick' }, consent, "I agree to my photo being sent to outside AI services to read it. Diagnosia doesn't keep the photo."),
    readButton);

    show(container,
        h('h1', {}, 'Prescription Analyzer'),
        h('p', { class: 'lede' }, 'Reads your prescription and explains it in plain words. It never changes a dose.'),
        h('div', { class: 'sheet no-print' }, upload),
        stepOne, stepTwo, stepThree,
        h('p', { class: 'muted no-print' }, h('a', { href: 'prescription_analyzer_hiw.html' }, 'How it works')));
}
