// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { FC, useCallback, useEffect, useMemo, useState } from 'react';
import '../scss/App.scss';

import { useDispatch, useSelector } from "react-redux";
import {
    DataFormulatorState,
    dfActions,
    dfSelectors,
    fetchGlobalModelList,
    DEFAULT_ROW_LIMIT,
    DEFAULT_ROW_LIMIT_EPHEMERAL,
} from './dfSlice'
import { getBrowserId, generateUUID } from './identity';
import type { AuthInfo } from './oidcConfig';
import { OidcCallback } from './OidcCallback';
import { IdentityMigrationDialog } from './IdentityMigrationDialog';

import { red, purple, blue, brown, yellow, orange, } from '@mui/material/colors';
import { palettes, defaultPaletteKey, paletteKeys, bgAlpha } from './tokens';

import _ from 'lodash';

import {
    Button,
    Tooltip,
    Typography,
    Box,
    Divider,
    DialogTitle,
    Dialog,
    DialogContent,
    Link,
    DialogContentText,
    DialogActions,
    ToggleButtonGroup,
    ToggleButton,
    Menu,
    MenuItem,
    TextField,
    IconButton,
    Select,
    FormControl,
    InputLabel,
    ListItemIcon,
    ListItemText,
    CircularProgress,
    LinearProgress,
} from '@mui/material';


import { alpha, createTheme, ThemeProvider, useTheme } from '@mui/material/styles';

import LogoutIcon from '@mui/icons-material/Logout';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import ClearIcon from '@mui/icons-material/Clear';

import { DataFormulatorFC } from '../views/DataFormulator';
import { useAutoSave } from './useAutoSave';
import { useWorkspaceAutoName } from './useWorkspaceAutoName';

import GridViewIcon from '@mui/icons-material/GridView';
import ViewSidebarIcon from '@mui/icons-material/ViewSidebar';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import {
    createBrowserRouter,
    Link as RouterLink,
    Outlet,
    RouterProvider,
    useLocation,
    useSearchParams,
} from "react-router-dom";
import { About } from '../views/About';
import { MessageSnackbar } from '../views/MessageSnackbar';
import { ChartRenderService } from '../views/ChartRenderService';
import { DictTable } from '../components/ComponentType';
import { AppDispatch } from './store';
import { AnvilLoader } from '../components/AnvilLoader';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import DownloadIcon from '@mui/icons-material/Download';
import SaveIcon from '@mui/icons-material/Save';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import RefreshIcon from '@mui/icons-material/Refresh';
import { getUrls } from './utils';
import { apiRequest } from './apiClient';
import { listWorkspaces, loadWorkspace, deleteWorkspace, saveWorkspaceState, onWorkspaceListChanged } from './workspaceService';
import { getSerializableState } from './useAutoSave';
import store, { persistor } from './store';
import { UnifiedDataUploadDialog } from '../views/UnifiedDataUploadDialog';
import ChatIcon from '@mui/icons-material/Chat';
import ArticleIcon from '@mui/icons-material/Article';
import EditIcon from '@mui/icons-material/Edit';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import UploadIcon from '@mui/icons-material/Upload';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import PublicIcon from '@mui/icons-material/Public';
import { useTranslation } from 'react-i18next';
import { syncVegaLocale } from '../i18n/vega-locale';

declare module '@mui/material/styles' {
    interface PaletteColor {
        bgcolor?: string;
        textColor?: string;
    }
    interface SimplePaletteColorOptions {
        bgcolor?: string;
        textColor?: string;
    }
    interface Palette {
        derived: Palette['primary'];
        custom: Palette['primary'];
    }
    interface PaletteOptions {
        derived: PaletteOptions['primary'];
        custom: PaletteOptions['primary'];
    }
}

export const toolName = "Data Formulator"

const LANGUAGE_LABELS: Record<string, string> = {
    en: 'EN',
    zh: '中文',
    ja: '日本語',
    ko: '한국어',
    fr: 'FR',
    de: 'DE',
};

const LanguageSwitcher: React.FC = () => {
    const { i18n } = useTranslation();
    const availableLanguages = useSelector(
        (state: DataFormulatorState) => state.serverConfig.AVAILABLE_LANGUAGES
    );

    if (!availableLanguages || availableLanguages.length <= 1) return null;

    return (
        <ToggleButtonGroup
            value={i18n.language.split('-')[0]}
            exclusive
            onChange={(_, value) => value && i18n.changeLanguage(value)}
            size="small"
            sx={{ 
                height: '28px', 
                my: 'auto',
                '& .MuiToggleButton-root': {
                    textTransform: 'none',
                    fontSize: '12px',
                    py: 0,
                    minWidth: '40px',
                    color: 'text.secondary',
                    borderColor: 'divider',
                    '&.Mui-selected': {
                        color: 'text.primary',
                    },
                },
            }}
        >
            {availableLanguages.map(lang => (
                <ToggleButton key={lang} value={lang}>
                    {LANGUAGE_LABELS[lang] || lang.toUpperCase()}
                </ToggleButton>
            ))}
        </ToggleButtonGroup>
    );
};

