import { useEffect, useState } from 'react';
import ChatPage from './pages/ChatPage';
import ConfigPage from './pages/ConfigPage';

function getRoute(): string {
  return window.location.pathname;
}

export default function App() {
  const [route, setRoute] = useState(getRoute);

  useEffect(() => {
    const onPop = () => setRoute(getRoute());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  if (route.startsWith('/config')) return <ConfigPage />;
  return <ChatPage />;
}
