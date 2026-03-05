import { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Tag, UserInfo, TarjetaCreate } from '../api/client';

interface Props {
  boardId: number;
  onClose: () => void;
  onSuccess?: () => void;
}

function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 768);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)');
    const handler = () => setIsMobile(mq.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return isMobile;
}

export default function NuevaTarjetaModal({ boardId, onClose, onSuccess }: Props) {
  const [step, setStep] = useState<'capture' | 'preview' | 'form'>('capture');
  const [error, setError] = useState('');
  const [flash, setFlash] = useState(false);
  const [capturedPreview, setCapturedPreview] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const isMobile = useIsMobile();
  const [cameraActive, setCameraActive] = useState(() => window.innerWidth <= 768);
  const [photoFiles, setPhotoFiles] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [uploadState, setUploadState] = useState<'idle' | 'uploading' | 'partial_failed' | 'done'>('idle');

  const [form, setForm] = useState({
    nombre_cliente: '',
    producto: '',
    numero_factura: '',
    problema: '',
    whatsapp: '',
    fecha_limite: '',
    imagen_url: '',
    prioridad: 'media',
    asignado_a: '' as string | number,
    costo_estimado: '' as string | number,
    notas_tecnicas: '',
  });
  const [selectedTags, setSelectedTags] = useState<number[]>([]);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const { data: allTags = [] } = useQuery({ queryKey: ['tags', boardId], queryFn: () => api.getTags(boardId) });
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: api.getUsers });

  const createMut = useMutation({
    mutationFn: (data: TarjetaCreate) => api.createTarjeta(boardId, data),
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Error al crear'),
  });

  useEffect(() => {
    const currentVideo = videoRef.current;
    return () => {
      if (currentVideo?.srcObject) {
        (currentVideo.srcObject as MediaStream).getTracks().forEach(t => t.stop());
      }
    };
  }, []);

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      if (videoRef.current) { videoRef.current.srcObject = stream; setCameraActive(true); }
    } catch {
      setError('No se pudo acceder a la cámara');
      setCameraActive(false);
    }
  };

  // En móvil: ir directo a la cámara al abrir, sin menú de opciones
  useEffect(() => {
    if (isMobile && step === 'capture') {
      setCameraActive(true);
      startCamera();
    }
  }, [isMobile, step]);

  const capturePhoto = () => {
    if (!videoRef.current || !canvasRef.current) return;
    const v = videoRef.current;
    const c = canvasRef.current;
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext('2d')?.drawImage(v, 0, 0);
    const dataUrl = c.toDataURL('image/jpeg', 0.7);
    setFlash(true);
    setTimeout(() => setFlash(false), 200);
    setCapturedPreview(dataUrl);
    setStep('preview');
    if (videoRef.current?.srcObject) {
      (videoRef.current.srcObject as MediaStream).getTracks().forEach(t => t.stop());
      setCameraActive(false);
    }
  };

  const confirmPhoto = () => {
    if (!capturedPreview) return;
    setForm(prev => ({ ...prev, imagen_url: capturedPreview }));
    setCapturedPreview(null);
    setStep('form');
  };

  const retakePhoto = () => {
    setCapturedPreview(null);
    setCameraActive(true);
    setStep('capture');
    startCamera();
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const all = [...photoFiles, ...files].slice(0, 10);
    if (all.length < photoFiles.length + files.length) {
      setError('Limite maximo de 10 fotos por tarjeta');
    }
    setPhotoFiles(all);
    const readers = all.map(file => new Promise<string>((resolve) => {
      const r = new FileReader();
      r.onload = ev => resolve((ev.target?.result as string) || '');
      r.readAsDataURL(file);
    }));
    Promise.all(readers).then(previews => {
      setPhotoPreviews(previews.filter(Boolean));
      setStep('form');
    });
  };

  const validate = (): boolean => {
    const errs: Record<string, string> = {};
    if (form.whatsapp && !/^\+?\d{7,15}$/.test(form.whatsapp.replace(/[\s-]/g, ''))) {
      errs.whatsapp = 'Formato: +57 300 123 4567';
    }
    setValidationErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    try {
      const created = await createMut.mutateAsync({
        nombre_cliente: form.nombre_cliente.trim() || undefined,
        producto: form.producto.trim() || undefined,
        numero_factura: form.numero_factura.trim() || undefined,
        problema: form.problema.trim() || undefined,
        whatsapp: form.whatsapp.trim() || undefined,
        fecha_limite: form.fecha_limite || undefined,
        imagen_url: photoFiles.length > 0 ? undefined : (form.imagen_url || undefined),
        prioridad: form.prioridad,
        asignado_a: form.asignado_a ? Number(form.asignado_a) : undefined,
        costo_estimado: form.costo_estimado ? Number(form.costo_estimado) : undefined,
        notas_tecnicas: form.notas_tecnicas || undefined,
        tags: selectedTags.length ? selectedTags : undefined,
      });
      if (photoFiles.length > 0) {
        setUploadState('uploading');
        try {
          await api.uploadTarjetaMedia(boardId, created.id, photoFiles);
          setUploadState('done');
        } catch {
          setUploadState('partial_failed');
          setError('Tarjeta creada, pero algunas fotos no se pudieron subir');
        }
      }
      onSuccess?.();
      onClose();
    } catch {
      setUploadState('idle');
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-pro" onClick={e => e.stopPropagation()}>
        <div className="modal-pro-header">
          <h3><i className="fas fa-plus-circle"></i> Nueva Garantía</h3>
          <button className="modal-close" onClick={onClose}><i className="fas fa-times"></i></button>
        </div>

        <div className="modal-pro-body">
          {error && <div className="login-error"><i className="fas fa-exclamation-triangle"></i> {error}</div>}

          {step === 'capture' && (
            <div className={`capture-step ${isMobile && cameraActive ? 'camera-fullscreen' : ''}`}>
              {!cameraActive && (
                <p className="capture-instructions">
                  <i className="fas fa-camera"></i> Toma una foto del equipo
                </p>
              )}
              {cameraActive ? (
                <div className={`camera-container ${isMobile ? 'camera-fullscreen-inner' : ''}`}>
                  {isMobile && (
                    <button type="button" className="camera-back-btn" onClick={() => { (videoRef.current?.srcObject as MediaStream)?.getTracks().forEach(t => t.stop()); onClose(); }} aria-label="Cerrar cámara">
                      <i className="fas fa-times"></i>
                    </button>
                  )}
                  {flash && <div className="capture-flash" aria-hidden="true" />}
                  <video ref={videoRef} autoPlay playsInline muted className="camera-preview" />
                  <canvas ref={canvasRef} style={{ display: 'none' }} />
                  <button className="btn-capture btn-capture-large" onClick={capturePhoto}
                    type="button" aria-label="Tomar foto">
                    <i className="fas fa-camera"></i>
                  </button>
                </div>
              ) : (
                <div className="capture-options capture-options-horizontal">
                  <button className="capture-btn capture-btn-large" onClick={startCamera} type="button">
                    <i className="fas fa-camera"></i>
                    <span>Usar cámara</span>
                  </button>
                  <label className="capture-btn capture-btn-large">
                    <i className="fas fa-image"></i>
                    <span>Subir imagenes</span>
                    <input type="file" accept="image/*" multiple onChange={handleFileUpload} style={{ display: 'none' }} />
                  </label>
                  <button className="capture-btn capture-btn-large skip" onClick={() => setStep('form')} type="button">
                    <i className="fas fa-keyboard"></i>
                    <span>Sin imagen</span>
                  </button>
                </div>
              )}
            </div>
          )}

          {step === 'preview' && capturedPreview && (
            <div className="capture-preview-step">
              <p className="capture-instructions">Revisa la foto</p>
              <div className="capture-preview-image">
                <img src={capturedPreview} alt="Vista previa" />
              </div>
              <div className="capture-preview-actions">
                <button className="btn-cancel" onClick={retakePhoto} type="button">
                  <i className="fas fa-redo"></i> Repetir
                </button>
                <button className="btn-save" onClick={confirmPhoto} type="button">
                  <i className="fas fa-check"></i> Aceptar
                </button>
              </div>
            </div>
          )}

          {step === 'form' && (
            <div className="edit-form">
              <div className="form-essentials">
                <div className="form-group">
                  <label><i className="fas fa-exclamation-circle"></i> Descripción del problema</label>
                  <textarea rows={isMobile ? 3 : 4} value={form.problema} onChange={e => setForm({ ...form, problema: e.target.value })}
                    placeholder="Describe el problema reportado..." autoFocus />
                </div>
              </div>

              <div className="form-advanced-accordion">
                <button type="button" className="form-advanced-toggle" onClick={() => setAdvancedOpen(!advancedOpen)}
                  aria-expanded={advancedOpen}>
                  <i className={`fas fa-chevron-${advancedOpen ? 'up' : 'down'}`}></i> Más opciones
                </button>
                {advancedOpen && (
                  <div className="form-advanced-content">
                    <div className="form-row">
                      <div className="form-group">
                        <label><i className="fas fa-user"></i> Cliente</label>
                        <input value={form.nombre_cliente} onChange={e => setForm({ ...form, nombre_cliente: e.target.value })} placeholder="Nombre del cliente" />
                      </div>
                      <div className="form-group">
                        <label><i className="fab fa-whatsapp"></i> WhatsApp</label>
                        <input value={form.whatsapp} onChange={e => setForm({ ...form, whatsapp: e.target.value })}
                          placeholder="+57 300 123 4567" className={validationErrors.whatsapp ? 'error' : ''} />
                        {validationErrors.whatsapp && <span className="field-error">{validationErrors.whatsapp}</span>}
                      </div>
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label><i className="fas fa-box"></i> Producto</label>
                        <input value={form.producto} onChange={e => setForm({ ...form, producto: e.target.value })} placeholder="Modelo o descripción del equipo" />
                      </div>
                      <div className="form-group">
                        <label><i className="fas fa-file-invoice"></i> N° Factura</label>
                        <input value={form.numero_factura} onChange={e => setForm({ ...form, numero_factura: e.target.value })} placeholder="Opcional" />
                      </div>
                    </div>
                    <div className="form-group">
                      <label><i className="fas fa-calendar"></i> Fecha límite</label>
                      <input type="date" value={form.fecha_limite} onChange={e => setForm({ ...form, fecha_limite: e.target.value })} />
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label><i className="fas fa-flag"></i> Prioridad</label>
                        <select value={form.prioridad} onChange={e => setForm({ ...form, prioridad: e.target.value })}>
                          <option value="alta">Alta</option>
                          <option value="media">Media</option>
                          <option value="baja">Baja</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label><i className="fas fa-user-cog"></i> Asignar a</label>
                        <select value={form.asignado_a} onChange={e => setForm({ ...form, asignado_a: e.target.value })}>
                          <option value="">Sin asignar</option>
                          {users.map((u: UserInfo) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                        </select>
                      </div>
                    </div>
                    <div className="form-group">
                      <label><i className="fas fa-wrench"></i> Notas técnicas</label>
                      <textarea rows={2} value={form.notas_tecnicas} onChange={e => setForm({ ...form, notas_tecnicas: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label><i className="fas fa-dollar-sign"></i> Costo estimado</label>
                      <input type="number" value={form.costo_estimado} onChange={e => setForm({ ...form, costo_estimado: e.target.value })} placeholder="0" />
                    </div>
                    {allTags.length > 0 && (
                      <div className="form-group">
                        <label><i className="fas fa-tags"></i> Etiquetas</label>
                        <div className="tags-select">
                          {allTags.map((tag: Tag) => (
                            <button key={tag.id} type="button"
                              className={`tag-chip-btn ${selectedTags.includes(tag.id) ? 'selected' : ''}`}
                              style={{
                                borderColor: tag.color, color: selectedTags.includes(tag.id) ? '#fff' : tag.color,
                                background: selectedTags.includes(tag.id) ? tag.color : 'transparent'
                              }}
                              onClick={() => setSelectedTags(p => p.includes(tag.id) ? p.filter(i => i !== tag.id) : [...p, tag.id])}>
                              {tag.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
              {form.imagen_url && (
                <div className="preview-image">
                  <img src={form.imagen_url} alt="Preview" />
                  <button className="btn-del-sm" onClick={() => setForm({ ...form, imagen_url: '' })}><i className="fas fa-times"></i></button>
                </div>
              )}
              {photoPreviews.length > 0 && (
                <div className="photo-grid">
                  {photoPreviews.map((src, idx) => (
                    <div key={`${src}-${idx}`} className="preview-image">
                      <img src={src} alt={`Foto ${idx + 1}`} />
                    </div>
                  ))}
                  <small>{photoPreviews.length}/10 fotos</small>
                </div>
              )}
              {uploadState !== 'idle' && <small>Estado fotos: {uploadState}</small>}
            </div>
          )}
        </div>

        {step === 'form' && (
          <div className="modal-pro-footer">
            <button className="btn-cancel" onClick={() => setStep('capture')}>
              <i className="fas fa-arrow-left"></i> Volver
            </button>
            <button className="btn-save" onClick={handleSubmit} disabled={createMut.isPending}>
              {createMut.isPending ? <><i className="fas fa-spinner fa-spin"></i> Creando...</> : <><i className="fas fa-check"></i> Crear</>}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