export interface AppFCProps {
}

// Extract menu components into separate components to prevent full app re-renders
const TableMenu: React.FC = () => {
    const [dialogOpen, setDialogOpen] = useState<boolean>(false);
    const { t } = useTranslation();
    
    return (
        <>
            <Button
                variant="text"
                onClick={() => setDialogOpen(true)}
                sx={{ textTransform: 'none' }}
            >
                {t('appBar.data')}
            </Button>
            
            {/* Unified Data Upload Dialog */}
            <UnifiedDataUploadDialog 
                open={dialogOpen}
                onClose={() => setDialogOpen(false)}
                initialTab="menu"
            />
        </>
    );
};


const WorkspacePickerDialog: React.FC<{open: boolean, onClose: () => void}> = ({open, onClose}) => {
    const [workspaces, setWorkspaces] = useState<{id: string, display_name: string, saved_at: string}[]>([]);
    const [loading, setLoading] = useState(false);
    const [listLoading, setListLoading] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
    const dispatch = useDispatch();
    const activeWorkspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    const { t } = useTranslation();

    const fetchWsList = useCallback(async () => {
        setListLoading(true);
        try {
            const sessions = await listWorkspaces();
            setWorkspaces(sessions as any);
        } catch (e) { /* ignore */ }
        setListLoading(false);
    }, []);

    useEffect(() => {
        if (!open) return;
        fetchWsList();
    }, [open, fetchWsList]);

    useEffect(() => {
        if (!open) return;
        return onWorkspaceListChanged(fetchWsList);
    }, [open, fetchWsList]);

    const handleOpen = async (wsId: string) => {
        if (activeWorkspace?.id === wsId) { onClose(); return; }
        try { await saveWorkspaceState(getSerializableState(store.getState())); } catch { /* best effort */ }
        const wsEntry = workspaces.find(w => w.id === wsId);
        setLoading(true);
        dispatch(dfActions.setSessionLoading({ loading: true, label: t('workspace.openingWorkspace') }));
        onClose();
        try {
            const result = await loadWorkspace(wsId);
            if (result) {
                const displayName = result.displayName || wsEntry?.display_name || wsId;
                dispatch(dfActions.loadState({ ...result.state, activeWorkspace: { id: wsId, displayName } }));
                dispatch(dfActions.addMessages({ timestamp: Date.now(), component: "Workspace", type: "success", value: t('workspace.openedSession', { name: displayName }) }));
            } else {
                dispatch(dfActions.addMessages({ timestamp: Date.now(), component: "Workspace", type: "error", value: t('workspace.failedToOpenWorkspace') }));
            }
        } catch (e) {
            dispatch(dfActions.addMessages({ timestamp: Date.now(), component: "Workspace", type: "error", value: t('workspace.failedToOpenWorkspace') }));
        }
        setLoading(false);
        dispatch(dfActions.setSessionLoading({ loading: false }));
    };

    const handleCreate = () => {
        dispatch(dfActions.resetState());
        onClose();
    };

    const handleDelete = async (workspaceId: string) => {
        try {
            await deleteWorkspace(workspaceId);
            setWorkspaces(prev => prev.filter(s => s.id !== workspaceId));
            dispatch(dfActions.addMessages({ timestamp: Date.now(), component: "Workspace", type: "success", value: t('workspace.deletedSession', { name: workspaceId }) }));
        } catch (e) { /* ignore */ }
        setConfirmDelete(null);
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
            <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                {t('workspace.sessions')}
                <Tooltip title={t('workspace.refreshList')}>
                    <IconButton size="small" onClick={fetchWsList} disabled={listLoading} sx={{ color: 'text.secondary' }}>
                        {listLoading ? <CircularProgress size={18} /> : <RefreshIcon fontSize="small" />}
                    </IconButton>
                </Tooltip>
            </DialogTitle>
            <DialogContent sx={{ px: 1 }}>
                {listLoading && workspaces.length === 0 ? (
                    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 4, gap: 1.5 }}>
                        <CircularProgress size={28} />
                        <Typography variant="body2" color="text.secondary">{t('workspace.loadingSessions')}</Typography>
                    </Box>
                ) : (
                    <>
                        {/* New session — same row style as session items */}
                        <Box
                            sx={{
                                display: 'flex', alignItems: 'center',
                                px: 1.5, py: 1, mx: 0, my: 0.5, borderRadius: 1, cursor: 'pointer',
                                '&:hover': { backgroundColor: 'action.hover' },
                                transition: 'background-color 0.15s',
                            }}
                            onClick={handleCreate}
                        >
                            <Typography variant="body2" color="primary" sx={{ fontWeight: 500 }}>
                                {t('workspace.newSession')}
                            </Typography>
                        </Box>
                        {workspaces.length > 0 && <Divider sx={{ my: 0.5 }} />}
                        {workspaces.map(s => (
                        <Box
                            key={s.id}
                            sx={{
                                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                px: 1.5, py: 1, mx: 0, my: 0.5, borderRadius: 1, cursor: 'pointer',
                                backgroundColor: activeWorkspace?.id === s.id ? 'action.selected' : 'transparent',
                                '&:hover': { backgroundColor: activeWorkspace?.id === s.id ? 'action.selected' : 'action.hover' },
                                transition: 'background-color 0.15s',
                            }}
                            onClick={() => handleOpen(s.id)}
                        >
                            <Box sx={{ flex: 1, minWidth: 0 }}>
                                <Typography variant="body2" fontWeight={activeWorkspace?.id === s.id ? 'bold' : 'normal'} noWrap>
                                    {s.display_name} {activeWorkspace?.id === s.id ? t('workspace.active') : ''}
                                </Typography>
                                {s.saved_at && (
                                    <Typography variant="caption" color="text.secondary">
                                        {new Date(s.saved_at).toLocaleString()}
                                    </Typography>
                                )}
                            </Box>
                            {activeWorkspace?.id !== s.id && (
                                confirmDelete === s.id ? (
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }} onClick={e => e.stopPropagation()}>
                                        <Button size="small" color="error" sx={{ minWidth: 0, fontSize: 11, textTransform: 'none' }}
                                            onClick={() => handleDelete(s.id)}>{t('workspace.delete')}</Button>
                                        <Button size="small" sx={{ minWidth: 0, fontSize: 11, textTransform: 'none' }}
                                            onClick={() => setConfirmDelete(null)}>{t('workspace.cancel')}</Button>
                                    </Box>
                                ) : (
                                    <Tooltip title={t('workspace.deleteSession')}>
                                        <IconButton size="small" onClick={(e) => { e.stopPropagation(); setConfirmDelete(s.id); }} sx={{ color: 'text.secondary' }}>
                                            <ClearIcon fontSize="small" />
                                        </IconButton>
                                    </Tooltip>
                                )
                            )}
                        </Box>
                    ))
                    }
                    </>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>{t('workspace.close')}</Button>
            </DialogActions>
        </Dialog>
    );
};

