import PersonRemoveIcon from '@mui/icons-material/PersonRemove';
import Autocomplete from '@mui/material/Autocomplete';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { clientsApi, linksApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminClient } from '@/api/types';
import { EmptyState, ErrorState } from '@/components/States';
import { useDebounced } from '@/hooks/useDebounced';

/** Manage the explicit client list used by client_scope = "selected". */
export function LinkClientsPanel({ linkId, canWrite }: { linkId: number; canWrite: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const params = { limit: 100, offset: 0 };
  const list = useQuery({
    queryKey: qk.admin.links.clients(linkId, params),
    queryFn: () => linksApi.clients(linkId, params),
  });
  const [input, setInput] = useState('');
  const q = useDebounced(input.trim());
  const search = useQuery({
    queryKey: qk.admin.clients.list({ q, limit: 10, offset: 0, picker: true }),
    queryFn: () => clientsApi.list({ q, limit: 10, offset: 0 }),
    enabled: canWrite && q.length >= 2,
  });

  const change = useMutation({
    mutationFn: (body: { add: number[]; remove: number[] }) => linksApi.changeClients(linkId, body),
    onSuccess: async () => {
      enqueueSnackbar(t('links.clientsChanged'), { variant: 'success' });
      await qc.invalidateQueries({ queryKey: ['admin', 'links', 'clients', linkId] });
    },
  });

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Typography variant="h3" component="h2">
          {t('links.clientsTitle')} {list.data ? `(${list.data.total})` : ''}
        </Typography>
        {canWrite && (
          <Autocomplete<AdminClient>
            options={search.data?.items ?? []}
            loading={search.isFetching}
            filterOptions={(x) => x}
            getOptionLabel={(o) => o.name ?? o.sd_id}
            inputValue={input}
            value={null}
            onInputChange={(_, v, reason) => reason !== 'reset' && setInput(v)}
            onChange={(_, v) => {
              if (v) {
                change.mutate({ add: [v.id], remove: [] });
                setInput('');
              }
            }}
            noOptionsText={q.length < 2 ? t('links.clientsSearch') : t('clients.empty')}
            renderInput={(params) => (
              <TextField
                {...params}
                label={t('links.clientsAdd')}
                placeholder={t('links.clientsSearch')}
              />
            )}
          />
        )}
        {list.isPending ? (
          <Skeleton variant="rounded" height={80} />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title={t('links.clientsEmpty')} />
        ) : (
          <List dense disablePadding sx={{ maxHeight: 320, overflow: 'auto' }}>
            {list.data.items.map((c) => (
              <ListItem
                key={c.id}
                divider
                secondaryAction={
                  canWrite && (
                    <IconButton
                      edge="end"
                      aria-label={`${t('links.clientsRemove')}: ${c.name ?? c.sd_id}`}
                      onClick={() => change.mutate({ add: [], remove: [c.id] })}
                      disabled={change.isPending}
                    >
                      <PersonRemoveIcon fontSize="small" />
                    </IconButton>
                  )
                }
              >
                <ListItemText primary={c.name ?? c.sd_id} secondary={c.sd_id} />
              </ListItem>
            ))}
          </List>
        )}
      </Stack>
    </Paper>
  );
}
