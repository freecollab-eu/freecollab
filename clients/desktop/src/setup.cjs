'use strict';
const form = document.getElementById('connect'), error = document.getElementById('error');
form.addEventListener('submit', async event => {
  event.preventDefault(); const button = form.querySelector('button'); button.disabled = true; error.textContent = '';
  try { await window.freecollabSetup.connect(document.getElementById('installation').value.trim()); }
  catch (failure) { error.textContent = failure instanceof Error ? failure.message : 'This installation could not be opened.'; button.disabled = false; }
});