const WorkspaceMenu: React.FC = () => {
    const [pickerOpen, setPickerOpen] = useState(false);
    const activeWorkspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    const serverConfig = useSelector((state: DataFormulatorState) => state.serverConfig);
    const { t } = useTranslation();
    const diskPersistenceDisabled = false; // all backends support workspace switching

    console.log('Rendering WorkspaceMenu, activeWorkspace:', activeWorkspace, 'serverConfig:', serverConfig); // Debug log for rendering and state
    console.log(serverConfig); // Debug log for serverConfig
    console.log(activeWorkspace); // Debug log for activeWorkspace

    if (!activeWorkspace) return null;

    return (
        <>
            <Tooltip title={t('workspace.sessionTooltip', { name: activeWorkspace?.id || '' })} placement="bottom">
                <Box 
                    onClick={() => !diskPersistenceDisabled && setPickerOpen(true)}
                    sx={{ 
                        display: 'flex', alignItems: 'center', gap: 0.5,
                        cursor: 'pointer',
                        px: 1,
                        py: 0.25,
                        borderRadius: 1,
                        '&:hover': { backgroundColor: 'rgba(0,0,0,0.04)' },
                        '&:hover .ws-chevron': { opacity: 1 },
                    }}
                >
                    <Typography noWrap sx={{ 
                        fontSize: 14, 
                        fontWeight: 500, 
                        color: 'text.primary',
                        maxWidth: 280,
                        letterSpacing: '0.01em',
                    }}>
                        {activeWorkspace?.displayName || activeWorkspace?.id}
                    </Typography>
                    <KeyboardArrowDownIcon className="ws-chevron" sx={{ fontSize: 16, color: 'text.secondary', opacity: 0.4, transition: 'opacity 0.15s' }} />
                </Box>
            </Tooltip>
            <WorkspacePickerDialog open={pickerOpen} onClose={() => setPickerOpen(false)} />
        </>
    );
};

// Exit the current session and return to the front-page (no workspace).
// Saves work first so the session is recoverable from the workspace picker.
const ExitSessionButton: React.FC = () => {
    const dispatch = useDispatch();
    const state = useSelector((s: DataFormulatorState) => s);
    const { t } = useTranslation();

    const handleExit = async () => {
        try { await saveWorkspaceState(getSerializableState(state)); } catch { /* best effort */ }
        dispatch(dfActions.resetState());
    };

    return (
        <Tooltip title={t('workspace.exitSessionTooltip', { defaultValue: 'Exit session and return to the workspace picker' })} placement="bottom">
            <Button
                size="small"
                variant="text"
                onClick={handleExit}
                startIcon={<LogoutIcon sx={{ fontSize: 16 }} />}
                sx={{
                    textTransform: 'none',
                    fontSize: '13px',
                    fontWeight: 400,
                    px: 1.5,
                    py: 0.5,
                    minWidth: 'auto',
                    lineHeight: 1.5,
                    color: 'text.secondary',
                    '&:hover': { color: 'text.primary', backgroundColor: 'rgba(0, 0, 0, 0.04)' },
                }}
            >
                {t('workspace.exit', { defaultValue: 'Exit' })}
            </Button>
        </Tooltip>
    );
};

