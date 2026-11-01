// Patient text is always a text node or an input value. A draft is never sent as an answer.
const TEXT = {
    'en-IN': {
        start: 'Start my interview', record: 'Tap and speak', stop: 'Stop recording', thinking: 'Please wait…',
        type: 'Edit / type', send: 'Confirm answer',
        assistant: 'YOUR INTAKE ASSISTANT', transcript: 'YOUR WORDS', replay: '↻ Hear the question',
        chipHint: 'Tap an option or speak in your own words.', review: 'Check these words. Edit anything that is wrong, then confirm.',
        safety: 'If you need help now, please tell reception.', answer: 'Your answer', ready: 'Ready when you are',
        listening: '● Recording · listening', reviewStatus: 'Ready to review', confidence: 'Recognition confidence unavailable',
        browserConfidence: 'Browser recognition estimate', edited: 'Edited / typed answer · review before confirming',
        fallback: 'Live words unavailable. We’ll transcribe the recording when you stop.',
        microphone: 'Microphone unavailable. You can type your answer below.',
        partial: 'We could not transcribe the full recording. Check that your answer is complete, or type it below.',
        name: 'Please enter your name and check your age.', placeholder: 'Your words will appear here. You can also type.',
    },
    'hi-IN': {
        start: 'बातचीत शुरू करें', record: 'दबाएँ और बोलें', stop: 'रिकॉर्डिंग रोकें', thinking: 'कृपया रुकिए…',
        type: 'बदलें / लिखें', send: 'जवाब की पुष्टि करें',
        assistant: 'आपका सहायक', transcript: 'आपके शब्द', replay: '↻ प्रश्न फिर सुनें',
        chipHint: 'विकल्प चुनें या अपने शब्दों में बोलें।', review: 'अपने शब्द जाँचें। गलती हो तो सुधारें, फिर पुष्टि करें।',
        safety: 'अभी मदद चाहिए तो रिसेप्शन को बताएँ।', answer: 'आपका जवाब', ready: 'जब आप तैयार हों',
        listening: '● रिकॉर्डिंग · सुन रहे हैं', reviewStatus: 'जवाब जाँचें', confidence: 'आवाज़ पहचान का विश्वास स्तर उपलब्ध नहीं',
        browserConfidence: 'ब्राउज़र की आवाज़ पहचान का अनुमान', edited: 'लिखा / बदला हुआ जवाब · पुष्टि से पहले जाँचें',
        fallback: 'शब्द अभी नहीं दिख रहे। रोकने पर रिकॉर्डिंग लिखी जाएगी।',
        microphone: 'माइक्रोफ़ोन उपलब्ध नहीं है। नीचे अपना जवाब लिख सकते हैं।',
        partial: 'पूरी रिकॉर्डिंग लिखी नहीं जा सकी। जवाब पूरा है या नहीं जाँचें, या नीचे लिखें।',
        name: 'कृपया नाम भरें और उम्र जाँचें।', placeholder: 'आपके शब्द यहाँ दिखेंगे। आप लिख भी सकते हैं।',
    },
};
const DEPARTMENTS = {
    general_physician: ['General Medicine', 'सामान्य चिकित्सा', '✚'],
    dermatologist: ['Dermatology', 'त्वचा विभाग', '◈'],
    cardiology: ['Cardiology', 'हृदय विभाग', '♡'],
    orthopedics: ['Orthopedics', 'हड्डी एवं जोड़', '◇'],
    ent: ['ENT', 'कान, नाक और गला', '◎'],
    pediatrics: ['Pediatrics', 'बाल चिकित्सा', '☆'],
};
const MAX_RECORDING_MS = 28000;
const $ = (id) => document.getElementById(`intake-${id}`);
let token = new URLSearchParams(location.search).get('token');
const kiosk = !token;
let formId = null;
let busy = false;
let acquiring = false;
let stopping = false;
let disposed = false;
let recorder = null;
let stream = null;
let recognition = null;
let recognitionComplete = null;
let recognitionIncomplete = false;
let pendingBlob = null;
let stopTimer = null;
let audioContext = null;
let meterFrame = null;
let activeAudio = null;
let currentReply = null;
let playbackGeneration = 0;
const words = () => TEXT[$('language').value];

