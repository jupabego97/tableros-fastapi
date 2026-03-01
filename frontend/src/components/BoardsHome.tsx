import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Board } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import CreateBoardModal from './CreateBoardModal';

interface Props {
  onSelectBoard: (board: Board) => void;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

export default function BoardsHome({ onSelectBoard, theme, onToggleTheme }: Props) {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);

  const { data: boards = [], isLoading } = useQuery<Board[]>({
    queryKey: ['boards'],
    queryFn: api.getBoards,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteBoard(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['boards'] }),
  });

  return (
    <>
      <header className="app-header">
        <div className="header-left">
          <h1 className="app-title">
            <i className="fas fa-layer-group"></i> Tableros de Garantías
          </h1>
        </div>
        <div className="header-actions">
          <button className="header-btn active" onClick={() => setShowCreate(true)} aria-label="Nuevo tablero">
            <i className="fas fa-plus"></i> <span className="btn-text">Nuevo tablero</span>
          </button>
          <button className="header-btn" onClick={onToggleTheme} title="Cambiar tema" aria-label="Cambiar tema">
            <i className={theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon'}></i>
          </button>
          <div className="user-menu">
            <div className="user-avatar" style={{ background: user?.avatar_color || '#00ACC1' }}>
              {user?.full_name?.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()}
            </div>
            <span className="user-name">{user?.full_name}</span>
            <button className="btn-logout" onClick={logout} title="Cerrar sesion" aria-label="Cerrar sesion">
              <i className="fas fa-sign-out-alt"></i>
            </button>
          </div>
        </div>
      </header>

      <div className="boards-home">
        {isLoading ? (
          <div className="app-loading"><div className="spinner-large"></div></div>
        ) : boards.length === 0 ? (
          <div className="boards-empty">
            <i className="fas fa-layer-group" style={{ fontSize: '3rem', color: 'var(--text-muted)', marginBottom: '1rem' }}></i>
            <h2>No hay tableros aún</h2>
            <p style={{ color: 'var(--text-muted)', marginBottom: '1rem' }}>
              Crea tu primer tablero para comenzar a gestionar garantías por proveedor.
            </p>
            <button className="btn-save" onClick={() => setShowCreate(true)}>
              <i className="fas fa-plus"></i> Crear tablero
            </button>
          </div>
        ) : (
          <div className="boards-grid">
            {boards.map(board => (
              <div
                key={board.id}
                className="board-card"
                onClick={() => onSelectBoard(board)}
                role="button"
                tabIndex={0}
                aria-label={`Abrir tablero ${board.name}`}
                onKeyDown={e => { if (e.key === 'Enter') onSelectBoard(board); }}
              >
                <div className="board-card-accent" style={{ background: board.color }}></div>
                <div className="board-card-body">
                  <div className="board-card-icon">{board.icon || '📋'}</div>
                  <div className="board-card-info">
                    <h3 className="board-card-name">{board.name}</h3>
                    {board.description && <p className="board-card-desc">{board.description}</p>}
                    <span className="board-card-count">
                      <i className="fas fa-layer-group"></i> {board.card_count ?? 0} tarjetas
                    </span>
                  </div>
                </div>
                <button
                  className="board-card-delete"
                  onClick={e => {
                    e.stopPropagation();
                    if (confirm(`¿Eliminar el tablero "${board.name}"? Esta acción no se puede deshacer.`)) {
                      deleteMut.mutate(board.id);
                    }
                  }}
                  title="Eliminar tablero"
                  aria-label={`Eliminar tablero ${board.name}`}
                >
                  <i className="fas fa-trash"></i>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateBoardModal
          onClose={() => setShowCreate(false)}
          onSuccess={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ['boards'] });
          }}
        />
      )}
    </>
  );
}
