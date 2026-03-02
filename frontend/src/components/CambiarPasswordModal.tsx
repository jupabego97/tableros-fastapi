import { useState } from 'react';
import { api } from '../api/client';

interface Props {
  onClose: () => void;
}

export default function CambiarPasswordModal({ onClose }: Props) {
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (newPwd.length < 4) { setError('La nueva contraseña debe tener al menos 4 caracteres'); return; }
    if (newPwd !== confirm) { setError('Las contraseñas no coinciden'); return; }
    setLoading(true);
    try {
      await api.changePassword(oldPwd, newPwd);
      setSuccess(true);
      setTimeout(onClose, 1500);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Error al cambiar contraseña';
      setError(msg.includes('400') || msg.includes('incorrecta') ? 'Contraseña actual incorrecta' : msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" style={{ maxWidth: 380 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2><i className="fas fa-key"></i> Cambiar contraseña</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        {success ? (
          <div className="modal-body" style={{ textAlign: 'center', padding: '2rem' }}>
            <i className="fas fa-check-circle" style={{ fontSize: '2rem', color: '#22c55e' }}></i>
            <p style={{ marginTop: '1rem', fontWeight: 600 }}>¡Contraseña actualizada!</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              {error && <div className="alert-error" style={{ padding: '0.5rem 0.75rem', borderRadius: 6, background: '#fee2e2', color: '#dc2626', fontSize: '0.875rem' }}>{error}</div>}
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.875rem', fontWeight: 500 }}>
                Contraseña actual
                <input type="password" className="form-control" value={oldPwd} onChange={e => setOldPwd(e.target.value)} required autoFocus />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.875rem', fontWeight: 500 }}>
                Nueva contraseña
                <input type="password" className="form-control" value={newPwd} onChange={e => setNewPwd(e.target.value)} required minLength={4} />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.875rem', fontWeight: 500 }}>
                Confirmar nueva contraseña
                <input type="password" className="form-control" value={confirm} onChange={e => setConfirm(e.target.value)} required minLength={4} />
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-secondary" onClick={onClose}>Cancelar</button>
              <button type="submit" className="btn-primary" disabled={loading}>
                {loading ? 'Guardando...' : 'Cambiar contraseña'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
