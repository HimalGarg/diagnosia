// Fill in the signed-in user's details, or send them to the login page.
fetch('/api/me')
    .then(function(response) {
        if (!response.ok) throw new Error('not signed in');
        return response.json();
    })
    .then(function(user) {
        document.getElementById('user-id').textContent = user.id;
        document.getElementById('user-first-name').textContent = user.first_name;
        document.getElementById('user-last-name').textContent = user.last_name;
        document.getElementById('user-email').textContent = user.email;
        document.getElementById('user-role').textContent = user.role === 'doctor' ? 'Doctor' : 'Patient';
    })
    .catch(function() {
        window.location.href = 'login.html';
    });

document.getElementById('logout').addEventListener('click', function(event) {
    event.preventDefault();
    fetch('/api/auth/logout', { method: 'POST' }).finally(function() {
        window.location.href = 'index.html';
    });
});
