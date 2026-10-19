async function refresh() {
  const result = document.getElementById('result');
  try {
    const response = await fetch('/api/info');
    if (!response.ok) throw new Error(`API returned HTTP ${response.status}`);
    result.textContent = JSON.stringify(await response.json(), null, 2);
  } catch (error) {
    result.textContent = error.message;
  }
}
document.getElementById('refresh').addEventListener('click', refresh);
refresh();