function applyLanguage() {
    document.documentElement.lang = $('language').value.startsWith('hi') ? 'hi' : 'en';
    const labels = { start: 'start', type: 'type', send: 'send',
        'assistant-label': 'assistant', 'transcript-label': 'transcript', replay: 'replay', 'chip-hint': 'chipHint',
        'review-hint': 'review', 'safety-note': 'safety', 'answer-label': 'answer' };
    for (const [id, key] of Object.entries(labels)) $(id).textContent = words()[key];
    $('language-tag').textContent = $('language').value === 'hi-IN' ? 'हिन्दी' : 'English (IN)';
    $('answer').placeholder = words().placeholder;
    $('confidence').textContent = words().confidence;
    $('record-status').textContent = words().ready;
    syncControls();
}

function syncControls() {
    const recording = !!recorder;
    const locked = busy || acquiring || stopping || recording;
    $('record').disabled = busy || acquiring || stopping;
    $('record').textContent = recording ? words().stop : (busy || acquiring || stopping) ? words().thinking : words().record;
    $('record').classList.toggle('recording', recording);
    $('answer').readOnly = locked;
    $('send').disabled = locked || !$('answer').value.trim();
    $('type').disabled = locked;
    $('replay').disabled = locked;
    document.querySelectorAll('#intake-chips button').forEach((button) => { button.disabled = locked; });
}

function stopPlayback() {
    playbackGeneration += 1;
    if (activeAudio) { activeAudio.pause(); activeAudio = null; }
    window.speechSynthesis?.cancel();
    document.querySelector('.assistant-orb').classList.remove('speaking');
}

function browserVoice(text, generation) {
    if (!text || !window.speechSynthesis || generation !== playbackGeneration) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = $('language').value;
    const voices = speechSynthesis.getVoices().filter((voice) => voice.lang.toLowerCase().startsWith(utterance.lang.slice(0, 2)));
    utterance.voice = voices.find((voice) => /neerja|swara|samantha|siri|female/i.test(voice.name))
        || voices.find((voice) => voice.lang.toLowerCase() === utterance.lang.toLowerCase()) || voices[0] || null;
    utterance.rate = 0.9;
    utterance.pitch = 1.0;
    const orb = document.querySelector('.assistant-orb');
    utterance.onstart = () => { if (generation === playbackGeneration) orb.classList.add('speaking'); };
    utterance.onend = utterance.onerror = () => { if (generation === playbackGeneration) orb.classList.remove('speaking'); };
    speechSynthesis.speak(utterance);
}

function play(reply) {
    stopPlayback();
    const generation = playbackGeneration;
    const text = reply.finished ? reply.message : reply.question;
    if (!reply.audio) { browserVoice(text, generation); return; }
    activeAudio = new Audio(reply.audio);
    activeAudio.onplaying = () => document.querySelector('.assistant-orb').classList.add('speaking');
    activeAudio.onended = () => document.querySelector('.assistant-orb').classList.remove('speaking');
    activeAudio.play().catch(() => browserVoice(text, generation));
}

function renderChips(chips = []) {
    $('chips').replaceChildren();
    chips.forEach((chip) => {
        const button = element('button', '', chip.label);
        button.type = 'button';
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
            // A chip is a reviewable draft, never an automatic submission.
            pendingBlob = null;
            $('answer').value = chip.value;
            document.querySelectorAll('#intake-chips button').forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
            $('confidence').textContent = words().edited;
            $('record-status').textContent = words().reviewStatus;
            syncControls();
        });
        $('chips').append(button);
    });
}

function showNext(reply) {
    currentReply = reply;
    pendingBlob = null;
    $('answer').value = '';
    $('confidence').textContent = words().confidence;
    $('record-status').textContent = words().ready;
    renderChips(reply.suggested_chips);
    if (reply.finished) {
        $('interview').hidden = true;
        $('finish').hidden = false;
        $('finish-message').textContent = reply.message;
        $('finish-message').className = reply.urgent ? 'urgent-banner' : 'intake-question';
        // Remove the spent bearer token and identifying details from the shared screen.
        history.replaceState(null, '', 'intake.html');
        $('patient-name').value = '';
        $('name').textContent = '';
        $('age').value = '';
        $('sex').value = '';
    } else {
        $('question').textContent = reply.question;
        $('question').focus();
    }
    play(reply);
    syncControls();
}

async function sendAnswer() {
    if (busy || recorder || acquiring || stopping || !$('answer').value.trim()) return;
    const data = new FormData();
    data.append('text', $('answer').value.trim());
    if (pendingBlob) data.append('audio', pendingBlob, 'answer');
    $('answer-error').hidden = true;
    busy = true;
    syncControls();
    stopPlayback();
    try {
        const response = await apiFetch(`/api/diagnofast/patient/${encodeURIComponent(token)}/answer`, { method: 'POST', body: data });
        if (!disposed) showNext(await response.json());
    } catch (failure) {
        if (!disposed) showError($('answer-error'), failure.message);
    } finally {
        busy = false;
        syncControls();
    }
}

