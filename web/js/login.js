const signUpButton = document.getElementById('signUpButton');
const signInButton = document.getElementById('signInButton');
const signInForm = document.getElementById('signIn');
const signUpForm = document.getElementById('signup');

if (signUpButton && signInButton && signInForm && signUpForm) {
  signUpButton.addEventListener('click', function () {
    signInForm.style.display = 'none';
    signUpForm.style.display = 'block';
  });

  signInButton.addEventListener('click', function () {
    signInForm.style.display = 'block';
    signUpForm.style.display = 'none';
  });
}

// Password visibility toggle
document.querySelectorAll('.password-toggle-btn').forEach((btn) => {
  btn.addEventListener('click', function () {
    const wrapper = this.closest('.input-wrapper');
    if (!wrapper) return;
    const input = wrapper.querySelector('input[name="password"]');
    if (!input) return;

    if (input.type === 'password') {
      input.type = 'text';
      this.innerHTML =
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="m2 2 20 20"/><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/></svg>';
    } else {
      input.type = 'password';
      this.innerHTML =
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
    }
  });
});