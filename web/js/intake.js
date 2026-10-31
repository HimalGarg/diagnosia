// The patient's side of DiagnoFast. Voice first; typing is the fallback.
const TEXT = {
    'en-IN': {
        consent: 'I agree. My answers and voice recordings are sent to AI services to prepare a report for my doctor. My recordings are kept, encrypted, for my doctor to check.',
        abha: 'I also agree to share my past health records from ABHA with my doctor.',
        start: 'Start',
        record: 'Tap and speak',
        stop: 'Tap when you finish',
        thinking: 'Please wait...',
        type: 'Type instead',
        send: 'Send',
        noConsent: 'Please tick the box to continue.',
    },
    'hi-IN': {
        consent: 'मैं सहमत हूँ। मेरे जवाब और आवाज़ की रिकॉर्डिंग, डॉक्टर के लिए रिपोर्ट बनाने के लिए एआई सेवाओं को भेजी जाएँगी। मेरी रिकॉर्डिंग मेरे डॉक्टर की जाँच के लिए एन्क्रिप्ट करके रखी जाएँगी।',
        abha: 'मैं ABHA से अपने पुराने स्वास्थ्य रिकॉर्ड भी डॉक्टर के साथ साझा करने के लिए सहमत हूँ।',
        start: 'शुरू करें',
        record: 'दबाएँ और बोलें',
        stop: 'बोलने के बाद दबाएँ',
        thinking: 'कृपया रुकिए...',
        type: 'लिखकर बताएँ',
        send: 'भेजें',
        noConsent: 'आगे बढ़ने के लिए बॉक्स पर टिक करें।',
    },
};
// Answers are short, and this keeps each recording under the 5 MB upload limit.
const MAX_RECORDING_MS = 28000;

const token = new URLSearchParams(window.location.search).get('token');
const welcome = document.getElementById('intake-welcome');
const interview = document.getElementById('intake-interview');
const finish = document.getElementById('intake-finish');
const inactive = document.getElementById('intake-inactive');
const languageSelect = document.getElementById('intake-language');
const consent = document.getElementById('intake-consent');
const abhaRow = document.getElementById('intake-abha-row');
const abhaConsent = document.getElementById('intake-abha');
const startButton = document.getElementById('intake-start');
const startError = document.getElementById('intake-start-error');
const question = document.getElementById('intake-question');
const recordButton = document.getElementById('intake-record');
const typeButton = document.getElementById('intake-type');
const typedForm = document.getElementById('intake-typed');
const typedAnswer = document.getElementById('intake-answer');
const answerError = document.getElementById('intake-answer-error');

let recorder = null;
let stopTimer = null;

function words() {
    return TEXT[languageSelect.value];
}

function applyLanguage() {
    document.getElementById('intake-consent-text').textContent = words().consent;
    document.getElementById('intake-abha-text').textContent = words().abha;
    startButton.textContent = words().start;
    recordButton.textContent = words().record;
    typeButton.textContent = words().type;
    document.getElementById('intake-send').textContent = words().send;
}

// Hindi has no server voice, and neither does anything when speech is down. The browser reads it instead.
function browserVoice(text) {
    if (!text || !window.speechSynthesis) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = languageSelect.value;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
}

function play(audio, text) {
    if (audio) new Audio(audio).play().catch(() => browserVoice(text));
    else browserVoice(text);
}

function setBusy(busy) {
    recordButton.disabled = busy;
    recordButton.textContent = busy ? words().thinking : words().record;
}

function showNext(reply) {
    if (reply.finished) {
        interview.hidden = true;
        finish.hidden = false;
        const message = document.getElementById('intake-finish-message');
        message.textContent = reply.message;
        message.className = reply.urgent ? 'urgent-banner' : 'intake-question';
    } else {
        question.textContent = reply.question;
        typedAnswer.value = '';
    }
    play(reply.audio, reply.finished ? reply.message : reply.question);
}

async function sendAnswer(data) {
    answerError.hidden = true;
    setBusy(true);
    try {
        const response = await apiFetch(`/api/diagnofast/patient/${token}/answer`, { method: 'POST', body: data });
        showNext(await response.json());
    } catch (failure) {
        showError(answerError, failure.message);
        // Typing keeps the interview going if speech is down.
        typedForm.hidden = false;
    }
    setBusy(false);
}

async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener('dataavailable', (event) => chunks.push(event.data));
    recorder.addEventListener('stop', () => {
        clearTimeout(stopTimer);
        stream.getTracks().forEach((track) => track.stop());
        recordButton.classList.remove('recording');
        recorder = null;
        const type = chunks[0] ? chunks[0].type : 'audio/webm';
        const data = new FormData();
        data.append('audio', new Blob(chunks, { type }), 'answer');
        sendAnswer(data);
    });
    recorder.start();
    recordButton.classList.add('recording');
    recordButton.textContent = words().stop;
    stopTimer = setTimeout(() => recorder && recorder.stop(), MAX_RECORDING_MS);
}

recordButton.addEventListener('click', async () => {
    if (recorder) {
        recorder.stop();
        return;
    }
    try {
        await startRecording();
    } catch (failure) {
        typedForm.hidden = false;
    }
});

typeButton.addEventListener('click', () => {
    typedForm.hidden = false;
    typedAnswer.focus();
});

typedForm.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!typedAnswer.value.trim()) return;
    const data = new FormData();
    data.append('text', typedAnswer.value);
    sendAnswer(data);
});

startButton.addEventListener('click', async () => {
    startError.hidden = true;
    if (!consent.checked) {
        showError(startError, words().noConsent);
        return;
    }
    try {
        const reply = await postJson(`/api/diagnofast/patient/${token}/start`, {
            language: languageSelect.value,
            consent: true,
            abha_consent: abhaConsent.checked,
        });
        welcome.hidden = true;
        interview.hidden = false;
        applyLanguage();
        showNext({ finished: false, ...reply });
    } catch (failure) {
        showError(startError, failure.message);
    }
});

languageSelect.addEventListener('change', applyLanguage);
applyLanguage();

(token ? fetch(`/api/diagnofast/patient/${encodeURIComponent(token)}`) : Promise.reject())
    .then((response) => (response.ok ? response.json() : Promise.reject()))
    .then((patient) => {
        document.getElementById('intake-name').textContent = patient.patient_name;
        abhaRow.hidden = !patient.has_abha_id;
        welcome.hidden = false;
    })
    .catch(() => {
        inactive.hidden = false;
    });
