import AddIcon from '@mui/icons-material/Add';
import ArchiveIcon from '@mui/icons-material/Archive';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import EditIcon from '@mui/icons-material/Edit';
import UnarchiveIcon from '@mui/icons-material/Unarchive';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import SearchIcon from '@mui/icons-material/Search';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink } from 'react-router-dom';
import { linksApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminLink } from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { CopyButton } from '@/components/CopyButton';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { formatDateTime } from '@/lib/format';
import { linkUrl, slugIsGuessable } from '@/lib/slug';
import { useCan } from '../AdminContext';
import { stateColor } from './utils';

type ActiveFilter = 'all' | 'active' | 'inactive';
type Pending = { kind: 'archive' | 'unarchive' | 'clone'; link: AdminLink } | null;

export default function LinksListPage() {
  const { t } = useTranslation();
  const can = useCan();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging();
  const [search, setSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>('all');
  const [archived, setArchived] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const q = useDebounced(search.trim());
  const canWrite = can('links.write');

  const params = {
    q: q || undefined,
    active: activeFilter === 'all' ? undefined : activeFilter === 'active',
    archived,
    limit: paging.pageSize,
    offset: paging.offset,
  };
  const list = useQuery({
    queryKey: qk.admin.links.list(params),
    queryFn: () => linksApi.list(params),
    placeholderData: keepPreviousData,
  });

  const act = useMutation({
    mutationFn: async ({ kind, link }: NonNullable<Pending>) => {
      if (kind === 'archive') return linksApi.archive(link.id);
      if (kind === 'unarchive') return linksApi.unarchive(link.id);
      return linksApi.clone(link.id);
    },
    onSuccess: async (_d, { kind }) => {
      enqueueSnackbar(
        t(
          kind === 'archive'
            ? 'links.archived'
            : kind === 'unarchive'
              ? 'links.unarchived'
              : 'links.cloned',
        ),
        { variant: 'success' },
      );
      setPending(null);
      await qc.invalidateQueries({ queryKey: qk.admin.links.all() });
    },
  });

  const items = list.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={t('links.title')}
        actions={
          canWrite && (
            <Button
              component={RouterLink}
              to="/admin/links/new"
              variant="contained"
              startIcon={<AddIcon />}
            >
              {t('links.new')}
            </Button>
          )
        }
      />
      <Paper variant="outlined">
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          spacing={2}
          sx={{ p: 2 }}
          alignItems={{ md: 'center' }}
        >
          <TextField
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              paging.reset();
            }}
            label={t('common.search')}
            placeholder={t('links.searchPlaceholder')}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              },
              htmlInput: { maxLength: 100 },
            }}
            sx={{ maxWidth: { md: 360 } }}
          />
          <TextField
            select
            label={t('links.filterActive')}
            value={activeFilter}
            onChange={(e) => {
              setActiveFilter(e.target.value as ActiveFilter);
              paging.reset();
            }}
            sx={{ minWidth: 180, maxWidth: { md: 220 } }}
          >
            <MenuItem value="all">{t('links.filterAll')}</MenuItem>
            <MenuItem value="active">{t('links.filterActiveOnly')}</MenuItem>
            <MenuItem value="inactive">{t('links.filterInactiveOnly')}</MenuItem>
          </TextField>
          <FormControlLabel
            control={
              <Switch
                checked={archived}
                onChange={(_, v) => {
                  setArchived(v);
                  paging.reset();
                }}
              />
            }
            label={t('links.showArchived')}
          />
        </Stack>
        <TableContainer>
          <Table size="small" aria-label={t('links.title')}>
            <TableHead>
              <TableRow>
                <TableCell>{t('links.colName')}</TableCell>
                <TableCell>{t('links.colSlug')}</TableCell>
                <TableCell>{t('links.colState')}</TableCell>
                <TableCell align="right">{t('links.colLogins')}</TableCell>
                <TableCell>{t('links.colPeriod')}</TableCell>
                <TableCell align="right">{t('common.actions')}</TableCell>
              </TableRow>
            </TableHead>
            {list.isPending ? (
              <TableSkeleton cols={6} />
            ) : (
              <TableBody>
                {items.map((l) => (
                  <TableRow key={l.id} hover>
                    <TableCell>
                      <Button
                        component={RouterLink}
                        to={`/admin/links/${l.id}`}
                        sx={{ textAlign: 'left', justifyContent: 'flex-start', fontWeight: 600 }}
                      >
                        {l.name}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" alignItems="center" spacing={0.5}>
                        <code>{l.slug}</code>
                        {slugIsGuessable(l.slug) && (
                          <Tooltip title={t('links.slugLowEntropyBadge')}>
                            <WarningAmberIcon
                              color="warning"
                              fontSize="small"
                              aria-label={t('links.slugLowEntropyBadge')}
                            />
                          </Tooltip>
                        )}
                        <CopyButton
                          value={linkUrl(l.slug)}
                          label={t('links.copyUrl')}
                          doneMessage={t('links.urlCopied')}
                        />
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={stateColor(l.state)}
                        label={t(`links.state_${l.state}`)}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {l.max_logins ? `${l.login_count} / ${l.max_logins}` : l.login_count}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      {l.starts_at || l.ends_at
                        ? `${formatDateTime(l.starts_at)} – ${formatDateTime(l.ends_at)}`
                        : '—'}
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      <Tooltip title={t('links.open')}>
                        <IconButton
                          component={RouterLink}
                          to={`/admin/links/${l.id}`}
                          size="small"
                          aria-label={`${t('links.open')}: ${l.name}`}
                        >
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      {canWrite && (
                        <>
                          <Tooltip title={t('links.clone')}>
                            <IconButton
                              size="small"
                              aria-label={`${t('links.clone')}: ${l.name}`}
                              onClick={() => setPending({ kind: 'clone', link: l })}
                            >
                              <ContentCopyIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          {l.archived_at ? (
                            <Tooltip title={t('links.unarchive')}>
                              <IconButton
                                size="small"
                                aria-label={`${t('links.unarchive')}: ${l.name}`}
                                onClick={() => act.mutate({ kind: 'unarchive', link: l })}
                              >
                                <UnarchiveIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          ) : (
                            <Tooltip title={t('links.archive')}>
                              <IconButton
                                size="small"
                                aria-label={`${t('links.archive')}: ${l.name}`}
                                onClick={() => setPending({ kind: 'archive', link: l })}
                              >
                                <ArchiveIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            )}
          </Table>
        </TableContainer>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && (
          <EmptyState
            title={
              q || activeFilter !== 'all' || archived ? t('links.emptyFiltered') : t('links.empty')
            }
            text={q ? undefined : t('links.emptyText')}
            action={
              canWrite && !q ? (
                <Button component={RouterLink} to="/admin/links/new" variant="contained">
                  {t('links.new')}
                </Button>
              ) : undefined
            }
          />
        )}
        <TablePagination
          component="div"
          count={list.data?.total ?? 0}
          page={paging.page}
          rowsPerPage={paging.pageSize}
          onPageChange={(_, p) => paging.setPage(p)}
          onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
          rowsPerPageOptions={[10, 25, 50]}
          labelRowsPerPage={t('common.rowsPerPage')}
          labelDisplayedRows={({ from, to, count }) => `${from}–${to} ${t('common.of')} ${count}`}
        />
      </Paper>

      <ConfirmDialog
        open={!!pending}
        destructive={pending?.kind === 'archive'}
        loading={act.isPending}
        title={pending ? t(`links.${pending.kind}`) : ''}
        message={
          pending
            ? t(pending.kind === 'clone' ? 'links.cloneConfirm' : 'links.archiveConfirm', {
                name: pending.link.name,
              })
            : undefined
        }
        onClose={() => setPending(null)}
        onConfirm={() => pending && act.mutate(pending)}
      />
    </>
  );
}
