// The public pages: when someone is already signed in, the header takes them back to work.
fetch('/api/me')
    .then(function(response) { return response.ok ? response.json() : null; })
    .then(function(user) {
        var cta = document.getElementById('account-cta');
        if (!user || !cta) return;
        cta.href = 'care.html';
        cta.firstElementChild.textContent = 'OPEN DIAGNOSIA';
    })
    .catch(function() {});
