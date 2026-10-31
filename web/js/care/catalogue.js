// Every tool, in the order of a patient's visit. The rail and the home screen both
// read this list, so a tool has one name and one place everywhere in the product.
export const STAGES = [
    {
        title: 'Before the visit',
        tools: [
            { id: 'diagnofast', label: 'DiagnoFast', audience: 'staff', blurb: 'A spoken interview in the waiting room, ready as a one-page report.' },
        ],
    },
    {
        title: 'During the visit',
        tools: [
            { id: 'clinscribe', label: 'ClinScribe', audience: 'clinician', blurb: 'Turns a consultation into a draft note to check and sign.' },
            { id: 'lablens', label: 'LabLens', blurb: 'Reads lab reports into results a person confirms.' },
            { id: 'skin', label: 'Skin Cancer Detection', audience: 'staff', blurb: 'A research screening aid with a heatmap, for a second opinion.' },
        ],
    },
    {
        title: 'After the visit',
        tools: [
            { id: 'prescription', label: 'Prescription Analyzer', blurb: 'Reads a prescription photo and explains it in plain words.' },
            { id: 'referrals', label: 'ReferralLoop', blurb: 'Follows a referral from request to returned outcome.' },
            { id: 'discharge', label: 'DischargeCare', blurb: 'Turns a discharge plan into a checklist for the family.' },
        ],
    },
];

export const SWITCHED_TOOLS = STAGES.flatMap((stage) => stage.tools.map((tool) => tool.id));

export function canUse(tool, { me, patient, config }) {
    if (SWITCHED_TOOLS.includes(tool.id) && !config.tools.includes(tool.id)) return false;
    if (tool.audience === 'clinician') return patient?.relationship === 'clinician' || (!patient && me.role === 'doctor');
    if (tool.audience === 'staff') return me.role === 'doctor';
    return true;
}