const ConfigDialog: React.FC = () => {
    const [open, setOpen] = useState(false);
    const dispatch = useDispatch();
    const { t } = useTranslation();
    const config = useSelector((state: DataFormulatorState) => state.config);
    const isEphemeral = useSelector((state: DataFormulatorState) => state.serverConfig?.WORKSPACE_BACKEND === 'ephemeral');
    const rowLimitDefault = isEphemeral ? DEFAULT_ROW_LIMIT_EPHEMERAL : DEFAULT_ROW_LIMIT;
    const rowLimitMax = DEFAULT_ROW_LIMIT;


    const [formulateTimeoutSeconds, setFormulateTimeoutSeconds] = useState(config.formulateTimeoutSeconds ?? 180);
    const [defaultChartWidth, setDefaultChartWidth] = useState(config.defaultChartWidth ?? 300);
    const [defaultChartHeight, setDefaultChartHeight] = useState(config.defaultChartHeight ?? 300);
    const [maxStretchFactor, setMaxStretchFactor] = useState(config.maxStretchFactor ?? 1.5);
    const [frontendRowLimit, setFrontendRowLimit] = useState(config.frontendRowLimit ?? rowLimitDefault);
    const [paletteKey, setPaletteKey] = useState(
        (config.paletteKey && palettes[config.paletteKey]) ? config.paletteKey : defaultPaletteKey
    );

    const hasChanges = formulateTimeoutSeconds !== config.formulateTimeoutSeconds || 
                      defaultChartWidth !== config.defaultChartWidth ||
                      defaultChartHeight !== config.defaultChartHeight ||
                      maxStretchFactor !== config.maxStretchFactor ||
                      frontendRowLimit !== config.frontendRowLimit ||
                      paletteKey !== ((config.paletteKey && palettes[config.paletteKey]) ? config.paletteKey : defaultPaletteKey);

    return (
        <>
            <Tooltip title={t('app.settings')}>
                <IconButton
                    size="small"
                    onClick={() => setOpen(true)}
                    aria-label={t('app.settings')}
                    sx={{
                        p: 0.5,
                        color: 'text.secondary',
                        '&:hover': { color: 'text.primary', backgroundColor: 'rgba(0, 0, 0, 0.04)' },
                    }}
                >
                    <SettingsOutlinedIcon fontSize="small" />
                </IconButton>
            </Tooltip>
            <Dialog onClose={() => setOpen(false)} open={open}>
                <DialogTitle>{t('app.settings')}</DialogTitle>
                <DialogContent>
                    <Box sx={{ 
                        display: 'flex', 
                        flexDirection: 'column', 
                        gap: 3,
                        maxWidth: 400
                    }}>
                        <Divider><Typography variant="caption">{t('config.frontend')}</Typography></Divider>
                        <FormControl fullWidth size="small">
                            <InputLabel id="palette-select-label" sx={{ fontSize: 13 }}>{t('config.colorTheme')}</InputLabel>
                            <Select
                                labelId="palette-select-label"
                                value={paletteKey}
                                label={t('config.colorTheme')}
                                onChange={(e) => setPaletteKey(e.target.value)}
                                sx={{ fontSize: 13 }}
                                renderValue={(key) => {
                                    const p = palettes[key];
                                    return (
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <Box sx={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: p.primary.main, flexShrink: 0 }} />
                                            <Box sx={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: p.custom.main, flexShrink: 0 }} />
                                            <Typography sx={{ fontSize: 13 }}>{p.name}</Typography>
                                        </Box>
                                    );
                                }}
                            >
                                {paletteKeys.map(key => {
                                    const p = palettes[key];
                                    return (
                                        <MenuItem key={key} value={key} sx={{ py: 0.5 }}>
                                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mr: 1.5 }}>
                                                <Box sx={{ width: 14, height: 14, borderRadius: '50%', backgroundColor: p.primary.main, border: '1px solid rgba(0,0,0,0.1)' }} />
                                                <Box sx={{ width: 14, height: 14, borderRadius: '50%', backgroundColor: p.custom.main, border: '1px solid rgba(0,0,0,0.1)' }} />
                                            </Box>
                                            <ListItemText primary={p.name} slotProps={{ primary: { sx: { fontSize: 13 } } }} />
                                        </MenuItem>
                                    );
                                })}
                            </Select>
                        </FormControl>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Box sx={{ flex: 1 }}>
                                <TextField
                                    label={t('config.defaultChartWidth')}
                                    type="number"
                                    variant="outlined"
                                    value={defaultChartWidth}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        setDefaultChartWidth(value);
                                    }}
                                    fullWidth
                                    slotProps={{
                                        input: {
                                            inputProps: {
                                                min: 100,
                                                max: 1000
                                            }
                                        }
                                    }}
                                    error={defaultChartWidth < 100 || defaultChartWidth > 1000}
                                    helperText={defaultChartWidth < 100 || defaultChartWidth > 1000 ? 
                                        t('config.chartSizeRangeError') : ""}
                                />
                            </Box>
                            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                                <ClearIcon fontSize="small" />
                            </Typography>
                            <Box sx={{ flex: 1 }}>
                                <TextField
                                    label={t('config.defaultChartHeight')}
                                    type="number"
                                    variant="outlined"
                                    value={defaultChartHeight}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        setDefaultChartHeight(value);
                                    }}
                                    fullWidth
                                    slotProps={{
                                        input: {
                                            inputProps: {
                                                min: 100,
                                                max: 1000
                                            }
                                        }
                                    }}
                                    error={defaultChartHeight < 100 || defaultChartHeight > 1000}
                                    helperText={defaultChartHeight < 100 || defaultChartHeight > 1000 ? 
                                        t('config.chartSizeRangeError') : ""}
                                />
                            </Box>
                        </Box>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Box sx={{ flex: 1 }}>
                                <TextField
                                    label={t('config.localRowLimit')}
                                    type="number"
                                    variant="outlined"
                                    value={frontendRowLimit}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        setFrontendRowLimit(value);
                                    }}
                                    fullWidth
                                    slotProps={{
                                        input: {
                                            inputProps: {
                                                min: 100,
                                                max: rowLimitMax
                                            }
                                        }
                                    }}
                                    error={frontendRowLimit < 100 || frontendRowLimit > rowLimitMax}
                                    helperText={frontendRowLimit < 100 || frontendRowLimit > rowLimitMax ? 
                                        t('config.localRowLimitRangeError') : ""}
                                />
                                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                                    {t('config.localRowLimitHint')}
                                </Typography>
                            </Box>
                        </Box>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Box sx={{ flex: 1 }}>
                                <TextField
                                    label={t('config.maxStretchFactor')}
                                    type="number"
                                    variant="outlined"
                                    value={maxStretchFactor}
                                    onChange={(e) => {
                                        const value = parseFloat(e.target.value);
                                        setMaxStretchFactor(value);
                                    }}
                                    fullWidth
                                    slotProps={{
                                        input: {
                                            inputProps: {
                                                min: 1,
                                                max: 5,
                                                step: 0.1
                                            }
                                        }
                                    }}
                                    error={isNaN(maxStretchFactor) || maxStretchFactor < 1 || maxStretchFactor > 5}
                                    helperText={isNaN(maxStretchFactor) || maxStretchFactor < 1 || maxStretchFactor > 5 ? 
                                        t('config.maxStretchFactorRangeError') : ""}
                                />
                                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                                    {t('config.maxStretchFactorHint')}
                                </Typography>
                            </Box>
                        </Box>
                        <Divider><Typography variant="caption">{t('config.backend')}</Typography></Divider>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Box sx={{ flex: 1 }}>
                                <TextField
                                    label={t('config.formulateTimeout')}
                                    type="number"
                                    variant="outlined"
                                    value={formulateTimeoutSeconds}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        setFormulateTimeoutSeconds(value);
                                    }}
                                    inputProps={{
                                        min: 0,
                                        max: 3600,
                                    }}
                                    error={formulateTimeoutSeconds <= 0 || formulateTimeoutSeconds > 3600}
                                    helperText={formulateTimeoutSeconds <= 0 || formulateTimeoutSeconds > 3600 ? 
                                        t('config.formulateTimeoutRangeError') : ""}
                                    fullWidth
                                />
                                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                                    {t('config.formulateTimeoutHint')}
                                </Typography>
                            </Box>
                        </Box>
                    </Box>
                </DialogContent>
                <DialogActions sx={{'.MuiButton-root': {textTransform: 'none'}}}>
                    <Button sx={{marginRight: 'auto'}} onClick={() => {
                        setFormulateTimeoutSeconds(180);
                        setDefaultChartWidth(300);
                        setDefaultChartHeight(300);
                        setMaxStretchFactor(2.0);
                        setFrontendRowLimit(rowLimitDefault);
                        setPaletteKey(defaultPaletteKey);
                    }}>{t('session.resetToDefault')}</Button>
                    <Button onClick={() => setOpen(false)}>{t('app.cancel')}</Button>
                    <Button 
                        variant={hasChanges ? "contained" : "text"}
                        disabled={!hasChanges || isNaN(formulateTimeoutSeconds) || formulateTimeoutSeconds <= 0 || formulateTimeoutSeconds > 3600
                            || isNaN(defaultChartWidth) || defaultChartWidth <= 0 || defaultChartWidth > 1000
                            || isNaN(defaultChartHeight) || defaultChartHeight <= 0 || defaultChartHeight > 1000
                            || isNaN(maxStretchFactor) || maxStretchFactor < 1 || maxStretchFactor > 5
                            || isNaN(frontendRowLimit) || frontendRowLimit < 100 || frontendRowLimit > rowLimitMax}
                        onClick={() => {
                            dispatch(dfActions.setConfig({formulateTimeoutSeconds, defaultChartWidth, defaultChartHeight, maxStretchFactor, frontendRowLimit, paletteKey, miniMode: config.miniMode ?? false}));
                            setOpen(false);
                        }}
                    >
                        {t('app.apply')}
                    </Button>
                </DialogActions>
            </Dialog>
        </>
    );  
}