function startMeter(mic) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    try {
        audioContext = new AudioContextClass();
        audioContext.resume().catch(() => {});
        const analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        audioContext.createMediaStreamSource(mic).connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const canvas = $('meter');
        const context = canvas.getContext('2d');
        const draw = () => {
            analyser.getByteFrequencyData(data);
            context.clearRect(0, 0, canvas.width, canvas.height);
            context.fillStyle = '#b1edda';
            for (let i = 0; i < 40; i += 1) {
                const height = Math.max(2, data[i * 2] / 255 * 36);
                context.fillRect(i * 12 + 2, (40 - height) / 2, 8, height);
            }
            meterFrame = requestAnimationFrame(draw);
        };
        draw();
    } catch (_) { /* Recording still works without a visualizer. */ }
}

function releaseMic() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    cancelAnimationFrame(meterFrame);
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    $('meter').getContext('2d')?.clearRect(0, 0, 480, 40);
}

function startRecognition() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognitionIncomplete = !Recognition;
    if (!Recognition) { $('record-status').textContent = words().fallback; return; }
    const session = new Recognition();
    recognition = session;
    session.lang = $('language').value;
    session.interimResults = true;
    session.continuous = true;
    session.onresult = (event) => {
        if (disposed || recognition !== session) return;
        const fragments = [];
        const scores = [];
        for (const result of event.results) {
            fragments.push(result[0].transcript);
            if (result.isFinal && result[0].confidence > 0) scores.push(result[0].confidence);
        }
        $('answer').value = fragments.join(' ').slice(0, 2000);
        $('confidence').textContent = scores.length
            ? `${words().browserConfidence}: ${Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 100)}%`
            : words().confidence;
    };
    session.onerror = () => {
        recognitionIncomplete = true;
        if (!disposed) $('record-status').textContent = words().fallback;
    };
    recognitionComplete = new Promise((resolve) => {
        session.onend = () => {
            if (recorder && !stopping) recognitionIncomplete = true;
            resolve();
        };
    });
    try { session.start(); } catch (_) { recognitionIncomplete = true; recognition = null; recognitionComplete = null; }
}

async function finishRecording(chunks) {
    clearTimeout(stopTimer);
    stopping = true;
    recorder = null;
    releaseMic();
    syncControls();
    const session = recognition;
    if (session) {
        try { session.stop(); } catch (_) { /* It may have ended on its own. */ }
        let timeout;
        await Promise.race([recognitionComplete, new Promise((resolve) => {
            timeout = setTimeout(() => { recognitionIncomplete = true; resolve(); }, 1000);
        })]);
        clearTimeout(timeout);
        recognition = null;
        try { session.abort(); } catch (_) { /* Already ended. */ }
    }
    if (disposed) { stopping = false; return; }
    pendingBlob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' });
    if (!pendingBlob.size) pendingBlob = null;
    if (pendingBlob && (recognitionIncomplete || !$('answer').value.trim())) {
        $('record-status').textContent = words().thinking;
        const data = new FormData();
        data.append('audio', pendingBlob, 'answer');
        try {
            const response = await apiFetch(`/api/diagnofast/patient/${encodeURIComponent(token)}/transcribe`, { method: 'POST', body: data });
            const reply = await response.json();
            if (!disposed) { $('answer').value = reply.text; $('confidence').textContent = words().confidence; }
        } catch (failure) {
            if (!disposed) showError($('answer-error'), `${words().partial} ${failure.message}`);
        }
    }
    stopping = false;
    if (!disposed) {
        $('record-status').textContent = words().reviewStatus;
        syncControls();
    }
}

async function startRecording() {
    if (busy || acquiring || stopping || disposed) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        showError($('answer-error'), words().microphone);
        return;
    }
    acquiring = true;
    syncControls();
    stopPlayback();
    $('answer-error').hidden = true;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (disposed) { releaseMic(); return; }
        const chunks = [];
        const session = new MediaRecorder(stream);
        recorder = session;
        session.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
        session.onstop = () => finishRecording(chunks);
        session.onerror = () => {
            showError($('answer-error'), words().microphone);
            if (session.state !== 'inactive') session.stop();
            else { recorder = null; releaseMic(); syncControls(); }
        };
        session.start();
        pendingBlob = null;
        $('answer').value = '';
        $('confidence').textContent = words().confidence;
        $('record-status').textContent = words().listening;
        startRecognition();
        startMeter(stream);
        stopTimer = setTimeout(stopRecording, MAX_RECORDING_MS);
    } catch (_) {
        recorder = null;
        releaseMic();
        if (!disposed) showError($('answer-error'), words().microphone);
    } finally {
        acquiring = false;
        syncControls();
    }
}

