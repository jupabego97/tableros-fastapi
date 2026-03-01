import { useState } from 'react';
import { api } from '../api/client';
import type { BoardCreate } from '../api/client';

const PRESET_COLORS = ['#0ea5e9', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444', '#ec4899', '#06b6d4', '#f97316'];
const PRESET_EMOJIS = ['📱', '💻', '📺', '🖨️', '🎮', '📷', '🔧', '⚙️', '🏭', '🔌', '📋', '🏷️'];

interface Props { onClose: () => void; onSuccess: () => void; }

export default function CreateBoardModal({ onClose, onSuccess }: Props) {
  const [form, setForm] = useState<BoardCreate>({ name: '', color: '#0ea5e9', icon: '📋', description: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!form.name.trim()) { setError('El nombre es requerido'); return; }
    setLoading(true);
    try {
      await api.createBoard({ ...form, name: form.name.trim(), description: form.description || undefined });
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al crear tablero');
    }
    setLoading(false);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-pro" onClick={e => e.stopPropagation()}>
        <div className="modal-pro-header">
          <h3><i className="fas fa-plus-circle"></i> Nuevo Tablero</h3>
          <button className="modal-close" onClick={onClose}><i className="fas fa-times"></i></button>
        </div>
        <div className="modal-pro-body">
          {error && <div className="login-error"><i className="fas fa-exclamation-triangle"></i> {error}</div>}
          <div className="edit-form">
            <div className="form-group">
              <label><i className="fas fa-building"></i> Nombre del proveedor *</label>
              <input
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="Ej: Samsung, Apple, LG..."
                autoFocus
                onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
              />
            </div>
            <div className="form-group">
              <label><i className="fas fa-palette"></i> Color</label>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.3rem' }}>
                {PRESET_COLORS.map(c => (
                  <button
                    key={c}
                    type="button"
                    style={{
                      width: 28, height: 28, borderRadius: '50%', background: c, cursor: 'pointer',
                      border: form.color === c ? '3px solid white' : '2px solid transparent',
                      outline: form.color === c ? `2px solid ${c}` : 'none',
                    }}
                    onClick={() => setForm({ ...form, color: c })}
                    aria-label={`Color ${c}`}
                  />
                ))}
              </div>
            </div>
            <div className="form-group">
              <label><i className="fas fa-smile"></i> Ícono</label>
              <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.3rem' }}>
                {PRESET_EMOJIS.map(em => (
                  <button
                    key={em}
                    type="button"
                    style={{
                      fontSize: '1.3rem', padding: '0.2rem 0.4rem', cursor: 'pointer', background: 'transparent',
                      border: form.icon === em ? '2px solid var(--accent)' : '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                    }}
                    onClick={() => setForm({ ...form, icon: em })}
                  >
                    {em}
                  </button>
                ))}
              </div>
            </div>
            <div className="form-group">
              <label><i className="fas fa-align-left"></i> Descripción (opcional)</label>
              <textarea
                rows={2}
                value={form.description || ''}
                onChange={e => setForm({ ...form, description: e.target.value })}
                placeholder="Descripción del tablero..."
              />
            </div>
          </div>
        </div>
        <div className="modal-pro-footer">
          <button className="btn-cancel" onClick={onClose}>Cancelar</button>
          <button className="btn-save" onClick={handleSubmit} disabled={loading}>
            {loading ? <><i className="fas fa-spinner fa-spin"></i> Creando...</> : <><i className="fas fa-check"></i> Crear tablero</>}
          </button>
        </div>
      </div>
    </div>
  );
}
