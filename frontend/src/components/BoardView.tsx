import { useState, useEffect, lazy, Suspense, useCallback, useRef, useMemo } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { InfiniteData } from '@tanstack/react-query';
import { io } from 'socket.io-client';
import { api } from '../api/client';
import type { Board, TarjetaBoardItem, KanbanColumn, Tag, UserInfo, TarjetasBoardResponse, TarjetaUpdate, UserPreferences, SavedView } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import KanbanBoard from './KanbanBoard';
import BusquedaFiltros from './BusquedaFiltros';
import ConexionBadge from './ConexionBadge';
import NotificationCenter from './NotificationCenter';
import Toast from './Toast';
import ActivityFeed from './ActivityFeed';
import CalendarView from './CalendarView';
import BulkActionsBar from './BulkActionsBar';
import { EmptyState, ErrorState } from './UiState';
import { useDebounce } from '../hooks/useDebounce';
import { API_BASE } from '../api/client';

const NuevaTarjetaModal = lazy(() => import('./NuevaTarjetaModal'));
const EditarTarjetaModal = lazy(() => import('./EditarTarjetaModal'));
const EstadisticasModal = lazy(() => import('./EstadisticasModal'));
const ExportarModal = lazy(() => import('./ExportarModal'));

type ThemeMode = 'light' | 'dark';
type ViewMode = 'kanban' | 'calendar';
type ToastType = 'success' | 'warning' | 'info' | 'error';
type BoardInfiniteData = InfiniteData<TarjetasBoardResponse, number>;

interface Props {
  board: Board;
  onBack: () => void;
  theme: ThemeMode;
  onThemeChange: (t: ThemeMode) => void;
}

type ReorderItem = { id: number; columna: string; posicion: number };
type SocketEnvelope<T> = { event_version?: number; data?: T } | T;

function unwrapSocketData<T>(payload: SocketEnvelope<T>): T {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data;
  }
  return payload as T;
}

function loadFilters(boardId: number) {
  try {
    const saved = localStorage.getItem(`kanban-filters-${boardId}`);
    return saved ? JSON.parse(saved) : { search: '', estado: '', prioridad: '', asignado_a: '', tag: '' };
  } catch {
    return { search: '', estado: '', prioridad: '', asignado_a: '', tag: '' };
  }
}

const DEFAULT_PREFERENCES: UserPreferences = {
  saved_views: [],
  default_view: null,
  density: 'comfortable',
  theme: 'dark',
  mobile_behavior: 'horizontal_swipe',
};

function applyCardPatch(data: BoardInfiniteData | undefined, card: TarjetaBoardItem): BoardInfiniteData | undefined {
  if (!data) return data;
  let found = false;
  const nextPages = data.pages.map(page => {
    const idx = page.tarjetas.findIndex(t => t.id === card.id);
    if (idx === -1) return page;
    found = true;
    const nextTarjetas = [...page.tarjetas];
    nextTarjetas[idx] = { ...nextTarjetas[idx], ...card };
    return { ...page, tarjetas: nextTarjetas };
  });
  if (!found && nextPages.length > 0) {
    const first = nextPages[0];
    nextPages[0] = { ...first, tarjetas: [card, ...first.tarjetas] };
  }
  return { ...data, pages: nextPages };
}

function removeCardPatch(data: BoardInfiniteData | undefined, id: number): BoardInfiniteData | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map(page => ({
      ...page,
      tarjetas: page.tarjetas.filter(t => t.id !== id),
    })),
  };
}