function stopRecording() {
    if (!recorder || recorder.state === 'inactive') return;
    stopping = true;
    syncControls();
    recorder.stop();
}

$('record').addEventListener('click', () => recorder ? stopRecording() : startRecording());
$('type').addEventListener('click', () => $('answer').focus());
$('typed').addEventListener('submit', (event) => { event.preventDefault(); sendAnswer(); });
$('answer').addEventListener('input', () => {
    $('confidence').textContent = words().edited;
    document.querySelectorAll('#intake-chips button').forEach((button) => button.setAttribute('aria-pressed', 'false'));
    syncControls();
});
$('replay').addEventListener('click', () => currentReply && play(currentReply));
$('language').addEventListener('change', applyLanguage);
$('change-specialty').addEventListener('click', () => { $('welcome').hidden = true; $('specialties').hidden = false; });

$('start').addEventListener('click', async () => {
    if ($('start').disabled) return;
    $('start-error').hidden = true;
    if (kiosk && (!$('patient-name').value.trim() || !$('age').checkValidity())) {
        showError($('start-error'), words().name); return;
    }
    $('start').disabled = true;
    $('change-specialty').disabled = true;
    $('language').disabled = true;
    try {
        if (!token) {
            const made = await postJson('/api/diagnofast/kiosk/intakes', {
                form_id: formId, patient_name: $('patient-name').value.trim(),
                patient_age: $('age').value === '' ? null : Number($('age').value),
                patient_sex: $('sex').value || null, consent: true,
            });
            token = new URL(made.link, location.href).searchParams.get('token');
            // Keep the new link on a start failure so a retry cannot create a second visit.
            history.replaceState(null, '', `intake.html?token=${encodeURIComponent(token)}`);
            $('change-specialty').hidden = true;
            $('demographics').querySelectorAll('input, select').forEach((input) => { input.disabled = true; });
        }
        const reply = await postJson(`/api/diagnofast/patient/${encodeURIComponent(token)}/start`, {
            language: $('language').value, consent: true, abha_consent: true,
        });
        $('welcome').hidden = true;
        $('interview').hidden = false;
        showNext({ finished: false, ...reply });
    } catch (failure) {
        showError($('start-error'), failure.message);
    } finally {
        $('start').disabled = false;
        $('change-specialty').disabled = false;
        $('language').disabled = false;
    }
});

window.addEventListener('pagehide', () => {
    disposed = true;
    clearTimeout(stopTimer);
    stopPlayback();
    try { recognition?.abort(); } catch (_) { /* Already ended. */ }
    if (recorder?.state === 'recording') recorder.stop();
    releaseMic();
    pendingBlob = null;
    $('answer').value = '';
});
// A shared kiosk must not restore a previous patient's draft from the back/forward cache.
window.addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });

async function initialize() {
    applyLanguage();
    try {
        if (token) {
            const response = await apiFetch(`/api/diagnofast/patient/${encodeURIComponent(token)}`);
            const patient = await response.json();
            $('name').textContent = `, ${patient.patient_name}`;
            $('welcome').hidden = false;
        } else {
            const response = await apiFetch('/api/diagnofast/kiosk');
            const { forms } = await response.json();
            Object.entries(DEPARTMENTS).forEach(([id, [title, hindi, symbol]]) => {
                if (!forms.some((form) => form.id === id)) return;
                const button = element('button', 'specialty-card');
                button.type = 'button';
                const icon = element('span', 'specialty-symbol', symbol);
                icon.setAttribute('aria-hidden', 'true');
                button.append(icon, element('strong', '', title), element('small', '', hindi));
                button.addEventListener('click', () => {
                    formId = id;
                    $('department').textContent = `${title} · ${hindi}`;
                    $('specialties').hidden = true;
                    $('welcome').hidden = false;
                    $('patient-name').focus();
                });
                $('specialty-grid').append(button);
            });
            $('demographics').hidden = false;
            $('change-specialty').hidden = false;
            $('specialties').hidden = false;
        }
    } catch (failure) {
        $('inactive-message').textContent = failure.message;
        $('inactive').hidden = false;
    }
}
initialize();
