import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { initApiOrigin } from './lib/origin';
import './styles/global.css';

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

// Resolve the backend origin once, before React mounts. In Tauri this
// invokes the `backend_port` command to learn which port the shell
// actually chose (it probes for a free one, so a stuck socket from a
// previous crash doesn't keep us stuck at :8000 forever).
//
// `.finally` (not `.then`) so a failure in initApiOrigin — which falls
// back to :8000 internally — still mounts the splash so the user sees
// the "backend isn't responding" path rather than a blank window.
initApiOrigin().finally(() => {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