const ErrorBoundaryFallback: React.FC = () => {
    const { t } = useTranslation();
    return (
        <Box sx={{ width: "100%", height: "100%", display: "flex" }}>
            <Typography color="gray" sx={{ margin: "150px auto" }}>
                {t('workspace.errorOccurred')} <Link href="/app">{t('workspace.refreshSession')}</Link>{'. '}{t('workspace.errorPersistHint')}
            </Typography>
        </Box>
    );
};

const AUTH_ERROR_MESSAGES: Record<string, string> = {
    access_denied: 'auth.ssoErrorAccessDenied',
    invalid_state: 'auth.ssoErrorInvalidState',
    invalid_client: 'auth.ssoErrorInvalidClient',
    token_exchange_failed: 'auth.ssoErrorTokenExchange',
    missing_token_endpoint: 'auth.ssoErrorMissingEndpoint',
};

const AppShell: FC = () => {
    const dispatch = useDispatch<AppDispatch>();
    const { t } = useTranslation();
    const location = useLocation();
    const [searchParams, setSearchParams] = useSearchParams();
    const viewMode = useSelector((state: DataFormulatorState) => state.viewMode);
    const tables = useSelector((state: DataFormulatorState) => state.tables);
    const activeWorkspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    const serverConfig = useSelector((state: DataFormulatorState) => state.serverConfig);

    useEffect(() => {        const authError = searchParams.get('auth_error');
        if (!authError) return;
        const i18nKey = AUTH_ERROR_MESSAGES[authError] || 'auth.ssoErrorGeneric';
        dispatch(dfActions.addMessages({
            type: 'error',
            component: 'auth',
            timestamp: Date.now(),
            value: t(i18nKey, { defaultValue: 'SSO login failed. Please contact your administrator.' }),
        }));
        searchParams.delete('auth_error');
        setSearchParams(searchParams, { replace: true });
    }, []);

    // Auto-persist session state to the active workspace (debounced)
    useAutoSave();
    // Auto-name workspace after first table + model are available
    useWorkspaceAutoName();
    const generatedReports = useSelector((state: DataFormulatorState) => state.generatedReports);

    const isAboutPage = location.pathname === '/about';
    const isAppPage = !isAboutPage;

    // The canvas (threads, encoding shelf, viz cards) genuinely needs room, so
    // the app shell floors content at 1000px and scrolls horizontally below
    // that. The landing page (app route with no tables yet) has none of that —
    // its hero, chips, connected-sources row and demo grid all reflow — so we
    // relax its floor to 640px, a comfortable width where everything still
    // wraps cleanly before a horizontal scrollbar appears.
    const isLandingView = isAppPage && tables.length === 0;
    const shellMinWidth = isLandingView ? '640px' : '1000px';

    return (
        <Box sx={{
            position: 'absolute',
            backgroundColor: 'rgba(255, 255, 255, 0.3)',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            overflow: 'auto',
            '& > *': {
                minWidth: shellMinWidth,
                minHeight: '600px'
            },
        }}>
            <Box sx={{
                display: 'flex',
                flexDirection: 'column',
                height: '100%',
                width: '100%',
                overflow: 'hidden'
            }}>
                <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden', '& > div': { height: '100%' } }}>
                    <Outlet />
                </Box>
                <MessageSnackbar />
                <ChartRenderService />
            </Box>
        </Box>
    );
}

