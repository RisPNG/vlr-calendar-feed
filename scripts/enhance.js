const status = document.querySelector('#copy-status');

for (const button of document.querySelectorAll('button[data-feed]')) {
  const address = button.closest('.subscription-address').querySelector('code');
  const url = new URL(button.dataset.feed, document.baseURI).href;
  address.textContent = url;
  if (!navigator.clipboard?.writeText) continue;
  button.hidden = false;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(url);
      button.textContent = 'Copied';
      status.textContent = 'Subscription link copied.';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(address);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Copy did not work. The address is selected so you can copy it with your keyboard.';
    }
  });
}