function applyReorderPatch(data: BoardInfiniteData | undefined, items: ReorderItem[]): BoardInfiniteData | undefined {
  if (!data || !items.length) return data;
  const byId = new Map(items.map(i => [i.id, i]));
  return {
    ...data,
    pages: data.pages.map(page => ({
      ...page,
      tarjetas: page.tarjetas.map(t => {
        const upd = byId.get(t.id);
        return upd ? { ...t, columna: upd.columna, posicion: upd.posicion } : t;
      }),
    })),
  };
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

export default function BoardView({ board, onBack, theme, onThemeChange }: Props) {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const [mobileHome, setMobileHome] = useState(true);
  const reorderBufferRef = useRef<ReorderItem[]>([]);
  const reorderTimerRef = useRef<number | null>(null);
  const boardId = board.id;

  const [connStatus, setConnStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');

  const [showNew, setShowNew] = useState(false);
  const [editCardId, setEditCardId] = useState<number | null>(null);
  const [showStats, setShowStats] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [showActivity, setShowActivity] = useState(false);

  const [viewMode, setViewMode] = useState<ViewMode>('kanban');

  const [filtros, setFiltros] = useState(() => loadFilters(boardId));
  const debouncedSearch = useDebounce(filtros.search, 300);
  useEffect(() => {
    localStorage.setItem(`kanban-filters-${boardId}`, JSON.stringify(filtros));
  }, [filtros, boardId]);

  const [groupBy, setGroupBy] = useState<string>('none');
  const [compactView, setCompactView] = useState(false);

  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const [undoAction, setUndoAction] = useState<{ cardId: number; oldCol: string; msg: string } | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: ToastType } | null>(null);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [activeSavedViewId, setActiveSavedViewId] = useState<string>('');
  const hasAppliedDefaultViewRef = useRef(false);

  const { data: preferences = DEFAULT_PREFERENCES } = useQuery<UserPreferences>({
    queryKey: ['preferences'],
    queryFn: api.getMyPreferences,
  });

  const prefsMutation = useMutation({
    mutationFn: (nextPrefs: UserPreferences) => api.updateMyPreferences(nextPrefs),
    onSuccess: data => qc.setQueryData(['preferences'], data),
    onError: () => setToast({ msg: 'No se pudieron guardar preferencias', type: 'warning' }),
  });

  const boardQueryKey = useMemo(
    () => ['tarjetas-board', boardId, debouncedSearch, filtros.estado, filtros.prioridad, filtros.asignado_a, filtros.tag] as const,
    [boardId, debouncedSearch, filtros.estado, filtros.prioridad, filtros.asignado_a, filtros.tag],
  );
  const {
    data: boardData,
    isLoading: loadingCards,
    isError: boardIsError,
    error: boardError,
    refetch: refetchBoard,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery<TarjetasBoardResponse, Error, BoardInfiniteData, typeof boardQueryKey, string | undefined>({
    queryKey: boardQueryKey,
    queryFn: ({ pageParam }) => api.getTarjetasBoard(boardId, {
      cursor: pageParam,
      search: debouncedSearch || undefined,
      estado: filtros.estado || undefined,
      prioridad: filtros.prioridad || undefined,
      asignado_a: filtros.asignado_a ? Number(filtros.asignado_a) : undefined,
      tag: filtros.tag ? Number(filtros.tag) : undefined,
      mode: 'fast',
      per_page: 200,
      includeImageThumb: true,
    }),
    initialPageParam: undefined,
    getNextPageParam: lastPage => lastPage.next_cursor ?? undefined,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const tarjetas = useMemo(() => {
    if (!boardData?.pages?.length) return [];
    const merged: TarjetaBoardItem[] = [];
    const seen = new Set<number>();
    for (const page of boardData.pages) {
      for (const t of page.tarjetas) {
        if (seen.has(t.id)) continue;
        seen.add(t.id);
        merged.push(t);
      }
    }
    return merged;
  }, [boardData]);

  useEffect(() => {
    if (!hasNextPage || isFetchingNextPage) return;
    const timer = window.setTimeout(() => {
      fetchNextPage().catch(() => undefined);
    }, 50);
    return () => window.clearTimeout(timer);
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const { data: columnas = [] } = useQuery<KanbanColumn[]>({
    queryKey: ['columnas', boardId],
    queryFn: () => api.getColumnas(boardId),
    staleTime: 5 * 60_000,
  });

  const { data: allTags = [] } = useQuery<Tag[]>({
    queryKey: ['tags', boardId],
    queryFn: () => api.getTags(boardId),
    staleTime: 5 * 60_000,
  });

  const { data: users = [] } = useQuery<UserInfo[]>({
    queryKey: ['users'],
    queryFn: api.getUsers,
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (!preferences || hasAppliedDefaultViewRef.current) return;
    hasAppliedDefaultViewRef.current = true;
    if (preferences.theme && preferences.theme !== theme) {
      onThemeChange(preferences.theme);
    }
    if (preferences.default_view) {
      const found = preferences.saved_views.find(v => v.id === preferences.default_view);
      if (found) {
        setFiltros(found.filtros);
        setGroupBy(found.groupBy);
        setCompactView(found.compactView);
        setViewMode(found.viewMode);
        setActiveSavedViewId(found.id);
      }
    }
  }, [preferences, theme, onThemeChange]);

  useEffect(() => {
    const close = () => setShowMoreMenu(false);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, []);

  // Resetear a pantalla de inicio al volver de background (solo móvil)
  useEffect(() => {
    if (!isMobile) return;
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        setMobileHome(true);
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [isMobile]);

  const saveCurrentView = useCallback(() => {
    const nextIndex = (preferences.saved_views?.length || 0) + 1;
    const nextView: SavedView = {
      id: `view_${Date.now()}`,
      name: `Vista ${nextIndex}`,
      filtros,
      groupBy,
      compactView,
      viewMode,
    };
    const saved_views = [...(preferences.saved_views || []), nextView];
    const payload: UserPreferences = {
      ...DEFAULT_PREFERENCES,
      ...preferences,
      saved_views,
      default_view: preferences.default_view || nextView.id,
      theme,
    };
    prefsMutation.mutate(payload);
    setActiveSavedViewId(nextView.id);
    setToast({ msg: 'Vista guardada', type: 'success' });
  }, [preferences, filtros, groupBy, compactView, viewMode, theme, prefsMutation]);

  const applySavedView = useCallback((viewId: string) => {
    setActiveSavedViewId(viewId);
    const selected = preferences.saved_views.find(v => v.id === viewId);
    if (!selected) return;
    setFiltros(selected.filtros);
    setGroupBy(selected.groupBy);
    setCompactView(selected.compactView);
    setViewMode(selected.viewMode);
  }, [preferences.saved_views]);

  const removeSavedView = useCallback(() => {
    if (!activeSavedViewId) return;
    const saved_views = preferences.saved_views.filter(v => v.id !== activeSavedViewId);
    const payload: UserPreferences = {
      ...DEFAULT_PREFERENCES,
      ...preferences,
      saved_views,
      default_view: preferences.default_view === activeSavedViewId ? null : preferences.default_view,
      theme,
    };
    prefsMutation.mutate(payload);
    setActiveSavedViewId('');
    setToast({ msg: 'Vista eliminada', type: 'info' });
  }, [activeSavedViewId, preferences, theme, prefsMutation]);

  const flushReorderBuffer = useCallback(() => {
    const items = reorderBufferRef.current;
    reorderBufferRef.current = [];
    reorderTimerRef.current = null;
    if (!items.length) return;
    qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyReorderPatch(old, items));
  }, [qc]);

  useEffect(() => {
    const url = API_BASE || window.location.origin;
    const safeModeEnv = import.meta.env.VITE_SOCKETIO_SAFE_MODE;
    const safeMode = safeModeEnv ? safeModeEnv === 'true' : import.meta.env.PROD;
    const s = io(url, {
      transports: safeMode ? ['polling'] : ['polling', 'websocket'],
      upgrade: !safeMode,
      reconnection: true,
    });

    s.on('connect', () => {
      setConnStatus('connected');
      s.emit('join_board', { board_id: boardId });
    });
    s.on('disconnect', () => setConnStatus('disconnected'));
    s.on('connect_error', () => setConnStatus('disconnected'));

    s.on('tarjeta_creada', (payload: SocketEnvelope<TarjetaBoardItem>) => {
      const card = unwrapSocketData(payload);
      if (!card?.id) return;
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyCardPatch(old, card));
      qc.invalidateQueries({ queryKey: ['notificaciones', boardId] });
    });

    s.on('tarjeta_actualizada', (payload: SocketEnvelope<TarjetaBoardItem>) => {
      const card = unwrapSocketData(payload);
      if (!card?.id) return;
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyCardPatch(old, card));
      qc.invalidateQueries({ queryKey: ['notificaciones', boardId] });
    });

    s.on('tarjeta_eliminada', (payload: SocketEnvelope<{ id: number }>) => {
      const data = unwrapSocketData(payload);
      if (!data?.id) return;
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => removeCardPatch(old, data.id));
    });

    s.on('tarjetas_reordenadas', (payload: SocketEnvelope<{ items?: ReorderItem[] }>) => {
      const data = unwrapSocketData(payload);
      const items = data?.items;
      if (!Array.isArray(items) || !items.length) return;
      reorderBufferRef.current.push(...items);
      if (reorderTimerRef.current == null) {
        reorderTimerRef.current = window.setTimeout(flushReorderBuffer, 150);
      }
    });

    setConnStatus('connecting');
    return () => {
      s.emit('leave_board', { board_id: boardId });
      if (reorderTimerRef.current != null) {
        window.clearTimeout(reorderTimerRef.current);
      }
      s.disconnect();
    };
  }, [boardId, qc, flushReorderBuffer]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); setShowNew(true); }
      else if (e.key === 'e' || e.key === 'E') { e.preventDefault(); setShowStats(true); }
      else if (e.key === 'x' || e.key === 'X') { e.preventDefault(); setShowExport(true); }
      else if (e.key === '/') { e.preventDefault(); document.querySelector<HTMLInputElement>('.search-box input')?.focus(); }
      else if (e.key === 'Escape') {
        setShowNew(false);
        setEditCardId(null);
        setShowStats(false);
        setShowExport(false);
        setShowActivity(false);
        if (selectMode) {
          setSelectMode(false);
          setSelectedIds([]);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectMode]);

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  }, []);

  const handleBlock = useCallback(async (id: number, reason: string) => {
    try {
      const updated = await api.blockTarjeta(boardId, id, reason);
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyCardPatch(old, updated));
      setToast({ msg: 'Tarjeta bloqueada', type: 'info' });
    } catch {
      setToast({ msg: 'Error al bloquear', type: 'error' });
    }
  }, [boardId, qc]);

  const handleUnblock = useCallback(async (id: number) => {
    try {
      const updated = await api.unblockTarjeta(boardId, id);
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyCardPatch(old, updated));
      setToast({ msg: 'Tarjeta desbloqueada', type: 'success' });
    } catch {
      setToast({ msg: 'Error al desbloquear', type: 'error' });
    }
  }, [boardId, qc]);

  const handleUndo = useCallback(async () => {
    if (!undoAction) return;
    try {
      const updated = await api.updateTarjeta(boardId, undoAction.cardId, { columna: undoAction.oldCol } as TarjetaUpdate);
      qc.setQueriesData<BoardInfiniteData>({ queryKey: ['tarjetas-board'] }, old => applyCardPatch(old, updated as TarjetaBoardItem));
      setUndoAction(null);
      setToast({ msg: 'Movimiento deshecho', type: 'success' });
    } catch {
      setToast({ msg: 'Error al deshacer', type: 'error' });
    }
  }, [boardId, undoAction, qc]);

  const toggleTheme = useCallback(() => {
    const nextTheme: ThemeMode = theme === 'dark' ? 'light' : 'dark';
    onThemeChange(nextTheme);
    const payload: UserPreferences = {
      ...DEFAULT_PREFERENCES,
      ...preferences,
      theme: nextTheme,
    };
    prefsMutation.mutate(payload);
  }, [theme, onThemeChange, preferences, prefsMutation]);

  useEffect(() => {
    if (!undoAction) return;
    const t = setTimeout(() => setUndoAction(null), 8000);
    return () => clearTimeout(t);
  }, [undoAction]);

  if (isMobile && mobileHome) {
    return (
      <div className="mobile-home-screen">
        <div className="mobile-home-logo">
          <span style={{ fontSize: '2rem' }}>{board.icon || '📋'}</span>
          <h1>{board.name}</h1>
        </div>
        <div className="mobile-home-actions">
          <button className="mobile-home-btn mobile-home-btn-primary"
            onClick={() => { setMobileHome(false); setShowNew(true); }}>
            <i className="fas fa-plus-circle"></i>
            <span>Nueva Garantía</span>
          </button>
          <button className="mobile-home-btn mobile-home-btn-secondary"
            onClick={() => setMobileHome(false)}>
            <i className="fas fa-columns"></i>
            <span>Ver Tablero</span>
          </button>
          <button className="mobile-home-btn mobile-home-btn-secondary"
            onClick={onBack}>
            <i className="fas fa-arrow-left"></i>
            <span>Volver a Tableros</span>
          </button>
        </div>
        <ConexionBadge status={connStatus} />

        <Suspense fallback={null}>
          {showNew && (
            <NuevaTarjetaModal
              boardId={boardId}
              onClose={() => setShowNew(false)}
              onSuccess={() => {
                setToast({ msg: 'Tarjeta creada correctamente', type: 'success' });
                qc.invalidateQueries({ queryKey: ['tarjetas-board'] });
              }}
            />
          )}
        </Suspense>
        <div aria-live="polite" aria-atomic="true">
          {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
        </div>
      </div>
    );
  }

  return (
    <>
      <header className="app-header">
        <div className="header-left">
          <button className="header-btn" onClick={onBack} title="Volver a tableros" aria-label="Volver a tableros">
            <i className="fas fa-arrow-left"></i>
          </button>
          <h1 className="app-title">
            <span style={{ marginRight: '0.3rem' }}>{board.icon || '📋'}</span>
            {board.name}
          </h1>
          <ConexionBadge status={connStatus} />
        </div>
        <div className="header-actions">
          <button className="header-btn active" onClick={() => setShowNew(true)} title="Nueva garantia (N)" aria-label="Crear nueva tarjeta">
            <i className="fas fa-plus"></i> <span className="btn-text">Nueva</span>
          </button>

          <NotificationCenter boardId={boardId} />

          <button className="header-btn" onClick={toggleTheme} title="Cambiar tema" aria-label="Cambiar tema">
            <i className={theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon'}></i>
          </button>
          <div className="header-menu-wrap" onClick={e => e.stopPropagation()}>
            <button
              className="header-btn"
              onClick={() => setShowMoreMenu(!showMoreMenu)}
              aria-haspopup="menu"
              aria-expanded={showMoreMenu}
              aria-controls="header-more-menu"
              title="Mas acciones"
            >
              <i className="fas fa-ellipsis-h"></i> <span className="btn-text">Mas</span>
            </button>
            {showMoreMenu && (
              <div id="header-more-menu" className="header-more-menu" role="menu">
                <button className="header-more-item" role="menuitem" onClick={() => { setShowStats(true); setShowMoreMenu(false); }}>
                  <i className="fas fa-chart-bar"></i> Estadisticas
                </button>
                <button className="header-more-item" role="menuitem" onClick={() => { setShowExport(true); setShowMoreMenu(false); }}>
                  <i className="fas fa-file-export"></i> Exportar
                </button>
                <button className="header-more-item" role="menuitem" onClick={() => { setShowActivity(true); setShowMoreMenu(false); }}>
                  <i className="fas fa-stream"></i> Actividad
                </button>
              </div>
            )}
          </div>

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

      <div className="toolbar-row">
        <div className="toolbar-left">
          <div className="view-toggle">
            <button className={`view-toggle-btn ${viewMode === 'kanban' ? 'active' : ''}`}
              onClick={() => setViewMode('kanban')}>
              <i className="fas fa-columns"></i> Kanban
            </button>
            <button className={`view-toggle-btn ${viewMode === 'calendar' ? 'active' : ''}`}
              onClick={() => setViewMode('calendar')}>
              <i className="fas fa-calendar-alt"></i> Calendario
            </button>
          </div>
          <span className="shortcuts-hint toolbar-secondary" title="N = Nueva | E = Estadisticas | X = Exportar | / = Buscar | Esc = Cerrar">
            <i className="fas fa-keyboard"></i> Atajos
          </span>
          <select
            className="header-select toolbar-secondary"
            value={activeSavedViewId}
            onChange={e => applySavedView(e.target.value)}
            aria-label="Vistas guardadas"
          >
            <option value="">Vistas guardadas</option>
            {preferences.saved_views.map(v => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </select>
          <button className="toolbar-btn toolbar-secondary" onClick={saveCurrentView} aria-label="Guardar vista actual">
            <i className="fas fa-save"></i> Guardar vista
          </button>
          <button className="toolbar-btn toolbar-secondary" disabled={!activeSavedViewId} onClick={removeSavedView} aria-label="Eliminar vista guardada">
            <i className="fas fa-trash"></i> Eliminar vista
          </button>
          <select className="header-select toolbar-secondary" value={groupBy} onChange={e => setGroupBy(e.target.value)} title="Agrupar por" aria-label="Agrupar tarjetas">
            <option value="none">Sin agrupar</option>
            <option value="priority">Por prioridad</option>
            <option value="assignee">Por tecnico</option>
          </select>
          <button className={`toolbar-btn ${compactView ? 'active' : ''}`} onClick={() => setCompactView(!compactView)}
            title="Vista compacta" aria-label="Alternar vista compacta">
            <i className={compactView ? 'fas fa-th-list' : 'fas fa-th-large'}></i> <span className="btn-text">Compacta</span>
          </button>
        </div>
        <div className="toolbar-right">
          <button className={`toolbar-btn ${selectMode ? 'active' : ''}`}
            onClick={() => { setSelectMode(!selectMode); if (selectMode) setSelectedIds([]); }}>
            <i className="fas fa-check-double"></i> {selectMode ? 'Cancelar seleccion' : 'Seleccionar'}
          </button>
        </div>
      </div>

      <BusquedaFiltros filtros={filtros} onChange={setFiltros} totalResults={tarjetas.length} users={users} tags={allTags}
        columnas={columnas.map(c => ({ key: c.key, title: c.title }))} />

      <div className="view-container" key={viewMode}>
        {viewMode === 'kanban' ? (
          <>
            {loadingCards ? (
              <div className="skeleton-board">
                {[1, 2, 3, 4].map(i => (
                  <div key={i} className="skeleton-column">
                    <div className="skeleton-header"></div>
                    {[1, 2, 3].map(j => <div key={j} className="skeleton-card"></div>)}
                  </div>
                ))}
              </div>
            ) : boardIsError ? (
              <ErrorState
                title="No se pudo cargar el tablero"
                message={boardError instanceof Error ? boardError.message : 'Error inesperado'}
                actionLabel="Reintentar"
                onAction={() => refetchBoard()}
              />
            ) : tarjetas.length === 0 ? (
              <EmptyState
                title="No hay tarjetas para mostrar"
                message={Object.values(filtros).some(Boolean) ? 'Pruebe limpiar o ajustar filtros.' : 'Cree su primera tarjeta para comenzar.'}
                actionLabel={Object.values(filtros).some(Boolean) ? 'Limpiar filtros' : 'Nueva tarjeta'}
                onAction={() => Object.values(filtros).some(Boolean)
                  ? setFiltros({ search: '', estado: '', prioridad: '', asignado_a: '', tag: '' })
                  : setShowNew(true)}
              />
            ) : (
              <KanbanBoard
                boardId={boardId}
                columnas={columnas}
                tarjetas={tarjetas}
                onEdit={t => setEditCardId(t.id)}
                groupBy={groupBy}
                compactView={compactView}
                selectable={selectMode}
                selectedIds={selectedIds}
                onSelect={toggleSelect}
                onBlock={handleBlock}
                onUnblock={handleUnblock}
                onMoveError={(err) => {
                  let msg = err instanceof Error ? err.message : (err && typeof err === 'object' && 'message' in err) ? String((err as { message: unknown }).message) : 'Error desconocido';
                  if (/ProgrammingError|psycopg2|SQL|column|relation|undefined/i.test(msg)) {
                    msg = 'No se pudo mover la tarjeta. Intenta de nuevo.';
                  } else {
                    msg = `Error al mover: ${msg}`;
                  }
                  setToast({ msg, type: 'error' });
                }}
                onMoveSuccess={(cardId, oldCol, newCol) => {
                  const colTitle = columnas.find(c => c.key === newCol)?.title || newCol;
                  setToast({ msg: `Tarjeta movida a ${colTitle}`, type: 'success' });
                  setUndoAction({ cardId, oldCol, msg: `Movida a ${colTitle}` });
                }}
              />
            )}
          </>
        ) : (
          <CalendarView tarjetas={tarjetas} onSelect={t => setEditCardId(t.id)} />
        )}
      </div>

      {selectMode && selectedIds.length > 0 && (
        <BulkActionsBar
          boardId={boardId}
          selectedIds={selectedIds}
          columns={columnas}
          onClear={() => setSelectedIds([])}
          onDone={() => {
            setSelectedIds([]);
            setSelectMode(false);
            setToast({ msg: 'Operacion en lote completada', type: 'success' });
          }}
        />
      )}

      {undoAction && (
        <div className="undo-toast">
          <span>{undoAction.msg}</span>
          <button onClick={handleUndo}>Deshacer</button>
        </div>
      )}

      {showActivity && <ActivityFeed boardId={boardId} onClose={() => setShowActivity(false)} />}

      <button className="mobile-fab-new" onClick={() => setShowNew(true)} title="Nueva garantia" aria-label="Crear nueva garantia">
        <i className="fas fa-plus"></i>
        <span>Nueva Garantía</span>
      </button>

      <Suspense fallback={null}>
        {showNew && (
          <NuevaTarjetaModal
            boardId={boardId}
            onClose={() => setShowNew(false)}
            onSuccess={() => {
              setToast({ msg: 'Tarjeta creada correctamente', type: 'success' });
              qc.invalidateQueries({ queryKey: ['tarjetas-board'] });
            }}
          />
        )}
        {editCardId != null && (
          <EditarTarjetaModal
            boardId={boardId}
            tarjetaId={editCardId}
            onClose={() => setEditCardId(null)}
          />
        )}
        {showStats && <EstadisticasModal boardId={boardId} onClose={() => setShowStats(false)} />}
        {showExport && <ExportarModal boardId={boardId} onClose={() => setShowExport(false)} />}
      </Suspense>

      <div aria-live="polite" aria-atomic="true">
        {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
      </div>
    </>
  );
}
