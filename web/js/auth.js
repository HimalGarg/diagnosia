// Sign in and sign up against the Diagnosia API.
async function submitAuth(form, url, body) {
    const error = form.querySelector('.form-error');
    error.hidden = true;
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (response.ok) {
            window.location.href = 'index.html';
            return;
        }
        const data = await response.json().catch(() => ({}));
        error.textContent = typeof data.detail === 'string'
            ? data.detail
            : 'Please check your details. Passwords need at least 8 characters.';
    } catch (networkError) {
        error.textContent = "We couldn't reach the server. Please try again.";
    }
    error.hidden = false;
}

document.getElementById('signUpForm').addEventListener('submit', function(event) {
    event.preventDefault();
    const fields = this.elements;
    submitAuth(this, '/api/auth/register', {
        first_name: fields.fName.value,
        last_name: fields.lName.value,
        email: fields.email.value,
        password: fields.password.value,
        role: fields.role.value,
    });
});

document.getElementById('signInForm').addEventListener('submit', function(event) {
    event.preventDefault();
    const fields = this.elements;
    submitAuth(this, '/api/auth/login', {
        email: fields.email.value,
        password: fields.password.value,
    });
});