export const AppFC: FC<AppFCProps> = function AppFC(appProps) {

    const dispatch = useDispatch<AppDispatch>();
    const { t, i18n } = useTranslation();
    const rawPaletteKey = useSelector((state: DataFormulatorState) => state.config.paletteKey);
    const activePaletteKey = (rawPaletteKey && palettes[rawPaletteKey]) ? rawPaletteKey : defaultPaletteKey;

    const [configLoaded, setConfigLoaded] = useState(false);

    useEffect(() => {
        syncVegaLocale();
        const onLangChanged = () => syncVegaLocale();
        i18n.on('languageChanged', onLangChanged);
        return () => { i18n.off('languageChanged', onLangChanged); };
    }, [i18n]);

    useEffect(() => {
        apiRequest(getUrls().APP_CONFIG)
            .then(({ data }) => {
                dispatch(dfActions.setServerConfig(data));
                setConfigLoaded(true);
            });
    }, []);

    // Validate persisted workspace still exists on the backend
    const activeWorkspace = useSelector((state: DataFormulatorState) => state.activeWorkspace);
    const tables = useSelector((state: DataFormulatorState) => state.tables);
    
    // Debug: log persisted state on startup
    useEffect(() => {
        if (configLoaded) {
            console.log('[DEBUG] activeWorkspace:', activeWorkspace);
            console.log('[DEBUG] tables:', tables.length, tables.map(t => ({ id: t.id, virtual: t.virtual, rowLen: t.rows?.length })));
            
            // Recover orphaned state: tables exist but activeWorkspace was lost
            if (!activeWorkspace && tables.length > 0) {
                const recoveredId = `recovered_${Date.now()}`;
                dispatch(dfActions.setActiveWorkspace({ id: recoveredId, displayName: t('workspace.recoveredSession') }));
            }
        }
    }, [configLoaded]);

    // Unified auth initialisation — driven by /api/auth/info and server IDENTITY
    const [authChecked, setAuthChecked] = useState(false);
    const [migrationBrowserId, setMigrationBrowserId] = useState<string | null>(null);
    const serverConfig = useSelector((state: DataFormulatorState) => state.serverConfig);

    useEffect(() => {
        if (!configLoaded) return;

        (async () => {
            const prevType = localStorage.getItem('df_identity_type');
            const prevBrowserId = localStorage.getItem('df_browser_id');

            let resolvedIdentity: { type: 'user' | 'browser' | 'local'; id: string; displayName?: string } | null = null;

            // Check if the server assigned a fixed identity (e.g. localhost mode)
            const serverIdentity = serverConfig?.IDENTITY;
            if (serverIdentity?.type === 'local' && serverIdentity?.id) {
                resolvedIdentity = { type: 'local', id: serverIdentity.id };
            }

            if (!resolvedIdentity) {
                try {
                    const { getAuthInfo, getOidcUser } = await import('./oidcConfig');
                    const info: AuthInfo | null = await getAuthInfo();

                    if (info?.action === 'backend') {
                        // Backend OIDC — identity from server session
                        try {
                            const { data: status } = await apiRequest(info.status_url || '/api/auth/oidc/status');
                            if (status.authenticated && status.user) {
                                resolvedIdentity = {
                                    type: 'user',
                                    id: String(status.user.sub || status.user.id || 'session_user'),
                                    displayName: typeof status.user.name === 'string' ? status.user.name : undefined,
                                };
                            }
                        } catch {
                            // fall through to browser identity
                        }
                    } else if (info?.action === 'frontend') {
                        // OIDC PKCE — check for an existing session
                        const user = await getOidcUser();
                        if (user && !user.expired) {
                            resolvedIdentity = {
                                type: 'user',
                                id: String(user.profile.sub),
                                displayName: typeof user.profile.name === 'string' ? user.profile.name : undefined,
                            };
                        }
                    } else if (info?.action === 'transparent') {
                        // Azure App Service EasyAuth — headers injected by Azure
                        try {
                            const resp = await fetch('/.auth/me');
                            const result = await resp.json();
                            if (Array.isArray(result) && result.length > 0) {
                                const authData = result[0];
                                const name = authData['user_claims']?.find((item: any) => item.typ === 'name')?.val || '';
                                const userId = authData['user_id'];
                                if (userId) {
                                    resolvedIdentity = { type: 'user', id: userId, displayName: name };
                                }
                            }
                        } catch {
                            // fall through to browser identity
                        }
                    }
                    // 'redirect' and 'none' → browser identity (resolvedIdentity stays null)
                } catch {
                    // fall through to browser identity
                }
            }

            if (!resolvedIdentity) {
                resolvedIdentity = { type: 'browser', id: getBrowserId() };
            }

            dispatch(dfActions.setIdentity(resolvedIdentity));

            try {
                const { data: refreshedConfig } = await apiRequest(getUrls().APP_CONFIG);
                dispatch(dfActions.setServerConfig(refreshedConfig));
            } catch {
                // App config was already loaded; connector status refresh is best-effort.
            }

            // Persist current identity type for next page load
            localStorage.setItem('df_identity_type', resolvedIdentity.type);
            if (resolvedIdentity.type === 'browser') {
                localStorage.setItem('df_browser_id', resolvedIdentity.id);
            }

            // Detect anonymous → authenticated transition
            if (
                prevType === 'browser' &&
                resolvedIdentity.type === 'user' &&
                prevBrowserId
            ) {
                setMigrationBrowserId(prevBrowserId);
            }

            setAuthChecked(true);
        })();
    }, [configLoaded]);

    useEffect(() => {
        document.title = toolName;
        // Load all server-configured models instantly (no connectivity check).
        // Users can verify connectivity via the "Test" button in the model dialog,
        // or errors will surface naturally when a model is first used.
        dispatch(fetchGlobalModelList());
    }, []);

    let theme = createTheme({
        typography: {
            fontFamily: [
                "Arial",
                "Roboto",
                "Helvetica Neue",
                "sans-serif"
            ].join(",")
        },
        // Default Material UI palette
        // Active palette from user config — selectable via Settings dialog
        // Available: material, fluent, vivid, jewel, electric, tealCoral, copilot
        palette: (() => {
            const p = palettes[activePaletteKey];
            const bg = (entry: { main: string; bgcolor?: string }) => entry.bgcolor ?? alpha(entry.main, bgAlpha);
            const tc = (entry: { main: string; textColor?: string }) => entry.textColor ?? entry.main;
            return {
                primary:   { main: p.primary.main,   bgcolor: bg(p.primary),   textColor: tc(p.primary)   },
                secondary: { main: p.secondary.main, bgcolor: bg(p.secondary), textColor: tc(p.secondary) },
                derived:   { main: p.derived.main,   bgcolor: bg(p.derived),   textColor: tc(p.derived)   },
                custom:    { main: p.custom.main,    bgcolor: bg(p.custom),    textColor: tc(p.custom)    },
                warning:   { main: p.warning.main },
            };
        })(),
        components: {
            MuiButton: {
                styleOverrides: {
                    text: ({ ownerState, theme: t }) => {
                        const c = ownerState.color;
                        if (c && c !== 'inherit' && c !== 'error' && c !== 'info' && c !== 'success' && c in t.palette) {
                            const p = (t.palette as any)[c];
                            if (p?.textColor) return { color: p.textColor };
                        }
                        return {};
                    },
                    outlined: ({ ownerState, theme: t }) => {
                        const c = ownerState.color;
                        if (c && c !== 'inherit' && c !== 'error' && c !== 'info' && c !== 'success' && c in t.palette) {
                            const p = (t.palette as any)[c];
                            if (p?.textColor) return { color: p.textColor, borderColor: alpha(p.textColor, 0.5) };
                        }
                        return {};
                    },
                },
            },
            MuiIconButton: {
                styleOverrides: {
                    root: ({ ownerState, theme: t }) => {
                        const c = ownerState.color;
                        if (c && c !== 'inherit' && c !== 'default' && c !== 'error' && c !== 'info' && c !== 'success' && c in t.palette) {
                            const p = (t.palette as any)[c];
                            if (p?.textColor) return { color: p.textColor };
                        }
                        return {};
                    },
                },
            },
            MuiLink: {
                styleOverrides: {
                    root: ({ ownerState, theme: t }) => {
                        const c = ownerState.color as string | undefined;
                        if (c && c !== 'inherit' && c in t.palette) {
                            const p = (t.palette as any)[c];
                            if (p?.textColor) return { color: p.textColor };
                        }
                        return {};
                    },
                },
            },
        },
        transitions: {
            duration: {
                shortest: 100,
                shorter: 100,
                short: 100,
                standard: 100,
                complex: 150,
                enteringScreen: 100,
                leavingScreen: 100,
            },
        },
    });

    const router = useMemo(() => createBrowserRouter([
        {
            path: "/auth/callback",
            element: <OidcCallback />,
        },
        {
            path: "/",
            element: <AppShell />,
            errorElement: <ErrorBoundaryFallback />,
            children: [
                {
                    index: true,
                    element: <DataFormulatorFC />,
                },
                {
                    path: "app",
                    element: <DataFormulatorFC />,
                },
                {
                    path: "about",
                    element: <About />,
                },
                {
                    path: "*",
                    element: <DataFormulatorFC />,
                },
            ],
        }
    ]), []);

    return (
        <ThemeProvider theme={theme}>
            {configLoaded && authChecked ? (
                <RouterProvider router={router} />
            ) : (
                <AnvilLoader label="loading data formulator..." />
            )}
            {migrationBrowserId && (
                <IdentityMigrationDialog
                    oldBrowserId={migrationBrowserId}
                    onDone={() => setMigrationBrowserId(null)}
                />
            )}
        </ThemeProvider>
    );
}

function stringAvatar(name: string) {
    let displayName = ""
    try {
        let nameSplit = name.split(' ')
        displayName = `${nameSplit[0][0]}${nameSplit.length > 1 ? nameSplit[nameSplit.length - 1][0] : ''}`
    } catch {
        displayName = name ? name[0] : "?";
    }
    return {
        sx: {
            bgcolor: "cornflowerblue",
            width: 36,
            height: 36,
            margin: "auto",
            fontSize: "1rem"
        },
        children: displayName,
    };
}
