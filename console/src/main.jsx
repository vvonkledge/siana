/**
 * The entry point, and the one place this app talks to the platform.
 */

import { createRoot } from 'react-dom/client';

import { App } from './app.jsx';
import './index.css';

createRoot(document.getElementById('app')).render(<App />);

// The service worker is what makes this installable and what makes it readable with
// no console to reach. Registration failing is not an error worth showing: the app
// works without it and only loses the offline copy, and a captain looking at a
// blocked task does not need to be told about a cache.
if (typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
  globalThis.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
