import { useState, useEffect } from 'react';
import { useAuth } from './contexts/AuthContext';
import LoginScreen from './components/LoginScreen';
import BoardsHome from './components/BoardsHome';
import BoardView from './components/BoardView';
import type { Board } from './api/client';

type ThemeMode = 'light' | 'dark';

export default function App() {
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [theme, setTheme] = useState<ThemeMode>(() => (localStorage.getItem('theme') as ThemeMode) || 'dark');
  const [currentBoard, setCurrentBoard] = useState<Board | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  if (authLoading) {
    return <div className="app-loading"><div className="spinner-large"></div><p>Cargando...</p></div>;
  }

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  return (
    <div className="app" data-theme={theme}>
      {currentBoard === null ? (
        <BoardsHome
          onSelectBoard={setCurrentBoard}
          theme={theme}
          onToggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
        />
      ) : (
        <BoardView
          board={currentBoard}
          onBack={() => setCurrentBoard(null)}
          theme={theme}
          onThemeChange={setTheme}
        />
      )}
    </div>
  );
}
